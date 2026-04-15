"""
DetectionRouter
POST /detect | GET /health
"""

import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.models.schemas import (
    AlertRecord,
    DetectionApiResponse,
    ErrorResponse,
    HealthStatus,
    ScoreRecord,
)

router = APIRouter(tags=["Detection"])

_start_time = time.time()


@router.post("/detect", response_model=DetectionApiResponse)
async def detect(
    file: UploadFile = File(...),
    camera_id: str = Form(default="1"),
    frame_id: int = Form(default=0),
    timestamp: Optional[str] = Form(default=None),
):
    """
    영상 프레임 수신 → 추론 파이프라인 실행 → 결과 반환
    AI 코어 (M1/M2) 모듈이 합류 전이므로,
    현재는 CAN 기반 운전 이벤트 판정 + 스코어링 파이프라인만 실행
    """
    from app.main import app_state

    session_id = app_state.active_session_id
    if not session_id:
        raise HTTPException(status_code=400, detail="활성 세션이 없습니다. /session/start를 먼저 호출하세요.")

    ts = datetime.fromisoformat(timestamp) if timestamp else datetime.utcnow()
    t0 = time.perf_counter()

    # ── 영상 수신 (AI 코어 합류 시 여기서 추론) ──
    image_bytes = await file.read()

    # 현재는 stub: 객체 탐지 결과 없음 (AI 모듈 미연결)
    detected_objects = []
    depth_executed = False
    inference_ms = (time.perf_counter() - t0) * 1000

    # ── CAN 데이터 수신 ──
    can_snapshot = app_state.can_simulator.get_latest()
    can_dict = None
    if can_snapshot:
        can_dict = can_snapshot

    # ── 운전 이벤트 판정 ──
    driving_events_out = []
    alerts_out = []
    max_risk = "safe"

    if can_snapshot:
        # CAN 데이터 저장
        can_id = await app_state.repo.save_can_data(session_id, can_snapshot)

        # 위험 이벤트 평가 (현재는 객체 없으므로 risk_level="safe")
        event = app_state.risk_evaluator.evaluate_driving_event(
            session_id=session_id,
            can_data=can_snapshot,
            risk_level="safe",  # AI 합류 후 실제 risk_level 반영
            track_id=None,
        )

        if event:
            event.can_id = can_id
            # 스코어 감점
            app_state.scorer.apply_deduction(event)
            event_id = await app_state.repo.save_driving_event(event)

            # 점수 기록
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

    # ── 탐지 로그 저장 ──
    log_id = await app_state.repo.save_detection(
        session_id=session_id,
        timestamp=ts,
        frame_number=frame_id,
        object_count=len(detected_objects),
        fps=round(1000 / max(inference_ms, 1), 1),
        inference_time_ms=round(inference_ms, 2),
    )

    # ── 응답 구성 (요구사항분석서 인터페이스 규격) ──
    fps = round(1000 / max(inference_ms, 1), 1)

    return DetectionApiResponse(
        frame_id=frame_id,
        timestamp=ts,
        system={
            "inference_ms": round(inference_ms, 2),
            "fps": fps,
            "depth_executed": depth_executed,
        },
        summary={
            "object_count": len(detected_objects),
            "max_risk_level": max_risk.upper(),
        },
        can=can_dict,
        objects=detected_objects,
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
    """서버 상태 확인"""
    from app.main import app_state
    db_ok = app_state.db.connection is not None
    return HealthStatus(
        status="healthy" if db_ok else "degraded",
        model_loaded=False,  # AI 모듈 합류 시 업데이트
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
