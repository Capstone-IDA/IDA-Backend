"""
ObjectDetector
ONNX Runtime 기반 YOLOv8 추론
"""

import logging
from typing import Optional

import numpy as np

try:
    import onnxruntime as ort
except ImportError:
    ort = None

from app.core.object_classes import get_class_name
from app.models.schemas import BBox, DetectedObject

logger = logging.getLogger(__name__)


class ObjectDetector:
    """YOLOv8 ONNX 추론 래퍼"""

    def __init__(
        self,
        model_path: str,
        confidence_threshold: float = 0.25,
        iou_threshold: float = 0.45,
        num_classes: int = 29,
    ):
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.iou_threshold = iou_threshold
        self.num_classes = num_classes
        self.session: Optional["ort.InferenceSession"] = None
        self.input_name: Optional[str] = None
        self.output_name: Optional[str] = None

    def load_model(self) -> bool:
        """모델 로드"""
        if ort is None:
            logger.error("onnxruntime이 설치되어 있지 않습니다")
            return False
        try:
            providers = ["CPUExecutionProvider"]
            if "CUDAExecutionProvider" in ort.get_available_providers():
                providers.insert(0, "CUDAExecutionProvider")

            self.session = ort.InferenceSession(self.model_path, providers=providers)
            self.input_name = self.session.get_inputs()[0].name
            self.output_name = self.session.get_outputs()[0].name
            logger.info(f"ObjectDetector 모델 로드 완료: {self.model_path}")
            return True
        except Exception as e:
            logger.error(f"ObjectDetector 모델 로드 실패: {e}")
            self.session = None
            return False

    def detect(
        self,
        tensor: np.ndarray,
        original_shape: tuple[int, int],
        ratio: float,
        padding: tuple[float, float],
    ) -> list[DetectedObject]:
        """추론 + 후처리"""
        if self.session is None:
            return []

        try:
            outputs = self.session.run(
                [self.output_name],
                {self.input_name: tensor},
            )
        except Exception as e:
            logger.error(f"ONNX 추론 실패: {e}")
            return []

        # [1, 4 + num_classes, 8400] -> [8400, 4 + num_classes]
        predictions = outputs[0][0].transpose()

        boxes = predictions[:, :4]
        class_scores = predictions[:, 4:]

        class_ids = np.argmax(class_scores, axis=1)
        confidences = np.max(class_scores, axis=1)

        # confidence 필터
        mask = confidences >= self.confidence_threshold
        boxes = boxes[mask]
        class_ids = class_ids[mask]
        confidences = confidences[mask]

        if len(boxes) == 0:
            return []

        # cx,cy,w,h -> x1,y1,x2,y2
        boxes_xyxy = self._cxcywh_to_xyxy(boxes)

        # 클래스별 NMS
        keep = self._nms_per_class(boxes_xyxy, confidences, class_ids, self.iou_threshold)

        if not keep:
            return []

        boxes_xyxy = boxes_xyxy[keep]
        class_ids = class_ids[keep]
        confidences = confidences[keep]

        # letterbox 좌표를 원본 픽셀 좌표로 역변환
        h_orig, w_orig = original_shape[:2]
        pad_x, pad_y = padding
        boxes_xyxy[:, [0, 2]] = (boxes_xyxy[:, [0, 2]] - pad_x) / ratio
        boxes_xyxy[:, [1, 3]] = (boxes_xyxy[:, [1, 3]] - pad_y) / ratio

        # 0~1 정규화
        boxes_xyxy[:, [0, 2]] = np.clip(boxes_xyxy[:, [0, 2]] / w_orig, 0.0, 1.0)
        boxes_xyxy[:, [1, 3]] = np.clip(boxes_xyxy[:, [1, 3]] / h_orig, 0.0, 1.0)

        results: list[DetectedObject] = []
        for i in range(len(boxes_xyxy)):
            x1, y1, x2, y2 = boxes_xyxy[i]
            class_id = int(class_ids[i])

            w = float(x2 - x1)
            h = float(y2 - y1)
            if w <= 0 or h <= 0:
                continue

            results.append(
                DetectedObject(
                    class_id=class_id,
                    class_name=get_class_name(class_id),
                    confidence=float(confidences[i]),
                    bbox=BBox(x=float(x1), y=float(y1), w=w, h=h),
                )
            )

        return results

    @staticmethod
    def _cxcywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
        """(cx, cy, w, h)를 (x1, y1, x2, y2)로 변환"""
        out = np.empty_like(boxes)
        out[:, 0] = boxes[:, 0] - boxes[:, 2] / 2.0
        out[:, 1] = boxes[:, 1] - boxes[:, 3] / 2.0
        out[:, 2] = boxes[:, 0] + boxes[:, 2] / 2.0
        out[:, 3] = boxes[:, 1] + boxes[:, 3] / 2.0
        return out

    @staticmethod
    def _nms_single_class(
        boxes: np.ndarray,
        scores: np.ndarray,
        iou_threshold: float,
    ) -> list[int]:
        """단일 클래스 NMS"""
        if len(boxes) == 0:
            return []
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1) * (y2 - y1)
        order = scores.argsort()[::-1]

        keep: list[int] = []
        while order.size > 0:
            i = order[0]
            keep.append(int(i))
            if order.size == 1:
                break

            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])

            w = np.maximum(0.0, xx2 - xx1)
            h = np.maximum(0.0, yy2 - yy1)
            inter = w * h

            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
            inds = np.where(iou <= iou_threshold)[0]
            order = order[inds + 1]

        return keep

    def _nms_per_class(
        self,
        boxes: np.ndarray,
        scores: np.ndarray,
        class_ids: np.ndarray,
        iou_threshold: float,
    ) -> list[int]:
        """클래스별 NMS"""
        final_keep: list[int] = []
        for cls in np.unique(class_ids):
            mask = class_ids == cls
            cls_indices = np.where(mask)[0]
            cls_boxes = boxes[mask]
            cls_scores = scores[mask]

            local_keep = self._nms_single_class(cls_boxes, cls_scores, iou_threshold)
            final_keep.extend(cls_indices[local_keep].tolist())

        return final_keep
