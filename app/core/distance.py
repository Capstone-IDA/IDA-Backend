"""거리 구간화 헬퍼"""


def classify_zone(depth: float) -> str:
    """depth 값을 거리 구간으로 분류"""
    if depth <= 0.15:
        return "danger"
    elif depth <= 0.35:
        return "warning"
    return "safe"