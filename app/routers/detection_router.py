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
    NotificationLog,
    ScoreRecord,
    ScoringConfig,
    WSFrameResultMessage,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Detection"])

_start_time = time.time()
SCORE_INTERVAL = int(os.getenv("IDA_SCORE_INTERVAL", "5"))


@router.post("/detect", response_model=DetectionApiResponse)
async def detect(payload: AIDetectionPayload):
    """AI 추론 결과 수신, 조건부 점수 산출"""
    from app.main import app_state

    # 세션 런타임 컨텍스트 조회
    ctx = app_state.sessions.get(payload.session_id)
    if ctx is None:
        raise HTTPException(
            status_code=404,
            detail="세션을 찾을 수 없습니다. /session/start를 먼저 호출하세요.",
        )
    session_id = ctx.session_id
    ctx.last_activity = datetime.utcnow()

    # timestamp 안전 파싱
    ts = _parse_timestamp(payload.timestamp)

    # CAN 우선순위: 사전 적재(frame 정확 매칭) 우선, 없으면 라이브 시뮬레이터 폴백
    can_snapshot = await app_state.repo.get_can_by_frame(session_id, payload.frame_id)
    can_from_sim = False
    if can_snapshot is None:
        can_snapshot = ctx.can_simulator.get_latest()
        can_from_sim = can_snapshot is not None

    # 객체별 위험도 평가
    objects_response: list[dict] = []
    max_risk = "safe"
    worst_track_id: Optional[int] = None

    for obj in payload.objects:
        risk_level = app_state.risk_evaluator.assess(
            track_id=obj.track_id,
            class_id=obj.class_id,
            depth=obj.depth_val,
            is_moving=obj.is_moving,
            bbox_area_ratio=obj.bbox_area_ratio,
            bbox_h=obj.bbox.h,
            bbox_y2=obj.bbox.y + obj.bbox.h,
        )
        if _risk_priority(risk_level) > _risk_priority(max_risk):
            max_risk = risk_level
            worst_track_id = obj.track_id

        objects_response.append(build_object_view(obj, risk_level))

    # 프레임 단위 추돌 경고 갱신: danger 객체가 연속 유지되면 활성, FE 배너 신호
    collision_active, collision_notify = app_state.risk_evaluator.update_collision_state(
        session_id, max_risk == "danger", payload.frame_id
    )

    # 탐지 로그 저장 (매 프레임)
    log_id = await app_state.repo.save_detection(
        session_id=session_id,
        timestamp=ts,
        frame_number=payload.frame_id,
        object_count=len(payload.objects),
        fps=round(payload.fps, 1),
        inference_time_ms=round(payload.inference_time_ms, 2),
        collision_warning=collision_active,
    )

    # 프레임 이미지 저장 (AI가 base64로 실어서 보낸 경우)
    if payload.frame_image_b64:
        import base64
        try:
            image_bytes = base64.b64decode(payload.frame_image_b64)
            await app_state.repo.save_frame_image(
                session_id=session_id,
                frame_number=payload.frame_id,
                image_data=image_bytes,
                log_id=log_id,
            )
        except Exception as e:
            logger.warning(f"프레임 이미지 저장 실패 frame={payload.frame_id}: {e}")
            
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
    ctx.frame_counter += 1
    cfg = ctx.scorer.config_cache
    should_score = (
        ctx.frame_counter % SCORE_INTERVAL == 0
        or _is_can_event(can_snapshot, cfg)
    )

    driving_events_out: list[dict] = []
    alerts_out: list[dict] = []
    can_id: Optional[int] = None

    # 추돌 경고 신규 진입 시 알림 1건 기록 (에피소드당 1회, 최소 간격 적용)
    if collision_notify:
        alerts_out.append({
            "type": "collision_warning",
            "frame_id": payload.frame_id,
            "track_id": worst_track_id,
            "message": "추돌 주의: 근접 장애물 감지",
        })
        try:
            company_id = await app_state.repo.get_session_company(session_id)
            await app_state.repo.save_notification(NotificationLog(
                session_id=session_id,
                timestamp=ts,
                grade=ctx.scorer.get_grade(),
                score=ctx.scorer.current_score,
                notification_type="collision_warning",
                company_id=company_id,
                status="sent",
                retry_count=0,
            ))
        except Exception as e:
            logger.warning(f"추돌 경고 알림 저장 실패 frame={payload.frame_id}: {e}")

    # 시뮬레이터가 새로 생성한 CAN만 저장 (적재분은 이미 DB에 있으므로 중복 저장 방지)
    if can_snapshot and can_from_sim:
        can_id = await app_state.repo.save_can_data(
            session_id, can_snapshot, frame_number=payload.frame_id
        )
    
    if can_snapshot and should_score:
        event = app_state.risk_evaluator.evaluate_driving_event(
            session_id=session_id,
            can_data=can_snapshot,
            risk_level=max_risk,
            track_id=worst_track_id,
        )

        # 동일 유형 이벤트 쿨다운: 같은 급제동/급출발이 연속 프레임에서 중복 카운트되는 것 방지
        if event and not ctx.scorer.is_cooldown_active(
            event.event_type, event.timestamp
        ):
            event.can_id = can_id
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

            if ctx.scorer.has_grade_changed():
                grade = ctx.scorer.get_grade()
                await app_state.alert_manager.on_grade_change(
                    session_id=session_id,
                    grade=grade,
                    score=ctx.scorer.current_score,
                )
                alerts_out.append({
                    "type": "grade_change",
                    "grade": grade,
                    "score": ctx.scorer.current_score,
                })

    # FE 대시보드로 프레임 결과 브로드캐스트
    frame_result = WSFrameResultMessage(
        session_id=session_id,
        frame_id=payload.frame_id,
        timestamp=ts.isoformat(),
        objects=objects_response,
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
            "collision_warning": collision_active,
        },
        can=can_snapshot,
        ego_motion=payload.ego_motion,
        objects=objects_response,
        driving_events=driving_events_out,
        alerts=alerts_out,
        score={
            "current_score": ctx.scorer.current_score,
            "grade": ctx.scorer.get_grade(),
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


def build_object_view(obj, risk_level: str) -> dict:
    """탐지 객체를 응답/브로드캐스트용 dict로 변환"""
    return {
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
    }