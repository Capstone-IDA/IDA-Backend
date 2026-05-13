"""
DetectionRouter
POST /detect | GET /health
AI 서버가 보낸 추론 결과를 받아 점수 산출
"""

import logging
import os
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.core.distance import classify_zone
from app.models.schemas import (
    AIDetectionPayload,
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
async def detect(payload: AIDetectionPayload):
    """AI 추론 결과 수신, 조건부 점수 산출"""
    from app.main import app_state

    # 활성 세션 확인
    active_session = app_state.active_session_id
    if not active_session:
        raise HTTPException(
            status_code=400,
            detail="활성 세션이 없습니다. /session/start를 먼저 호출하세요.",
        )

    # session_id 불일치는 경고만, BE 세션을 신뢰
    if payload.session_id != active_session:
        logger.warning(
            f"session_id 불일치: payload={payload.session_id}, "
            f"active={active_session}"
        )
    session_id = active_session

    # timestamp 안전 파싱
    ts = _parse_timestamp(payload.timestamp)

    # CAN 스냅샷 (BE가 자체 보유)
    can_snapshot = app_state.can_simulator.get_latest()

    # 객체별 위험도 평가
    objects_response: list[dict] = []
    max_risk = "safe"
    worst_track_id: Optional[int] = None

    for obj in payload.objects:
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
        frame_number=payload.frame_id,
        object_count=len(payload.objects),
        fps=round(payload.fps, 1),
        inference_time_ms=round(payload.inference_time_ms, 2),
    )

    for obj, obj_resp in zip(payload.objects, objects_response):
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
        frame_id=payload.frame_id,
        frame_ref=f"frame_{payload.frame_id}",
        timestamp=ts,
        system={
            "inference_time_ms": round(payload.inference_time_ms, 2),
            "fps": round(payload.fps, 1),
        },
        summary={
            "object_count": len(payload.objects),
            "max_risk_level": max_risk.upper(),
            "score_triggered": should_score,
        },
        can=can_snapshot,
        ego_motion=payload.ego_motion,
        objects=objects_response,
        driving_events=driving_events_out,
        alerts=alerts_out,
        score={
            "current_score": app_state.scorer.current_score,
            "grade": app_state.scorer.get_grade(),
        },
        error=None,
    )


@router.get("/health", response_model=HealthStatus)
async def health():
    """서버 상태"""
    from app.main import app_state
    db_ok = app_state.db.connection is not None
    return HealthStatus(
        status="healthy" if db_ok else "degraded",
        model_loaded=True,
        db_connected=db_ok,
        uptime_seconds=round(time.time() - _start_time, 1),
    )


def _parse_timestamp(ts_str: str) -> datetime:
    """ISO 8601 안전 파싱, 실패 시 현재 시각"""
    try:
        # 'Z' 접미사를 Python이 이해하는 형식으로
        normalized = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except (ValueError, AttributeError):
        return datetime.utcnow()


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