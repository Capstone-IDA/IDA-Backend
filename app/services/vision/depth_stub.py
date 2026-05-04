"""
DepthStub
임시 깊이 추정. bbox 면적 기반.
"""

import logging
import math

from app.models.schemas import BBox

logger = logging.getLogger(__name__)


class DepthStub:
    """임시 깊이 추정"""

    @staticmethod
    def estimate(bbox: BBox) -> float:
        """bbox 면적 기반 의사 depth (0~1)"""
        area = max(0.0, min(1.0, bbox.w * bbox.h))
        depth = 1.0 - math.sqrt(area)
        return max(0.0, min(1.0, depth))

    @staticmethod
    def classify_zone(depth: float) -> str:
        """거리 구간 분류"""
        if depth <= 0.15:
            return "NEAR"
        elif depth <= 0.35:
            return "MID"
        else:
            return "FAR"
