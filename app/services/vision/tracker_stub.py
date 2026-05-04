"""
TrackerStub
임시 트래커. 매 프레임 자동증가 ID 부여.
"""

import itertools
import logging

from app.models.schemas import DetectedObject, TrackedObject

logger = logging.getLogger(__name__)


class TrackerStub:
    """임시 트래커"""

    def __init__(self) -> None:
        self._counter = itertools.count(1)

    def update(self, detections: list[DetectedObject]) -> list[TrackedObject]:
        """탐지 결과를 추적 객체로 변환"""
        tracked: list[TrackedObject] = []
        for det in detections:
            track_id = next(self._counter)
            tracked.append(
                TrackedObject(
                    track_id=track_id,
                    detection=det,
                    is_new=True,
                    smoothed_bbox=det.bbox,
                    depth=0.0,
                    risk_level="safe",
                )
            )
        return tracked

    def reset(self) -> None:
        """카운터 초기화"""
        self._counter = itertools.count(1)
