"""
DrivingScorer
누적 스코어 관리 + 등급 산출, config_cache로 기준값 캐싱
"""

import logging
from typing import Optional

from app.models.schemas import DrivingEvent, ScoringConfig

logger = logging.getLogger(__name__)


class DrivingScorer:
    """운전 행동 스코어 관리"""

    def __init__(self):
        self.current_score: float = 100.0
        self.initial_score: float = 100.0
        self.min_score: float = 0.0
        self.config_cache: Optional[ScoringConfig] = None
        self._previous_grade: str = "Green"

    def reload_config(self, config: ScoringConfig) -> None:
        """설정 캐시 갱신"""
        self.config_cache = config
        logger.info("DrivingScorer 설정 캐시 갱신 완료")

    def reset(self, session_id: str) -> None:
        """세션 시작 시 점수 초기화"""
        self.current_score = self.initial_score
        self._previous_grade = "Green"
        logger.info(f"스코어 초기화: session={session_id}, score={self.current_score}")

    def apply_deduction(self, event: DrivingEvent) -> float:
        """이벤트에 따른 감점 적용, 감점 후 현재 점수 반환"""
        deduction = self._get_deduction(event.event_type, event.is_proximate)
        event.deduction = deduction  # 이벤트에 실제 감점값 기록

        previous = self.current_score
        self.current_score = max(self.min_score, self.current_score - deduction)

        logger.info(
            f"감점 적용: {event.event_type} "
            f"(proximate={event.is_proximate}) "
            f"-{deduction}점 → {previous} → {self.current_score}"
        )
        return self.current_score

    def get_current_score(self) -> float:
        return self.current_score

    def get_grade(self) -> str:
        return self._classify_grade(self.current_score)

    def has_grade_changed(self) -> bool:
        """등급 변동 여부 확인"""
        current_grade = self.get_grade()
        changed = current_grade != self._previous_grade
        if changed:
            logger.info(f"등급 변동: {self._previous_grade} → {current_grade}")
        self._previous_grade = current_grade
        return changed

    def _get_deduction(self, event_type: str, is_proximate: bool) -> float:
        """이벤트 유형 + 근접 여부에 따른 감점값 산출"""
        if not self.config_cache:
            # 캐시 없을 때 기본값
            defaults = {
                "sudden_start": 5.0,
                "sudden_brake": 5.0,
                "overspeeding": 8.0,
            }
            base = defaults.get(event_type, 5.0)
            return 10.0 if is_proximate and event_type != "overspeeding" else (8.0 if is_proximate else base)

        cfg = self.config_cache
        if is_proximate:
            if event_type == "overspeeding":
                return cfg.deduction_overspeeding
            return cfg.deduction_proximate
        else:
            if event_type == "sudden_start":
                return cfg.deduction_sudden_start
            elif event_type == "sudden_brake":
                return cfg.deduction_sudden_brake
            elif event_type == "overspeeding":
                return cfg.deduction_overspeeding
            return cfg.deduction_sudden_start

    def _classify_grade(self, score: float) -> str:
        """점수 기반 등급 분류: Green/Yellow/Orange/Red"""
        if not self.config_cache:
            # 기본값
            if score >= 80:
                return "Green"
            elif score >= 50:
                return "Yellow"
            elif score >= 30:
                return "Orange"
            return "Red"

        cfg = self.config_cache
        if score >= cfg.green_min:
            return "Green"
        elif score >= cfg.yellow_min:
            return "Yellow"
        elif score >= cfg.orange_min:
            return "Orange"
        return "Red"
