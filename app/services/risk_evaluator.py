"""
RiskEvaluator
객체 위험도 평가 + CAN 데이터 기반 운전 이벤트 생성
"""

import logging
from datetime import datetime
from typing import Optional

from app.models.schemas import CANSnapshot, DrivingEvent, ScoringConfig, TrackedObject

logger = logging.getLogger(__name__)


class RiskEvaluator:
    """위험도 평가 및 운전 이벤트 생성"""

    def __init__(self):
        self.danger_threshold: float = 0.15   # depth 기준 Danger
        self.warning_threshold: float = 0.35  # depth 기준 Warning
        self.consecutive_danger: dict[int, int] = {}  # track_id → 연속 Danger 프레임 수
        self.alert_frame_count: int = 3  # 연속 3프레임 Danger 시 경고
        self.config: Optional[ScoringConfig] = None

    def reload_config(self, config: ScoringConfig) -> None:
        """설정 반영"""
        self.config = config
        self.danger_threshold = config.proximity_distance

    def assess(self, track_id: int, depth: float,
           can_data: Optional[CANSnapshot] = None,
           is_moving: bool = True) -> str:
        """객체별 위험도 평가, 정적 객체는 평가 제외"""
        # 정적 객체는 위험도 평가 제외 (결함 #3 대응)
        if not is_moving:
            self.reset_counter(track_id)
            return "safe"

        # 거리 기반 위험도 구간화
        if depth <= self.danger_threshold:
            risk_level = "danger"
        elif depth <= self.warning_threshold:
            risk_level = "warning"
        else:
            risk_level = "safe"

        # 연속 Danger 카운트
        if risk_level == "danger":
            self.consecutive_danger[track_id] = \
                self.consecutive_danger.get(track_id, 0) + 1
        else:
            self.reset_counter(track_id)

        return risk_level

    def check_consecutive_danger(self, track_id: int) -> bool:
        """연속 3프레임 이상 Danger인지 확인"""
        return self.consecutive_danger.get(track_id, 0) >= self.alert_frame_count

    def reset_counter(self, track_id: int) -> None:
        """연속 Danger 카운터 리셋"""
        self.consecutive_danger.pop(track_id, None)

    def get_risk_color(self, risk_level: str) -> str:
        """위험도 → 색상 (MR001)"""
        colors = {
            "danger": "red",
            "warning": "yellow",
            "safe": "green",
        }
        return colors.get(risk_level, "green")

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
