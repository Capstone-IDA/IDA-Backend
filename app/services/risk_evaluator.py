"""
RiskEvaluator
객체 위험도 평가 + CAN 데이터 기반 운전 이벤트 생성
"""

import logging
from datetime import datetime
from typing import Optional

from app.core.object_classes import RISK_TARGET_CATEGORIES, get_category
from app.models.schemas import CANSnapshot, DrivingEvent, ScoringConfig, TrackedObject

logger = logging.getLogger(__name__)


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
        """객체별 위험도 평가. 거리로 판정하며 is_moving은 위험도를 올리는 가중 요소.
        INFO/UNDEFINED 카테고리는 평가 대상이 아님."""
        # 위험 평가 대상이 아닌 카테고리 (주차선, 표지 아이콘 등)
        if get_category(class_id) not in RISK_TARGET_CATEGORIES:
            return "safe"

        # 거리 기반 위험도 판정
        if depth <= self.danger_threshold:
            # 근접: 정지/이동 무관하게 danger (코앞 객체는 충돌 위험)
            return "danger"
        if depth <= self.warning_threshold:
            # 중거리: 정지는 warning, 이동 객체는 danger로 격상
            return "danger" if is_moving else "warning"
        # 원거리: 정지/이동 무관 safe
        return "safe"

    def evaluate_driving_event(
        self,
        session_id: str,
        can_data: CANSnapshot,
        risk_level: str,
        track_id: Optional[int] = None,
    ) -> Optional[DrivingEvent]:
        """CAN 데이터 + 위험등급 기반 운전 이벤트 판정 (DS002)"""
        cfg = self.config
        accel_threshold = cfg.accel_threshold if cfg else 2.0
        brake_threshold = cfg.brake_threshold if cfg else 2.0
        speed_limit = cfg.speed_limit if cfg else 30.0

        is_proximate = risk_level == "danger"
        event_type: Optional[str] = None
        severity = "normal"

        # 급출발 판정
        if can_data.acceleration >= accel_threshold:
            event_type = "sudden_start"
            severity = "critical" if is_proximate else "warning"

        # 급제동 판정 (가속도가 음의 임계값 이하)
        elif can_data.acceleration <= -brake_threshold:
            event_type = "sudden_brake"
            severity = "critical" if is_proximate else "warning"

        # 과속 판정
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
