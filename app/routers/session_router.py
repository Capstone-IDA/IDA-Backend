"""
SessionRouter
POST /session/start | POST /session/end
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    RentalReport,
    SessionEndRequest,
    SessionStartRequest,
    SessionStartResponse,
)

router = APIRouter(prefix="/session", tags=["Session"])


@router.post("/start", response_model=SessionStartResponse)
async def start_session(req: SessionStartRequest):
    """세션 시작: 런타임 컨텍스트 생성, CAN 시뮬레이터 연동"""
    from app.main import app_state

    session_id = req.session_id or f"sess_{uuid.uuid4().hex[:12]}"

    # DB 세션 생성
    await app_state.repo.create_session(
        session_id=session_id,
        user_id=req.user_id,
        vehicle_id=req.vehicle_id,
        scenario=req.scenario,
        rental_id=req.rental_id,
    )

    # 세션 런타임 컨텍스트 생성 (점수, CAN, 프레임 카운터)
    ctx = app_state.create_session_context(session_id)

    # CAN 시나리오 설정 (선택)
    if req.scenario:
        try:
            ctx.can_simulator.load_scenario(req.scenario)
            ctx.can_simulator.start()
        except ValueError:
            pass  # 알 수 없는 시나리오는 무시

    return SessionStartResponse(
        session_id=session_id,
        start_time=datetime.utcnow(),
        initial_score=100.0,
        status="active",
    )


async def finalize_session(session_id: str) -> Optional[dict]:
    """세션 종료 공통 처리: CAN 중지, 세션 종료, 리포트 생성, 블랙리스트 판정, 컨텍스트 제거.
    세션이 없으면 None, 있으면 report_id가 포함된 리포트 데이터를 반환."""
    from app.main import app_state
    from app.models.schemas import BlacklistRecord

    session = await app_state.repo.get_session(session_id)
    if not session:
        return None

    # 세션 런타임 컨텍스트의 CAN 중지
    ctx = app_state.sessions.get(session_id)
    if ctx:
        ctx.can_simulator.stop()

    # 세션 종료
    await app_state.repo.end_session(session_id)

    # 리포트 생성
    report_data = await app_state.repo.generate_report(session_id)
    if report_data:
        report = RentalReport(**report_data, is_complete=True)
        report_id = await app_state.repo.save_report(report)
        report_data["report_id"] = report_id

        # 블랙리스트 판정
        threshold = app_state.config.blacklist_threshold if app_state.config else 30
        if report.final_score <= threshold:
            bl_record = BlacklistRecord(
                user_id=report.user_id,
                session_id=session_id,
                final_score=report.final_score,
                blacklist_grade="blacklisted",
            )
            await app_state.repo.save_blacklist(bl_record)

    # 런타임 컨텍스트 제거
    app_state.sessions.pop(session_id, None)

    return report_data


@router.post("/end")
async def end_session(req: SessionEndRequest):
    """세션 종료: CAN 중지 + 리포트 자동 생성"""
    result = await finalize_session(req.session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

    return {
        "session_id": req.session_id,
        "end_time": datetime.utcnow().isoformat(),
        "final_score": result.get("final_score", 100),
        "grade": result.get("final_grade", "Green"),
        "report_generated": result.get("report_id") is not None,
    }
