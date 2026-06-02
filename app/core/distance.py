"""거리 구간화 헬퍼"""

def classify_zone(depth: float) -> str:
    """depth를 거리 구간으로 분류. 값이 클수록 가까운 객체."""
    if depth >= 0.85:
        return "danger"
    if depth >= 0.65:
        return "warning"
    return "safe"