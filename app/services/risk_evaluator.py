"""
app/services/risk_evaluator.py

RiskEvaluator
객체 위험도 평가 + CAN 데이터 기반 운전 이벤트 생성
"""

import logging
from typing import Optional

from app.core.object_classes import RISK_TARGET_CATEGORIES, get_category
from app.models.schemas import CANSnapshot, DrivingEvent, ScoringConfig, TrackedObject

logger = logging.getLogger(__name__)

# 실내 주차장 기준 폴백 임계값 (DB 설정 미로드 시 사용)
# 실측 CAN 데이터(test_scenario_1~4) 기반으로 도출
_DEFAULT_ACCEL_THRESHOLD = 3.0    # m/s², 실측 급출발 최솟값
_DEFAULT_BRAKE_THRESHOLD = 3.0    # m/s², accYAve 기준 급제동
_DEFAULT_BRAKE_INTENSITY = 0.25   # brake_pressure/80 기준, 압력 급증 판정
_DEFAULT_SPEED_LIMIT = 20.0       # km/h, 실내 주차장 제한


# 벽처럼 화면을 가르는 면 구조물은 점유율이 거리와 무관하게 커서 근접 판정에서 제외
_BOUNDARY_CLASS_IDS = {1}         # Wall

# 기둥류 세로 구조물은 근접해도 폭이 좁아 면적이 작게 잡힌다
# 화면 높이 점유와 바닥 접점 위치로 근접을 보정 판정
_VERTICAL_CLASS_IDS = {8}         # Pillar
_VERTICAL_NEAR_H = 0.75           # bbox 높이 비율 하한
_VERTICAL_NEAR_Y2 = 0.85          # bbox 하단 위치 하한
_VERTICAL_NEAR_MIN_AREA = 0.02    # 슬리버 오탐 방지용 최소 면적

# 추돌 경고 판정: danger가 연속 유지될 때만 배너 신호 활성
_COLLISION_MIN_FRAMES = 3      # 최소 연속 프레임
_COLLISION_MISS_GRACE = 1      # 탐지 누락 허용 프레임
_COLLISION_NOTIFY_GAP = 72     # 알림 기록 최소 간격, 24fps 기준 약 3초


class RiskEvaluator:
    """위험도 평가 및 운전 이벤트 생성"""

    def __init__(self):
        self.area_danger_ratio: float = 0.20    # 화면 점유율 기준 근접 danger
        self.area_warning_ratio: float = 0.08   # 화면 점유율 기준 근접 warning
        self.config: Optional[ScoringConfig] = None
        # 세션별 추돌 경고 상태
        self.collision_streak: dict[str, int] = {}
        self.collision_miss: dict[str, int] = {}
        self.collision_active: dict[str, bool] = {}
        self.collision_last_notify: dict[str, int] = {}

    def reload_config(self, config: ScoringConfig) -> None:
        """면적 임계값 설정 반영"""
        self.config = config
        self.area_danger_ratio = config.area_danger_ratio
        self.area_warning_ratio = config.area_warning_ratio

    def update_collision_state(self, session_id: str, frame_has_danger: bool,
                               frame_number: int) -> tuple[bool, bool]:
        """프레임 단위 추돌 경고 상태 갱신. (활성 여부, 알림 필요 여부) 반환"""
        streak = self.collision_streak.get(session_id, 0)
        miss = self.collision_miss.get(session_id, 0)

        if frame_has_danger:
            streak += 1
            miss = 0
        elif streak >= _COLLISION_MIN_FRAMES and miss < _COLLISION_MISS_GRACE:
            # 활성 상태에서 한 프레임 탐지 누락은 유지로 간주, 진입에는 미적용
            streak += 1
            miss += 1
        else:
            streak = 0
            miss = 0

        self.collision_streak[session_id] = streak
        self.collision_miss[session_id] = miss

        was_active = self.collision_active.get(session_id, False)
        active = streak >= _COLLISION_MIN_FRAMES
        self.collision_active[session_id] = active

        notify = False
        if active and not was_active:
            last = self.collision_last_notify.get(session_id)
            if last is None or frame_number - last >= _COLLISION_NOTIFY_GAP:
                notify = True
                self.collision_last_notify[session_id] = frame_number
        return active, notify

    def reset_collision_state(self, session_id: str) -> None:
        """세션 종료 시 추돌 경고 상태 제거"""
        self.collision_streak.pop(session_id, None)
        self.collision_miss.pop(session_id, None)
        self.collision_active.pop(session_id, None)
        self.collision_last_notify.pop(session_id, None)

    def assess(self, track_id: int, class_id: int, depth: float,
               is_moving: bool = True, bbox_area_ratio: float = 0.0,
               bbox_h: float = 0.0, bbox_y2: float = 0.0) -> str:
        """객체별 위험도 평가. 근접 판정은 화면 점유율(bbox_area_ratio) 기준.
        기둥류 세로 구조물은 높이와 바닥 접점 위치로 근접을 보정 판정한다.
        depth_val은 시나리오별 부호 방향이 일치하지 않아 구간 판정에 쓰지 않는다."""
        if get_category(class_id) not in RISK_TARGET_CATEGORIES:
            return "safe"
        if class_id in _BOUNDARY_CLASS_IDS:
            return "safe"
        if bbox_area_ratio >= self.area_danger_ratio:
            return "danger"
        if (class_id in _VERTICAL_CLASS_IDS
                and bbox_h >= _VERTICAL_NEAR_H
                and bbox_y2 >= _VERTICAL_NEAR_Y2
                and bbox_area_ratio >= _VERTICAL_NEAR_MIN_AREA):
            return "danger"
        if bbox_area_ratio >= self.area_warning_ratio:
            return "warning"
        return "safe"

    def evaluate_driving_event(
        self,
        session_id: str,
        can_data: CANSnapshot,
        risk_level: str,
        track_id: Optional[int] = None,
    ) -> Optional[DrivingEvent]:
        """CAN 데이터 + 위험등급 기반 운전 이벤트 판정 (DS002).

        판정 우선순위:
          1. brake_intensity >= brake_intensity_threshold -> sudden_brake (압력 기반)
          2. acceleration <= -brake_threshold            -> sudden_brake (감속도 기반)
          3. acceleration >= accel_threshold             -> sudden_start
          4. speed_kmh >= speed_limit                   -> overspeeding
        """
        cfg = self.config
        accel_threshold = cfg.accel_threshold if cfg else _DEFAULT_ACCEL_THRESHOLD
        brake_threshold = cfg.brake_threshold if cfg else _DEFAULT_BRAKE_THRESHOLD
        speed_limit = cfg.speed_limit if cfg else _DEFAULT_SPEED_LIMIT

        # brake_intensity_threshold: DB에 컬럼 없으므로 상수 사용
        # brake_pressure 20/80 = 0.25 (실내 주차 기준 유효 제동 최솟값)
        brake_intensity_threshold = _DEFAULT_BRAKE_INTENSITY

        is_proximate = risk_level == "danger"
        event_type: Optional[str] = None
        severity = "normal"

        # 1순위: brake_pressure 기반 급제동 (가속도보다 신뢰도 높음)
        if can_data.brake_intensity >= brake_intensity_threshold:
            event_type = "sudden_brake"
            severity = "critical" if is_proximate else "warning"

        # 2순위: 감속도 기반 급제동
        elif can_data.acceleration <= -brake_threshold:
            event_type = "sudden_brake"
            severity = "critical" if is_proximate else "warning"

        # 3순위: 급출발 (브레이크 미작동 상태에서 가속도 초과)
        elif can_data.acceleration >= accel_threshold:
            event_type = "sudden_start"
            severity = "critical" if is_proximate else "warning"

        # 4순위: 과속 (상기 이벤트 없을 때만 체크)
        elif can_data.speed_kmh >= speed_limit:
            event_type = "overspeeding"
            severity = "critical" if is_proximate else "warning"

        if event_type is None:
            return None

        return DrivingEvent(
            session_id=session_id,
            timestamp=can_data.timestamp,
            event_type=event_type,
            severity=severity,
            speed=can_data.speed_kmh,
            acceleration=can_data.acceleration,
            is_proximate=is_proximate,
            deduction=0,  # DrivingScorer에서 계산
            track_id=track_id,
        )