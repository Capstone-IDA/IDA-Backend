"""
Pydantic 데이터 모델
설계 레퍼런스 [5] 데이터 모델 기반
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field



# ──────────────────────────────────────
# 기본 데이터 구조
# ──────────────────────────────────────

class BBox(BaseModel):
    """바운딩 박스 좌표 (정규화 0~1)"""
    x: float = Field(..., ge=0, le=1)
    y: float = Field(..., ge=0, le=1)
    w: float = Field(..., ge=0, le=1)
    h: float = Field(..., ge=0, le=1)


class CANSnapshot(BaseModel):
    """CAN 시뮬레이터 스냅샷"""
    timestamp: datetime
    speed_kmh: float = Field(..., ge=0, description="차량 속도 (km/h)")
    acceleration: float = Field(..., description="가속도 (m/s²)")
    brake_intensity: float = Field(..., ge=0, le=1, description="브레이크 강도 (0~1)")
    scenario: str = Field(default="normal", description="현재 시나리오명")


class ScenarioPreset(BaseModel):
    """CAN 시뮬레이터 시나리오 프리셋"""
    name: str
    description: str
    speed_profile: list[float]
    accel_profile: list[float]
    brake_profile: list[float]
    duration_sec: int
# AI 응답 스키마

class EgoMotion(BaseModel):
    """자차 움직임 (옵티컬 플로우 기반)"""
    vx: float
    vy: float
    speed: float = 0.0


class AIDetectedObject(BaseModel):
    """AI가 보낸 개별 탐지 객체"""
    class_id: int
    class_name: str
    confidence: float = Field(..., ge=0, le=1)
    bbox: BBox
    track_id: int
    depth_val: float = Field(..., ge=0, le=1.5)
    bbox_area_ratio: float = Field(..., ge=0)
    bbox_velocity_x: float
    bbox_velocity_y: float
    obj_speed_px: float = Field(..., ge=0)
    is_moving: bool


class AIDetectionPayload(BaseModel):
    """AI가 BE의 /detect로 보내는 페이로드"""
    frame_id: int
    timestamp: str
    fps: float
    inference_time_ms: float
    session_id: str
    objects: list[AIDetectedObject] = Field(default_factory=list)
    ego_motion: EgoMotion

# ──────────────────────────────────────
# 영상 처리 & 객체 감지
# ──────────────────────────────────────

class DetectedObject(BaseModel):
    """개별 탐지 객체"""
    class_id: int
    class_name: str
    confidence: float = Field(..., ge=0, le=1)
    bbox: BBox
    can_snapshot: Optional[CANSnapshot] = None


class TrackedObject(BaseModel):
    """추적 중인 객체"""
    track_id: int
    detection: DetectedObject
    is_new: bool = False
    smoothed_bbox: BBox
    depth: float = Field(default=0.0, ge=0)
    risk_level: str = Field(default="safe", description="danger / warning / safe")


class DetectionResult(BaseModel):
    """/detect API 응답 래퍼"""
    frame_id: int
    timestamp: datetime
    objects: list[DetectedObject] = Field(default_factory=list)
    inference_time_ms: float = Field(..., ge=0)
    fps: float = Field(..., ge=0)


class AlertRecord(BaseModel):
    """경고 기록"""
    alert_id: Optional[int] = None
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    track_id: int
    risk_level: str
    consecutive_frames: int
    score: float
    grade: str


# ──────────────────────────────────────
# 운전 행동 분석
# ──────────────────────────────────────

class DrivingEvent(BaseModel):
    """운전 위험 이벤트"""
    event_id: Optional[int] = None
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    event_type: str = Field(..., description="sudden_start / sudden_brake / overspeeding")
    severity: str = Field(default="normal", description="normal / warning / critical")
    speed: float
    acceleration: float
    is_proximate: bool = False
    deduction: float = Field(..., ge=0)
    track_id: Optional[int] = None
    can_id: Optional[int] = None


class ScoreRecord(BaseModel):
    """점수 변동 기록"""
    score_id: Optional[int] = None
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    previous_score: float
    deduction: float
    current_score: float
    grade: str = Field(..., description="Green / Yellow / Orange / Red")
    event_id: Optional[int] = None


# ──────────────────────────────────────
# 알림 & 리포트
# ──────────────────────────────────────

class NotificationLog(BaseModel):
    """알림 로그"""
    notification_id: Optional[int] = None
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    grade: str
    score: float
    notification_type: str = Field(..., description="warning / critical")
    company_id: Optional[str] = None
    status: str = Field(default="sent", description="sent / failed / retrying")
    retry_count: int = Field(default=0, ge=0)


class RentalReport(BaseModel):
    """반납 시 종합 리포트"""
    report_id: Optional[int] = None
    session_id: str
    user_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    duration_minutes: float = Field(default=0, ge=0)
    initial_score: float = Field(default=100)
    final_score: float
    final_grade: str
    total_events: int = Field(default=0, ge=0)
    sudden_start_count: int = Field(default=0, ge=0)
    sudden_brake_count: int = Field(default=0, ge=0)
    overspeeding_count: int = Field(default=0, ge=0)
    proximate_event_count: int = Field(default=0, ge=0)
    score_timeline: list = Field(default_factory=list)
    is_complete: bool = False


class BlacklistRecord(BaseModel):
    """블랙리스트 기록"""
    blacklist_id: Optional[int] = None
    user_id: str
    session_id: str
    final_score: float
    blacklist_grade: str = Field(..., description="normal / caution / blacklisted")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    is_active: bool = True
    history_count: int = Field(default=1, ge=0)


# ──────────────────────────────────────
# 설정
# ──────────────────────────────────────

class ScoringConfig(BaseModel):
    """스코어링 기준값 설정"""
    config_id: Optional[int] = None
    accel_threshold: float = Field(default=2.0, description="급출발 가속도 임계값 (m/s²)")
    brake_threshold: float = Field(default=2.0, description="급제동 감속도 임계값 (m/s²)")
    speed_limit: float = Field(default=30.0, description="과속 기준 속도 (km/h)")
    proximity_distance: float = Field(default=0.2, description="근접 판정 거리")
    deduction_sudden_start: float = Field(default=5.0, description="급출발 감점")
    deduction_sudden_brake: float = Field(default=5.0, description="급제동 감점")
    deduction_proximate: float = Field(default=10.0, description="근접 객체 상태 급가속/급제동 감점")
    deduction_overspeeding: float = Field(default=8.0, description="근접 객체 상태 과속 감점")
    green_min: int = Field(default=80)
    yellow_min: int = Field(default=50)
    orange_min: int = Field(default=30)
    blacklist_threshold: int = Field(default=30, description="블랙리스트 편입 기준 점수")
    alert_min_interval_sec: int = Field(default=30, description="알림 최소 간격 (초)")
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None


# ──────────────────────────────────────
# API 요청/응답 모델
# ──────────────────────────────────────

class SessionStartRequest(BaseModel):
    """세션 시작 요청"""
    user_id: str
    vehicle_id: str
    scenario: Optional[str] = None


class SessionStartResponse(BaseModel):
    """세션 시작 응답"""
    session_id: str
    start_time: datetime
    initial_score: float = 100
    status: str = "active"


class SessionEndRequest(BaseModel):
    """세션 종료 요청"""
    session_id: str


class HealthStatus(BaseModel):
    """서버 상태"""
    status: str = "healthy"
    model_loaded: bool = False
    db_connected: bool = False
    uptime_seconds: float = 0


class ErrorResponse(BaseModel):
    """에러 응답"""
    error_type: str
    severity: str = "warning"
    message: str


class DetectionApiResponse(BaseModel):
    """POST /detect 전체 응답"""
    session_id: Optional[str] = None
    frame_id: int
    frame_ref: Optional[str] = None
    timestamp: datetime
    system: dict = Field(default_factory=dict)
    summary: dict = Field(default_factory=dict)
    can: Optional[CANSnapshot] = None
    ego_motion: Optional[EgoMotion] = None
    objects: list[dict] = Field(default_factory=list)
    driving_events: list[dict] = Field(default_factory=list)
    alerts: list[dict] = Field(default_factory=list)
    score: Optional[dict] = None
    error: Optional[ErrorResponse] = None


class ConfigUpdateRequest(BaseModel):
    """설정 부분 업데이트 요청"""
    accel_threshold: Optional[float] = None
    brake_threshold: Optional[float] = None
    speed_limit: Optional[float] = None
    proximity_distance: Optional[float] = None
    deduction_sudden_start: Optional[float] = None
    deduction_sudden_brake: Optional[float] = None
    deduction_proximate: Optional[float] = None
    deduction_overspeeding: Optional[float] = None
    green_min: Optional[int] = None
    yellow_min: Optional[int] = None
    orange_min: Optional[int] = None
    blacklist_threshold: Optional[int] = None
    alert_min_interval_sec: Optional[int] = None
    updated_by: Optional[str] = None


class LogQueryParams(BaseModel):
    """로그 조회 파라미터"""
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    limit: int = Field(default=100, ge=1, le=1000)


class BlacklistQueryParams(BaseModel):
    """블랙리스트 조회 필터"""
    grade: Optional[str] = None
    is_active: Optional[bool] = None
    limit: int = Field(default=50, ge=1, le=500)


# ──────────────────────────────────────
# 인증 (로그인/계정)
# ──────────────────────────────────────

class LoginRequest(BaseModel):
    """로그인 요청"""
    username: str
    password: str


class LoginResponse(BaseModel):
    """로그인 응답"""
    token: str
    account_id: str
    role: str = Field(..., description="admin / company")
    company_id: Optional[str] = None
    company_name: Optional[str] = None


class AccountInfo(BaseModel):
    """계정 정보 (GET /auth/me)"""
    account_id: str
    username: str
    role: str = Field(..., description="admin / company")
    company_id: Optional[str] = None
    company_name: Optional[str] = None


class AccountCreateRequest(BaseModel):
    """계정 생성 요청 (Admin 전용)"""
    username: str
    password: str
    role: str = Field(default="company", description="admin / company")
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    contact: Optional[str] = None
    notification_endpoint: Optional[str] = None
