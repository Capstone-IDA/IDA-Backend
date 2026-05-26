"""
CANRouter
POST /can/{session_id}/start/{scenario} | POST /can/{session_id}/stop
GET /can/{session_id}/data | GET /can/scenarios
"""

from fastapi import APIRouter, HTTPException

from app.services.can_simulator import DEFAULT_SCENARIOS

router = APIRouter(prefix="/can", tags=["CAN"])


@router.get("/scenarios")
async def can_scenarios():
    """사용 가능한 시나리오 목록"""
    return {
        "scenarios": list(DEFAULT_SCENARIOS.keys()),
        "descriptions": {
            name: preset.description
            for name, preset in DEFAULT_SCENARIOS.items()
        },
    }


@router.post("/{session_id}/start/{scenario}")
async def can_start(session_id: str, scenario: str):
    """세션의 CAN 시뮬레이션 시작 (시나리오 교체 포함)"""
    from app.main import app_state

    ctx = app_state.sessions.get(session_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

    try:
        ctx.can_simulator.load_scenario(scenario)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ctx.can_simulator.start()
    return {"status": "started", "scenario": scenario, "session_id": session_id}


@router.post("/{session_id}/stop")
async def can_stop(session_id: str):
    """세션의 CAN 시뮬레이션 중지"""
    from app.main import app_state

    ctx = app_state.sessions.get(session_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

    ctx.can_simulator.stop()
    return {"status": "stopped", "session_id": session_id}


@router.get("/{session_id}/data")
async def can_data(session_id: str):
    """세션의 최신 CAN 데이터 조회"""
    from app.main import app_state

    ctx = app_state.sessions.get(session_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

    latest = ctx.can_simulator.get_latest()
    if not latest:
        raise HTTPException(status_code=404, detail="CAN 데이터 없음 (시뮬레이터 미실행)")
    return latest
