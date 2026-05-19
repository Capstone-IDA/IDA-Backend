"""
WebSocketRouter
/ws/detect/{session_id}
AI 추론 결과 실시간 수신, 이벤트성 push (P 패턴)
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.core.distance import classify_zone
from app.models.schemas import (
    CANSnapshot,
    ScoreRecord,
    ScoringConfig,
    WSConnectedMessage,
    WSDetectionPayload,
    WSDrivingEventMessage,
    WSErrorMessage,
    WSGradeChangeMessage,
    WSSessionClosedMessage,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["WebSocket"])

SCORE_INTERVAL = int(os.getenv("IDA_SCORE_INTERVAL", "5"))


@router.websocket("/ws/detect/{session_id}")
async def ws_detect(websocket: WebSocket, session_id: str):
    """AI 추론 결과 실시간 수신, 운전 이벤트와 등급 변동을 push"""
    from app.main import app_state

    # 활성 세션 검증 (session_id가 UUID라 외부 추측 어려움)
    active = app_state.active_session_id
    if not active:
        await websocket.close(code=1008, reason="No active session")
        return
    if session_id != active:
        await websocket.close(code=1008, reason="Session id mismatch")
        return

    # 핸드셰이크 수락
    await websocket.accept()

    connected = WSConnectedMessage(
        session_id=session_id,
        server_time=datetime.utcnow().isoformat(),
    )
    await websocket.send_json(connected.model_dump())

    logger.info(f"WS 연결 수립: session={session_id}")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await _send_error(websocket, "INVALID_PAYLOAD", "JSON 파싱 실패")
                continue

            msg_type = data.get("type")

            if msg_type == "detection":
                await _handle_detection(websocket, data, session_id)
            elif msg_type == "session_end":
                await _handle_session_end(websocket, session_id)
                break
            else:
                await _send_error(
                    websocket,
                    "INVALID_PAYLOAD",
                    f"알 수 없는 메시지 타입: {msg_type}",
                )

    except WebSocketDisconnect:
        logger.info(f"WS 클라이언트 연결 종료: session={session_id}")
    except Exception as e:
        logger.exception(f"WS 처리 오류: {e}")
        try:
            await websocket.close(code=1011, reason="Internal error")
        except Exception:
            pass


async def _handle_detection(websocket: WebSocket, data: dict, session_id: str) -> None:
    """detection 메시지 처리 — 이벤트 발생/등급 변동 시에만 push"""
    from app.main import app_state

    try:
        payload = WSDetectionPayload(**data)
    except ValidationError as e:
        await _send_error(websocket, "INVALID_PAYLOAD", str(e))
        return

    # 활성 세션 재확인
    if app_state.active_session_id != session_id:
        await _send_error(websocket, "NO_ACTIVE_SESSION", "활성 세션이 변경됨")
        return

    try:
        ts = _parse_timestamp(payload.timestamp)
        can_snapshot = app_state.can_simulator.get_latest()

        # 객체별 위험도 평가
        max_risk = "safe"
        worst_track_id: Optional[int] = None
        risks_for_save: list[tuple] = []

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
            risks_for_save.append((obj, risk_level, classify_zone(obj.depth_val)))

        # 탐지 로그 저장
        log_id = await app_state.repo.save_detection(
            session_id=session_id,
            timestamp=ts,
            frame_number=payload.frame_id,
            object_count=len(payload.objects),
            fps=round(payload.fps, 1),
            inference_time_ms=round(payload.inference_time_ms, 2),
        )

        for obj, risk_level, dist_zone in risks_for_save:
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
                distance_zone=dist_zone,
                risk_level=risk_level,
            )

        # 점수 산출 조건
        app_state.frame_counter += 1
        cfg = app_state.scorer.config_cache
        should_score = (
            app_state.frame_counter % SCORE_INTERVAL == 0
            or _is_can_event(can_snapshot, cfg)
        )

        can_id: Optional[int] = None
        if can_snapshot:
            can_id = await app_state.repo.save_can_data(session_id, can_snapshot)

        if not (can_snapshot and should_score):
            return

        event = app_state.risk_evaluator.evaluate_driving_event(
            session_id=session_id,
            can_data=can_snapshot,
            risk_level=max_risk,
            track_id=worst_track_id,
        )
        if event is None:
            return

        event.can_id = can_id
        previous_grade = app_state.scorer.get_grade()
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

        # driving_event push
        event_msg = WSDrivingEventMessage(
            frame_id=payload.frame_id,
            timestamp=ts.isoformat(),
            event={
                "event_type": event.event_type,
                "severity": event.severity,
                "speed": event.speed,
                "acceleration": event.acceleration,
                "is_proximate": event.is_proximate,
                "deduction": event.deduction,
                "track_id": event.track_id,
            },
            score={
                "current_score": app_state.scorer.current_score,
                "grade": app_state.scorer.get_grade(),
            },
        )
        await websocket.send_json(event_msg.model_dump())

        # 등급 변동 시 grade_change push
        if app_state.scorer.has_grade_changed():
            current_grade = app_state.scorer.get_grade()
            await app_state.alert_manager.on_grade_change(
                session_id=session_id,
                grade=current_grade,
                score=app_state.scorer.current_score,
            )
            grade_msg = WSGradeChangeMessage(
                frame_id=payload.frame_id,
                timestamp=ts.isoformat(),
                grade=current_grade,
                previous_grade=previous_grade,
                score=app_state.scorer.current_score,
            )
            await websocket.send_json(grade_msg.model_dump())

    except Exception as e:
        logger.exception(f"detection 처리 오류: {e}")
        await _send_error(websocket, "INTERNAL", str(e))


async def _handle_session_end(websocket: WebSocket, session_id: str) -> None:
    """클라이언트 명시적 종료. 실제 세션 정리/리포트 생성은 별도 /session/end로 처리."""
    from app.main import app_state

    msg = WSSessionClosedMessage(
        final_score=app_state.scorer.current_score,
        report_id=None,
    )
    await websocket.send_json(msg.model_dump())
    await websocket.close(code=1000, reason="Session ended by client")


async def _send_error(websocket: WebSocket, code: str, message: str) -> None:
    """에러 메시지 전송 (연결은 유지)"""
    msg = WSErrorMessage(code=code, message=message)
    try:
        await websocket.send_json(msg.model_dump())
    except Exception:
        pass


def _parse_timestamp(ts_str: str) -> datetime:
    """ISO 8601 안전 파싱, 실패 시 현재 시각"""
    try:
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
