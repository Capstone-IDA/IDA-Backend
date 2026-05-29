"""거리 구간화 헬퍼"""


# 변경 (AI가 1=가까움 스케일로 보냄)
def classify_zone(depth: float) -> str:
    # AI depth: 1=가까움, 0=멀다 → 반전해서 판정
    inverted = 1.0 - depth
    if inverted <= 0.15:
        return "danger"
    elif inverted <= 0.35:
        return "warning"
    return "safe"