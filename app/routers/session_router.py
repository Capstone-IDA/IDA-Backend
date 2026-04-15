"""
SessionRouter
POST /session/start | POST /session/end
"""

import uuid
from datetime import datetime

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
    """세션 시작: 스코어 초기화, CAN 시뮬레이터 연동"""
    from app.main import app_state

    session_id = f"sess_{uuid.uuid4().hex[:12]}"

    # DB 세션 생성
    await app_state.repo.create_session(
        session_id=session_id,
        user_id=req.user_id,
        vehicle_id=req.vehicle_id,
        scenario=req.scenario,
    )

    # 스코어 초기화
    app_state.scorer.reset(session_id)

    # 설정 캐시 로드
    config_row = await app_state.repo.get_config()
    if config_row:
        from app.models.schemas import ScoringConfig
        cfg = ScoringConfig(**{k: v for k, v in config_row.items() if k in ScoringConfig.model_fields})
        app_state.scorer.reload_config(cfg)
        app_state.risk_evaluator.reload_config(cfg)
        app_state.alert_manager.min_interval_sec = cfg.alert_min_interval_sec

    # 현재 활성 세션 기록
    app_state.active_session_id = session_id

    # CAN 시나리오 설정 (선택)
    if req.scenario:
        try:
            app_state.can_simulator.load_scenario(req.scenario)
        except ValueError:
            pass  # 알 수 없는 시나리오는 무시

    return SessionStartResponse(
        session_id=session_id,
        start_time=datetime.utcnow(),
        initial_score=100.0,
        status="active",
    )


@router.post("/end")
async def end_session(req: SessionEndRequest):
    """세션 종료: CAN 중지 + 리포트 자동 생성"""
    from app.main import app_state

    session = await app_state.repo.get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

    # CAN 중지
    app_state.can_simulator.stop()

    # 세션 종료
    await app_state.repo.end_session(req.session_id)

    # 리포트 생성
    report_data = await app_state.repo.generate_report(req.session_id)
    if report_data:
        report = RentalReport(
            **report_data,
            is_complete=True,
        )
        await app_state.repo.save_report(report)

        # 블랙리스트 판정
        from app.models.schemas import BlacklistRecord
        cfg = app_state.scorer.config_cache
        threshold = cfg.blacklist_threshold if cfg else 30

        if report.final_score <= threshold:
            bl_record = BlacklistRecord(
                user_id=report.user_id,
                session_id=req.session_id,
                final_score=report.final_score,
                blacklist_grade="blacklisted",
            )
            await app_state.repo.save_blacklist(bl_record)

    # 활성 세션 해제
    if app_state.active_session_id == req.session_id:
        app_state.active_session_id = None

    return {
        "session_id": req.session_id,
        "end_time": datetime.utcnow().isoformat(),
        "final_score": report_data["final_score"] if report_data else 100,
        "grade": report_data["final_grade"] if report_data else "Green",
        "report_generated": report_data is not None,
    }
