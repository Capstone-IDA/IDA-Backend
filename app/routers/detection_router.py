"""
DetectionRouter
POST /detect | GET /health
"""

import logging
import time
from datetime import datetime
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.models.schemas import (
    DetectionApiResponse,
    HealthStatus,
    ScoreRecord,
)
from app.services.vision.depth_stub import DepthStub

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Detection"])

_start_time = time.time()


@router.post("/detect", response_model=DetectionApiResponse)
async def detect(
    file: UploadFile = File(...),
    camera_id: str = Form(default="1"),
    frame_id: int = Form(default=0),
    timestamp: Optional[str] = Form(default=None),
):
    """영상 프레임 수신 -> 추론 + 운전 평가 + DB 저장"""
    from app.main import app_state

    session_id = app_state.active_session_id
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail="활성 세션이 없습니다. /session/start를 먼저 호출하세요.",
        )

    ts = datetime.fromisoformat(timestamp) if timestamp else datetime.utcnow()

    # 이미지 디코드
    image_bytes = await file.read()
    nparr = np.frombuffer(image_bytes, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    decode_failed = frame is None

    # CAN 스냅샷
    can_snapshot = app_state.can_simulator.get_latest()

    # 추론 (디코드 실패 시 빈 결과로 진행)
    if decode_failed:
        logger.warning(f"이미지 디코드 실패 (frame_id={frame_id})")
        tracked_objects = []
        inference_ms = 0.0
        depth_executed = False
    else:
        tracked_objects, inference_ms, depth_executed = app_state.pipeline.process(
            frame=frame,
            risk_evaluator=app_state.risk_evaluator,
            can_snapshot=can_snapshot,
        )

    max_risk = app_state.pipeline.get_worst_risk(tracked_objects)

    # CAN 데이터 저장 + 운전 이벤트 판정
    driving_events_out: list[dict] = []
    alerts_out: list[dict] = []
    can_id: Optional[int] = None

    if can_snapshot:
        can_id = await app_state.repo.save_can_data(session_id, can_snapshot)

        # 가장 위험한 객체 기준 track_id
        worst_track_id: Optional[int] = None
        for t in tracked_objects:
            if t.risk_level == max_risk and max_risk != "safe":
                worst_track_id = t.track_id
                break

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

            # 등급 변동 시 알림
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

    # 탐지 로그 저장
    fps = round(1000 / max(inference_ms, 1), 1)
    log_id = await app_state.repo.save_detection(
        session_id=session_id,
        timestamp=ts,
        frame_number=frame_id,
        object_count=len(tracked_objects),
        fps=fps,
        inference_time_ms=round(inference_ms, 2),
    )

    # 탐지 객체 저장
    for t in tracked_objects:
        await app_state.repo.save_detected_object(
            log_id=log_id,
            track_id=t.track_id,
            class_name=t.detection.class_name,
            confidence=t.detection.confidence,
            bbox_x=t.smoothed_bbox.x,
            bbox_y=t.smoothed_bbox.y,
            bbox_w=t.smoothed_bbox.w,
            bbox_h=t.smoothed_bbox.h,
            depth_value=t.depth,
            distance_zone=DepthStub.classify_zone(t.depth),
            risk_level=t.risk_level,
        )

    # 응답 구성
    objects_response = [
        {
            "track_id": t.track_id,
            "class_id": t.detection.class_id,
            "class_name": t.detection.class_name,
            "confidence": round(t.detection.confidence, 3),
            "bbox": {
                "x": round(t.smoothed_bbox.x, 4),
                "y": round(t.smoothed_bbox.y, 4),
                "w": round(t.smoothed_bbox.w, 4),
                "h": round(t.smoothed_bbox.h, 4),
            },
            "depth": round(t.depth, 3),
            "distance_zone": DepthStub.classify_zone(t.depth),
            "risk_level": t.risk_level,
        }
        for t in tracked_objects
    ]

    return DetectionApiResponse(
        frame_id=frame_id,
        timestamp=ts,
        system={
            "inference_ms": round(inference_ms, 2),
            "fps": fps,
            "depth_executed": depth_executed,
            "model_loaded": app_state.pipeline.is_loaded,
        },
        summary={
            "object_count": len(tracked_objects),
            "max_risk_level": max_risk.upper(),
        },
        can=can_snapshot,
        objects=objects_response,
        driving_events=driving_events_out,
        alerts=alerts_out,
        score={
            "current_score": app_state.scorer.current_score,
            "grade": app_state.scorer.get_grade(),
        },
        error=(
            None
            if not decode_failed
            else {
                "error_type": "image_decode_failed",
                "severity": "warning",
                "message": "이미지 디코드 실패 - 객체 탐지를 건너뛰고 CAN 기반 평가만 수행",
            }
        ),
    )


@router.get("/health", response_model=HealthStatus)
async def health():
    """서버 상태 확인"""
    from app.main import app_state
    db_ok = app_state.db.connection is not None
    model_ok = bool(app_state.pipeline and app_state.pipeline.is_loaded)
    return HealthStatus(
        status="healthy" if db_ok else "degraded",
        model_loaded=model_ok,
        db_connected=db_ok,
        uptime_seconds=round(time.time() - _start_time, 1),
    )


def _event_message(event_type: str) -> str:
    messages = {
        "sudden_start": "급출발 감지",
        "sudden_brake": "급제동 감지",
        "overspeeding": "과속 감지",
    }
    return messages.get(event_type, "위험 이벤트 감지")
