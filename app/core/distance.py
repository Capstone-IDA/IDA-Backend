"""거리 구간화 헬퍼"""


def classify_zone(depth: float) -> str:
    """depth를 거리 구간으로 분류. 값이 작을수록 가까운 객체."""
    if depth <= 0.15:
        return "danger"
    elif depth <= 0.35:
        return "warning"
    return "safe"