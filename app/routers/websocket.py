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
    WSFrameResultMessage,
    WSGradeChangeMessage,
    WSSessionClosedMessage,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["WebSocket"])

SCORE_INTERVAL = int(os.getenv("IDA_SCORE_INTERVAL", "5"))
_active_ws: dict[str, WebSocket] = {}


@router.websocket("/ws/detect/{session_id}")
async def ws_detect(websocket: WebSocket, session_id: str):
    """AI 추론 결과 실시간 수신, 운전 이벤트와 등급 변동을 push"""
    from app.main import app_state

    ctx = app_state.sessions.get(session_id)
    if ctx is None:
        await websocket.close(code=1008, reason="Unknown or inactive session")
        return

    # 기존 연결이 살아있으면 강제 종료하고 새 연결로 교체
    prev = _active_ws.get(session_id)
    if prev is not None:
        try:
            await prev.close(code=1001, reason="Replaced by new connection")
        except Exception:
            pass

    await websocket.accept()
    _active_ws[session_id] = websocket

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
                await _handle_detection(websocket, data, ctx)
            elif msg_type == "session_end":
                await _handle_session_end(websocket, ctx)
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
    finally:
        if _active_ws.get(session_id) is websocket:
            _active_ws.pop(session_id, None)


@router.websocket("/ws/dashboard/{session_id}")
async def ws_dashboard(websocket: WebSocket, session_id: str):
    """FE 대시보드 구독 채널 - 프레임 결과를 단방향 수신"""
    from app.main import app_state

    await app_state.dashboard_hub.connect(session_id, websocket)
    try:
        while True:
            # FE는 수신 전용, 연결 유지를 위해 클라이언트 메시지를 대기만 함
            await websocket.receive_text()
    except WebSocketDisconnect:
        app_state.dashboard_hub.disconnect(session_id, websocket)
    except Exception as e:
        logger.exception(f"대시보드 WS 오류: {e}")
        app_state.dashboard_hub.disconnect(session_id, websocket)


async def _handle_detection(websocket: WebSocket, data: dict, ctx) -> None:
    """detection 처리 — AI 소켓에 이벤트 push, FE 대시보드에 프레임 결과 broadcast"""
    from app.main import app_state
    from app.routers.detection_router import build_object_view, _event_message

    session_id = ctx.session_id

    try:
        payload = WSDetectionPayload(**data)
    except ValidationError as e:
        await _send_error(websocket, "INVALID_PAYLOAD", str(e))
        return

    # 세션 종료 여부 재확인
    if session_id not in app_state.sessions:
        await _send_error(websocket, "NO_ACTIVE_SESSION", "세션이 종료됨")
        return

    try:
        ts = _parse_timestamp(payload.timestamp)
        ctx.last_activity = datetime.utcnow()
        # CAN: 사전 적재분 우선, 없으면 시뮬레이터 폴백
        can_snapshot = await app_state.repo.get_can_by_frame(session_id, payload.frame_id)
        can_from_sim = False
        if can_snapshot is None:
            can_snapshot = ctx.can_simulator.get_latest()
            can_from_sim = can_snapshot is not None

        # 객체별 위험도 평가
        max_risk = "safe"
        worst_track_id: Optional[int] = None
        objects_view: list[dict] = []

        for obj in payload.objects:
            risk_level = app_state.risk_evaluator.assess(
                track_id=obj.track_id,
                class_id=obj.class_id,
                depth=obj.depth_val,
                is_moving=obj.is_moving,
            )
            if _risk_priority(risk_level) > _risk_priority(max_risk):
                max_risk = risk_level
                worst_track_id = obj.track_id
            objects_view.append(build_object_view(obj, risk_level))

        # 탐지 로그 저장
        log_id = await app_state.repo.save_detection(
            session_id=session_id,
            timestamp=ts,
            frame_number=payload.frame_id,
            object_count=len(payload.objects),
            fps=round(payload.fps, 1),
            inference_time_ms=round(payload.inference_time_ms, 2),
        )

        for obj, ov in zip(payload.objects, objects_view):
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
                distance_zone=ov["distance_zone"],
                risk_level=ov["risk_level"],
            )

        # 점수 산출 조건
        ctx.frame_counter += 1
        cfg = ctx.scorer.config_cache
        should_score = (
            ctx.frame_counter % SCORE_INTERVAL == 0
            or _is_can_event(can_snapshot, cfg)
        )

        can_id: Optional[int] = None
        if can_snapshot and can_from_sim:
            can_id = await app_state.repo.save_can_data(
                session_id, can_snapshot, frame_number=payload.frame_id
            )

        driving_events_out: list[dict] = []
        alerts_out: list[dict] = []

        if can_snapshot and should_score:
            event = app_state.risk_evaluator.evaluate_driving_event(
                session_id=session_id,
                can_data=can_snapshot,
                risk_level=max_risk,
                track_id=worst_track_id,
            )
            # 동일 유형 이벤트 쿨다운: 연속 프레임 중복 카운트 방지
            if event and not ctx.scorer.is_cooldown_active(
                event.event_type, event.timestamp
            ):
                event.can_id = can_id
                previous_grade = ctx.scorer.get_grade()
                ctx.scorer.apply_deduction(event)
                event_id = await app_state.repo.save_driving_event(event)
                event.event_id = event_id

                score_record = ScoreRecord(
                    session_id=session_id,
                    previous_score=ctx.scorer.current_score + event.deduction,
                    deduction=event.deduction,
                    current_score=ctx.scorer.current_score,
                    grade=ctx.scorer.get_grade(),
                    event_id=event_id,
                )
                await app_state.repo.save_score(score_record)

                event_dict = {
                    "event_type": event.event_type,
                    "severity": event.severity,
                    "speed": event.speed,
                    "acceleration": event.acceleration,
                    "is_proximate": event.is_proximate,
                    "deduction": event.deduction,
                    "track_id": event.track_id,
                    "message": _event_message(event.event_type),
                }
                driving_events_out.append(event_dict)

                # AI 소켓으로 driving_event push
                event_msg = WSDrivingEventMessage(
                    frame_id=payload.frame_id,
                    timestamp=ts.isoformat(),
                    event=event_dict,
                    score={
                        "current_score": ctx.scorer.current_score,
                        "grade": ctx.scorer.get_grade(),
                    },
                )
                await websocket.send_json(event_msg.model_dump())

                # 등급 변동 시 grade_change push
                if ctx.scorer.has_grade_changed():
                    current_grade = ctx.scorer.get_grade()
                    await app_state.alert_manager.on_grade_change(
                        session_id=session_id,
                        grade=current_grade,
                        score=ctx.scorer.current_score,
                    )
                    alerts_out.append({
                        "type": "grade_change",
                        "grade": current_grade,
                        "score": ctx.scorer.current_score,
                    })
                    grade_msg = WSGradeChangeMessage(
                        frame_id=payload.frame_id,
                        timestamp=ts.isoformat(),
                        grade=current_grade,
                        previous_grade=previous_grade,
                        score=ctx.scorer.current_score,
                    )
                    await websocket.send_json(grade_msg.model_dump())

        # FE 대시보드로 프레임 결과 브로드캐스트
        frame_result = WSFrameResultMessage(
            session_id=session_id,
            frame_id=payload.frame_id,
            timestamp=ts.isoformat(),
            objects=objects_view,
            max_risk_level=max_risk.upper(),
            can=can_snapshot,
            driving_events=driving_events_out,
            alerts=alerts_out,
            score={
                "current_score": ctx.scorer.current_score,
                "grade": ctx.scorer.get_grade(),
            },
        )
        await app_state.dashboard_hub.broadcast(
            session_id, frame_result.model_dump(mode="json")
        )

    except Exception as e:
        logger.exception(f"detection 처리 오류: {e}")
        await _send_error(websocket, "INTERNAL", str(e))


async def _handle_session_end(websocket: WebSocket, ctx) -> None:
    """클라이언트 명시적 종료: 세션 정리와 리포트 생성까지 수행"""
    from app.routers.session_router import finalize_session

    result = await finalize_session(ctx.session_id)
    if result is not None:
        final_score = result.get("final_score", ctx.scorer.current_score)
        report_id = result.get("report_id")
    else:
        final_score = ctx.scorer.current_score
        report_id = None

    msg = WSSessionClosedMessage(final_score=final_score, report_id=report_id)
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