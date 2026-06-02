"""
ObjectClasses
탐지 모델의 클래스 정의 및 카테고리 매핑
"""

from enum import Enum


class ObjectCategory(str, Enum):
    """객체 카테고리"""
    DYNAMIC = "dynamic"
    STATIC_OBSTACLE = "static_obstacle"
    INFO = "info"
    UNDEFINED = "undefined"


# 클래스 ID -> 클래스명
CLASS_NAMES: dict[int, str] = {
    0: "Undefined Stuff",
    1: "Wall",
    2: "Driving Area",
    3: "Non Driving Area",
    4: "Parking Line",
    5: "Parking Area",
    6: "No Parking Area",
    7: "Big Notice",
    8: "Pillar",
    9: "Parking Area Number",
    10: "Disabled Icon",
    11: "Women Icon",
    12: "Compact Car Icon",
    13: "Speed Bump",
    14: "Parking Block",
    15: "Billboard",
    16: "Toll Bar",
    17: "Sign",
    18: "No Parking Sign",
    19: "Traffic Cone",
    20: "Fire Extinguisher",
    21: "Undefined Object",
    22: "Two-wheeled Vehicle",
    23: "Vehicle",
    24: "Wheelchair",
    25: "Stroller",
    26: "Shopping Cart",
    27: "Animal",
    28: "Human",
}


# 클래스 ID -> 카테고리
CLASS_CATEGORIES: dict[int, ObjectCategory] = {
    0: ObjectCategory.UNDEFINED,
    21: ObjectCategory.UNDEFINED,

    # 영역/라인/마커 (위험 평가 제외 대상)
    2: ObjectCategory.INFO,
    3: ObjectCategory.INFO,
    4: ObjectCategory.INFO,
    5: ObjectCategory.INFO,
    6: ObjectCategory.INFO,
    9: ObjectCategory.INFO,
    10: ObjectCategory.INFO,
    11: ObjectCategory.INFO,
    12: ObjectCategory.INFO,
    18: ObjectCategory.INFO,

    # 정적 장애물
    1: ObjectCategory.STATIC_OBSTACLE,   # Wall
    7: ObjectCategory.STATIC_OBSTACLE,   # Big Notice
    8: ObjectCategory.STATIC_OBSTACLE,   # Pillar
    13: ObjectCategory.STATIC_OBSTACLE,  # Speed Bump
    14: ObjectCategory.STATIC_OBSTACLE,  # Parking Block
    15: ObjectCategory.INFO,             # Billboard     ← INFO로 변경s
    16: ObjectCategory.STATIC_OBSTACLE,  # Toll Bar
    17: ObjectCategory.INFO,             # Sign          ← INFO로 변경
    19: ObjectCategory.STATIC_OBSTACLE,  # Traffic Cone
    20: ObjectCategory.STATIC_OBSTACLE,  # Fire Extinguisher
    26: ObjectCategory.STATIC_OBSTACLE,  # Shopping Cart

    # 동적 객체
    22: ObjectCategory.DYNAMIC,
    23: ObjectCategory.DYNAMIC,
    24: ObjectCategory.DYNAMIC,
    25: ObjectCategory.DYNAMIC,
    27: ObjectCategory.DYNAMIC,
    28: ObjectCategory.DYNAMIC,
}


# 위험 평가 대상 카테고리
RISK_TARGET_CATEGORIES: set[ObjectCategory] = {
    ObjectCategory.DYNAMIC,
    ObjectCategory.STATIC_OBSTACLE,
}


def get_category(class_id: int) -> ObjectCategory:
    """클래스 ID로 카테고리 반환"""
    return CLASS_CATEGORIES.get(class_id, ObjectCategory.UNDEFINED)


def is_risk_target(class_id: int) -> bool:
    """위험 평가 대상 여부"""
    return get_category(class_id) in RISK_TARGET_CATEGORIES


def get_class_name(class_id: int) -> str:
    """클래스 ID로 클래스명 반환"""
    return CLASS_NAMES.get(class_id, f"class_{class_id}")

# 클래스 ID -> 한글 표시명 (없으면 영문 폴백)
CLASS_NAMES_KO: dict[int, str] = {
    1: "벽", 8: "기둥", 7: "안내판", 13: "과속방지턱", 14: "주차블록",
    16: "차단바", 19: "라바콘", 20: "소화기", 26: "쇼핑카트",
    15: "광고판", 17: "표지판",
    22: "이륜차", 23: "차량", 24: "휠체어", 25: "유모차", 27: "동물", 28: "사람",
}

# 영문 클래스명 -> ID 역매핑
_NAME_TO_ID: dict[str, int] = {v: k for k, v in CLASS_NAMES.items()}

# 항상 그리는 정적 객체 (danger일 때만)
_STATIC_SHOW_WHEN_DANGER: set[int] = {1, 8}  # Wall, Pillar


def get_korean_name(class_name: str) -> str:
    """영문 클래스명을 한글 표시명으로 변환."""
    cid = _NAME_TO_ID.get(class_name)
    if cid is None:
        return class_name
    return CLASS_NAMES_KO.get(cid, class_name)


def get_category_by_name(class_name: str) -> ObjectCategory:
    """영문 클래스명으로 카테고리 반환."""
    cid = _NAME_TO_ID.get(class_name)
    return get_category(cid) if cid is not None else ObjectCategory.UNDEFINED


def should_display(class_name: str, risk_level: str) -> bool:
    """bbox 화면 표시 여부. 동적은 항상, 벽/기둥은 danger만, 나머지는 숨김."""
    cid = _NAME_TO_ID.get(class_name)
    if cid is None:
        return False
    category = get_category(cid)
    if category == ObjectCategory.DYNAMIC:
        return True
    if category == ObjectCategory.STATIC_OBSTACLE and cid in _STATIC_SHOW_WHEN_DANGER:
        return risk_level.lower() == "danger"
    return False