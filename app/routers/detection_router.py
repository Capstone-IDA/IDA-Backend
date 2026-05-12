"""
DetectionRouter
POST /detect | GET /health
AI 서버와 통신, BE에서 5프레임 주기 또는 CAN 이벤트 트리거로 점수 산출
"""

import logging
import os
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.distance import classify_zone
from app.models.schemas import (
    AIDetectionResponse,
    CANSnapshot,
    DetectionApiResponse,
    HealthStatus,
    ScoreRecord,
    ScoringConfig,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Detection"])

_start_time = time.time()
SCORE_INTERVAL = int(os.getenv("IDA_SCORE_INTERVAL", "5"))


@router.post("/detect", response_model=DetectionApiResponse)
async def detect(
    file: UploadFile = File(...),
    camera_id: str = Form(default="1"),
    frame_id: int = Form(default=0),
    timestamp: Optional[str] = Form(default=None),
):
    """프레임 수신, AI 호출, 조건부 점수 산출"""
    from app.main import app_state

    session_id = app_state.active_session_id
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail="활성 세션이 없습니다. /session/start를 먼저 호출하세요.",
        )

    ts = datetime.fromisoformat(timestamp) if timestamp else datetime.utcnow()
    image_bytes = await file.read()

    # CAN 스냅샷
    can_snapshot = app_state.can_simulator.get_latest()

    # AI 호출, 실패 시 빈 응답으로 진행 (CAN 기반 평가만)
    ai_response: Optional[AIDetectionResponse] = None
    if app_state.ai_client:
        ai_response = await app_state.ai_client.detect(
            session_id=session_id,
            frame_id=frame_id,
            image_bytes=image_bytes,
            can_snapshot=can_snapshot,
            filename=file.filename or "frame.jpg",
            content_type=file.content_type or "image/jpeg",
        )

    ai_failed = ai_response is None
    ai_objects = ai_response.objects if ai_response else []
    ego_motion = ai_response.ego_motion if ai_response else None
    inference_ms = (ai_response.system.get("inference_time_ms") if ai_response else 0.0) or 0.0
    fps_val = (ai_response.system.get("fps") if ai_response else 0.0) or 0.0
    frame_ref = ai_response.frame_ref if ai_response else f"frame_{frame_id}"

    # 객체별 위험도 평가
    objects_response: list[dict] = []
    max_risk = "safe"
    worst_track_id: Optional[int] = None

    for obj in ai_objects:
        risk_level = app_state.risk_evaluator.assess(
            track_id=obj.track_id,
            depth=obj.depth_val,
            can_data=can_snapshot,
            is_moving=obj.is_moving,
        )
        if _risk_priority(risk_level) > _risk_priority(max_risk):
            max_risk = risk_level
            worst_track_id = obj.track_id

        objects_response.append({
            "class_id": obj.class_id,
            "class_name": obj.class_name,
            "confidence": round(obj.confidence, 3),
            "bbox": obj.bbox.model_dump(),
            "track_id": obj.track_id,
            "depth_val": round(obj.depth_val, 3),
            "bbox_area_ratio": round(obj.bbox_area_ratio, 4),
            "bbox_velocity_x": round(obj.bbox_velocity_x, 3),
            "bbox_velocity_y": round(obj.bbox_velocity_y, 3),
            "obj_speed_px": round(obj.obj_speed_px, 3),
            "is_moving": obj.is_moving,
            "distance_zone": classify_zone(obj.depth_val),
            "risk_level": risk_level,
        })

    # 탐지 로그 저장 (매 프레임)
    log_id = await app_state.repo.save_detection(
        session_id=session_id,
        timestamp=ts,
        frame_number=frame_id,
        object_count=len(ai_objects),
        fps=round(fps_val, 1),
        inference_time_ms=round(inference_ms, 2),
    )

    for obj, obj_resp in zip(ai_objects, objects_response):
        await app_state.repo.save_detected_object(
            log_id=log_id,
            track_id=obj.track_id,
            class_name=obj.class_name,
            confidence=obj.confidence,
            bbox_x=obj.bbox.x,
            bbox_y=obj.bbox.y,
            bbox_w=obj.bbox.w,
            bbox_h=obj.bbox.h,
            depth_value=obj.depth_val,
            distance_zone=classify_zone(obj.depth_val),
            risk_level=obj_resp["risk_level"],
        )

    # 점수 산출 조건: 5프레임 주기 또는 CAN 이벤트 임계 도달
    app_state.frame_counter += 1
    cfg = app_state.scorer.config_cache
    should_score = (
        app_state.frame_counter % SCORE_INTERVAL == 0
        or _is_can_event(can_snapshot, cfg)
    )

    driving_events_out: list[dict] = []
    alerts_out: list[dict] = []
    can_id: Optional[int] = None

    if can_snapshot:
        can_id = await app_state.repo.save_can_data(session_id, can_snapshot)

    if can_snapshot and should_score:
        event = app_state.risk_evaluator.evaluate_driving_event(
            session_id=session_id,
            can_data=can_snapshot,
            risk_level=max_risk,
            track_id=worst_track_id,
        )

        if event:
            event.can_id = can_id
            app_state.scorer.apply_deduction(event)
            event_id = await app_state.repo.save_driving_event(event)
            event.event_id = event_id

            score_record = ScoreRecord(
                session_id=session_id,
                previous_score=app_state.scorer.current_score + event.deduction,
                deduction=event.deduction,
                current_score=app_state.scorer.current_score,
                grade=app_state.scorer.get_grade(),
                event_id=event_id,
            )
            await app_state.repo.save_score(score_record)

            driving_events_out.append({
                "event_type": event.event_type,
                "severity": event.severity,
                "acceleration": event.acceleration,
                "speed": event.speed,
                "deduction": event.deduction,
                "is_proximate": event.is_proximate,
                "track_id": event.track_id,
                "message": _event_message(event.event_type),
            })

            if app_state.scorer.has_grade_changed():
                grade = app_state.scorer.get_grade()
                await app_state.alert_manager.on_grade_change(
                    session_id=session_id,
                    grade=grade,
                    score=app_state.scorer.current_score,
                )
                alerts_out.append({
                    "type": "grade_change",
                    "grade": grade,
                    "score": app_state.scorer.current_score,
                })

    return DetectionApiResponse(
        session_id=session_id,
        frame_id=frame_id,
        frame_ref=frame_ref,
        timestamp=ts,
        system={
            "inference_time_ms": round(inference_ms, 2),
            "fps": round(fps_val, 1),
            "ai_ok": not ai_failed,
        },
        summary={
            "object_count": len(ai_objects),
            "max_risk_level": max_risk.upper(),
            "score_triggered": should_score,
        },
        can=can_snapshot,
        ego_motion=ego_motion,
        objects=objects_response,
        driving_events=driving_events_out,
        alerts=alerts_out,
        score={
            "current_score": app_state.scorer.current_score,
            "grade": app_state.scorer.get_grade(),
        },
        error=(
            None if not ai_failed else {
                "error_type": "ai_unavailable",
                "severity": "warning",
                "message": "AI 서버 응답 실패, CAN 기반 평가만 수행",
            }
        ),
    )


@router.get("/health", response_model=HealthStatus)
async def health():
    """서버 상태"""
    from app.main import app_state
    db_ok = app_state.db.connection is not None
    ai_ok = await app_state.ai_client.ping() if app_state.ai_client else False
    return HealthStatus(
        status="healthy" if db_ok and ai_ok else "degraded",
        model_loaded=ai_ok,
        db_connected=db_ok,
        uptime_seconds=round(time.time() - _start_time, 1),
    )


def _risk_priority(level: str) -> int:
    return {"danger": 3, "warning": 2, "safe": 1}.get(level, 0)


def _is_can_event(can_snapshot: Optional[CANSnapshot],
                  cfg: Optional[ScoringConfig]) -> bool:
    """CAN 이벤트 임계 도달 여부"""
    if not can_snapshot or not cfg:
        return False
    if can_snapshot.acceleration >= cfg.accel_threshold:
        return True
    if can_snapshot.acceleration <= -cfg.brake_threshold:
        return True
    if can_snapshot.speed_kmh >= cfg.speed_limit:
        return True
    return False


def _event_message(event_type: str) -> str:
    messages = {
        "sudden_start": "급출발 감지",
        "sudden_brake": "급제동 감지",
        "overspeeding": "과속 감지",
    }
    return messages.get(event_type, "위험 이벤트 감지")