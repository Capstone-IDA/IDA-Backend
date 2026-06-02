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


# depth는 0~1 정규화이며 값이 클수록 가깝다
# 벽처럼 화면을 가르는 면 구조물은 단일 depth 신뢰도가 낮아 danger로 격상하지 않는다
_BOUNDARY_CLASS_IDS = {1}         # Wall
_WARNING_MARGIN = 0.20            # danger와 warning 임계값 간격
_MIN_VALID_DANGER_THRESHOLD = 0.5  # 이 값 미만의 근접 임계값은 stale 설정으로 간주


class RiskEvaluator:
    """위험도 평가 및 운전 이벤트 생성"""

    def __init__(self):
        self.danger_threshold: float = 0.85    # depth 기준 danger, 값이 클수록 가까움
        self.warning_threshold: float = 0.65   # depth 기준 warning
        self.config: Optional[ScoringConfig] = None

    def reload_config(self, config: ScoringConfig) -> None:
        """설정 반영. proximity_distance는 high=close 기준 danger 임계값."""
        self.config = config
        proximity = config.proximity_distance
        if proximity < _MIN_VALID_DANGER_THRESHOLD:
            logger.warning(
                "proximity_distance %.3f가 high=close 기준에 맞지 않아 기본값(%.2f) 유지",
                proximity, self.danger_threshold,
            )
            return
        self.danger_threshold = proximity
        self.warning_threshold = max(0.0, proximity - _WARNING_MARGIN)

    def assess(self, track_id: int, class_id: int, depth: float,
               is_moving: bool = True) -> str:
        """객체별 위험도 평가. depth가 클수록 가깝고 위험.
        is_moving은 위험도를 올리는 가중 요소. INFO/UNDEFINED는 평가 제외."""
        if get_category(class_id) not in RISK_TARGET_CATEGORIES:
            return "safe"

        can_escalate = class_id not in _BOUNDARY_CLASS_IDS

        if depth >= self.danger_threshold and can_escalate:
            return "danger"
        if depth >= self.warning_threshold:
            return "danger" if (is_moving and can_escalate) else "warning"
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