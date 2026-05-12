"""
InferencePipeline
영상 처리 통합 파이프라인 (전처리 + 탐지 + 추적 + 깊이 + 위험도 평가)
"""

import logging
import time
from typing import Optional

import numpy as np

from app.core.object_classes import is_risk_target
from app.models.schemas import CANSnapshot, TrackedObject
from app.services.risk_evaluator import RiskEvaluator
from app.services.vision.depth_stub import DepthStub
from app.services.vision.object_detector import ObjectDetector
from app.services.vision.preprocessor import Preprocessor
from app.services.vision.tracker_stub import TrackerStub

logger = logging.getLogger(__name__)


class InferencePipeline:
    """영상 처리 통합 파이프라인"""

    def __init__(
        self,
        model_path: str,
        confidence_threshold: float = 0.25,
        iou_threshold: float = 0.45,
    ):
        self.preprocessor = Preprocessor()
        self.detector = ObjectDetector(
            model_path=model_path,
            confidence_threshold=confidence_threshold,
            iou_threshold=iou_threshold,
        )
        self.tracker = TrackerStub()
        self.depth_estimator = DepthStub()
        self.is_loaded: bool = False

    def load(self) -> bool:
        """모델 로드"""
        self.is_loaded = self.detector.load_model()
        return self.is_loaded

    def process(
        self,
        frame: np.ndarray,
        risk_evaluator: RiskEvaluator,
        can_snapshot: Optional[CANSnapshot] = None,
    ) -> tuple[list[TrackedObject], float, bool]:
        """프레임 1장 처리"""
        if not self.is_loaded:
            logger.warning("파이프라인이 로드되지 않은 상태에서 process() 호출")
            return [], 0.0, False

        t0 = time.perf_counter()

        # 전처리
        tensor, ratio, padding = self.preprocessor.preprocess(frame)

        # 객체 탐지
        detections = self.detector.detect(
            tensor=tensor,
            original_shape=frame.shape,
            ratio=ratio,
            padding=padding,
        )

        # 추적
        tracked = self.tracker.update(detections)

        # 깊이 추정 + 위험도 평가
        for t in tracked:
            t.depth = self.depth_estimator.estimate(t.smoothed_bbox)

            # INFO/UNDEFINED 카테고리는 위험 평가 제외
            if not is_risk_target(t.detection.class_id):
                t.risk_level = "safe"
                continue

            risk_level = risk_evaluator.assess(
                tracked_obj=t,
                depth=t.depth,
                can_data=can_snapshot,
            )
            t.risk_level = risk_level

        inference_ms = (time.perf_counter() - t0) * 1000
        depth_executed = len(tracked) > 0
        return tracked, inference_ms, depth_executed

    def reset_session(self) -> None:
        """세션 리셋"""
        self.tracker.reset()

    @staticmethod
    def get_worst_risk(tracked_objects: list[TrackedObject]) -> str:
        """가장 위험한 등급 반환"""
        priority = {"danger": 3, "warning": 2, "safe": 1}
        if not tracked_objects:
            return "safe"
        return max(
            (t.risk_level for t in tracked_objects),
            key=lambda r: priority.get(r, 0),
        )
