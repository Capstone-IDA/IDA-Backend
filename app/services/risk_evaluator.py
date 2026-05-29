"""
app/services/risk_evaluator.py

RiskEvaluator
객체 위험도 평가 + CAN 데이터 기반 운전 이벤트 생성
"""

import logging
from datetime import datetime
from typing import Optional

from app.core.object_classes import RISK_TARGET_CATEGORIES, get_category
from app.models.schemas import CANSnapshot, DrivingEvent, ScoringConfig, TrackedObject

logger = logging.getLogger(__name__)

# 실내 주차장 기준 폴백 임계값 (DB 설정 미로드 시 사용)
# 실측 CAN 데이터(test_scenario_1~4) 기반으로 도출
_DEFAULT_ACCEL_THRESHOLD = 3.0    # m/s², 실측 급출발 최솟값
_DEFAULT_BRAKE_THRESHOLD = 3.0    # m/s², accYAve 기준 급제동
_DEFAULT_BRAKE_INTENSITY = 0.25   # brake_pressure/80 기준, 압력 급증 판정
_DEFAULT_SPEED_LIMIT = 20.0       # km/h, 실내 주차장 제한


class RiskEvaluator:
    """위험도 평가 및 운전 이벤트 생성"""

    def __init__(self):
        self.danger_threshold: float = 0.15   # depth 기준 danger
        self.warning_threshold: float = 0.35  # depth 기준 warning
        self.config: Optional[ScoringConfig] = None

    def reload_config(self, config: ScoringConfig) -> None:
        """설정 반영"""
        self.config = config
        self.danger_threshold = config.proximity_distance

    def assess(self, track_id: int, class_id: int, depth: float,
                is_moving: bool = True) -> str:
        if get_category(class_id) not in RISK_TARGET_CATEGORIES:
            return "safe"

        # AI depth: 1=가까움, 0=멀다 → 반전
        inverted = 1.0 - depth

        if inverted <= self.danger_threshold:
            return "danger"
        if inverted <= self.warning_threshold:
            return "danger" if is_moving else "warning"
        return "safe"

    def evaluate_driving_event(
        self,
        session_id: str,
        can_data: CANSnapshot,
        risk_level: str,
        track_id: Optional[int] = None,
    ) -> Optional[DrivingEvent]:
        """CAN 데이터 + 위험등급 기반 운전 이벤트 판정 (DS002).

        판정 우선순위:
          1. brake_intensity >= brake_intensity_threshold -> sudden_brake (압력 기반)
          2. acceleration <= -brake_threshold            -> sudden_brake (감속도 기반)
          3. acceleration >= accel_threshold             -> sudden_start
          4. speed_kmh >= speed_limit                   -> overspeeding
        """
        cfg = self.config
        accel_threshold = cfg.accel_threshold if cfg else _DEFAULT_ACCEL_THRESHOLD
        brake_threshold = cfg.brake_threshold if cfg else _DEFAULT_BRAKE_THRESHOLD
        speed_limit = cfg.speed_limit if cfg else _DEFAULT_SPEED_LIMIT

        # brake_intensity_threshold: DB에 컬럼 없으므로 상수 사용
        # brake_pressure 20/80 = 0.25 (실내 주차 기준 유효 제동 최솟값)
        brake_intensity_threshold = _DEFAULT_BRAKE_INTENSITY

        is_proximate = risk_level == "danger"
        event_type: Optional[str] = None
        severity = "normal"

        # 1순위: brake_pressure 기반 급제동 (가속도보다 신뢰도 높음)
        if can_data.brake_intensity >= brake_intensity_threshold:
            event_type = "sudden_brake"
            severity = "critical" if is_proximate else "warning"

        # 2순위: 감속도 기반 급제동
        elif can_data.acceleration <= -brake_threshold:
            event_type = "sudden_brake"
            severity = "critical" if is_proximate else "warning"

        # 3순위: 급출발 (브레이크 미작동 상태에서 가속도 초과)
        elif can_data.acceleration >= accel_threshold:
            event_type = "sudden_start"
            severity = "critical" if is_proximate else "warning"

        # 4순위: 과속 (상기 이벤트 없을 때만 체크)
        elif can_data.speed_kmh >= speed_limit:
            event_type = "overspeeding"
            severity = "critical" if is_proximate else "warning"

        if event_type is None:
            return None

        return DrivingEvent(
            session_id=session_id,
            timestamp=can_data.timestamp,
            event_type=event_type,
            severity=severity,
            speed=can_data.speed_kmh,
            acceleration=can_data.acceleration,
            is_proximate=is_proximate,
            deduction=0,  # DrivingScorer에서 계산
            track_id=track_id,
        )