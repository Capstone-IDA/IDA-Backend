"""
Preprocessor
영상 프레임을 ONNX 입력 텐서로 변환
"""

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class Preprocessor:
    """CLAHE + letterbox + 정규화"""

    def __init__(
        self,
        target_size: tuple[int, int] = (640, 640),
        clip_limit: float = 2.0,
        tile_grid_size: tuple[int, int] = (8, 8),
        pad_value: int = 114,
    ):
        self.target_size = target_size
        self.clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid_size)
        self.pad_value = pad_value

    def apply_clahe(self, frame: np.ndarray) -> np.ndarray:
        """LAB 색공간 L 채널에 CLAHE 적용"""
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = self.clahe.apply(l)
        merged = cv2.merge([l, a, b])
        return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)

    def letterbox(self, frame: np.ndarray) -> tuple[np.ndarray, float, tuple[float, float]]:
        """비율 유지 리사이즈 + 패딩"""
        h, w = frame.shape[:2]
        target_w, target_h = self.target_size

        ratio = min(target_w / w, target_h / h)
        new_w = int(round(w * ratio))
        new_h = int(round(h * ratio))

        if (new_w, new_h) != (w, h):
            resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        else:
            resized = frame

        pad_w = (target_w - new_w) / 2
        pad_h = (target_h - new_h) / 2
        top = int(round(pad_h - 0.1))
        bottom = int(round(pad_h + 0.1))
        left = int(round(pad_w - 0.1))
        right = int(round(pad_w + 0.1))

        padded = cv2.copyMakeBorder(
            resized, top, bottom, left, right,
            cv2.BORDER_CONSTANT,
            value=(self.pad_value, self.pad_value, self.pad_value),
        )

        return padded, ratio, (float(left), float(top))

    def preprocess(self, frame: np.ndarray) -> tuple[np.ndarray, float, tuple[float, float]]:
        """전처리 파이프라인 실행"""
        # CLAHE
        frame = self.apply_clahe(frame)

        # letterbox
        padded, ratio, padding = self.letterbox(frame)

        # BGR -> RGB
        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)

        # 정규화
        normalized = rgb.astype(np.float32) / 255.0

        # HWC -> CHW
        chw = np.transpose(normalized, (2, 0, 1))

        # 배치 차원 추가
        tensor = np.expand_dims(chw, axis=0)
        tensor = np.ascontiguousarray(tensor)

        return tensor, ratio, padding
