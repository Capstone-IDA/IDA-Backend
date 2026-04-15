"""
CANRouter
POST /can/start/{scenario} | POST /can/stop | GET /can/data | GET /can/scenarios
"""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/can", tags=["CAN"])


@router.post("/start/{scenario}")
async def can_start(scenario: str):
    """CAN 시뮬레이션 시작"""
    from app.main import app_state
    try:
        app_state.can_simulator.load_scenario(scenario)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    app_state.can_simulator.start()
    return {"status": "started", "scenario": scenario}


@router.post("/stop")
async def can_stop():
    """CAN 시뮬레이션 중지"""
    from app.main import app_state
    app_state.can_simulator.stop()
    return {"status": "stopped"}


@router.get("/data")
async def can_data():
    """최신 CAN 데이터 조회"""
    from app.main import app_state
    latest = app_state.can_simulator.get_latest()
    if not latest:
        raise HTTPException(status_code=404, detail="CAN 데이터 없음 (시뮬레이터 미실행)")
    return latest


@router.get("/scenarios")
async def can_scenarios():
    """사용 가능한 시나리오 목록"""
    from app.main import app_state
    scenarios = app_state.can_simulator.list_scenarios()
    descs = {
        name: app_state.can_simulator.scenarios[name].description
        for name in scenarios
    }
    return {"scenarios": scenarios, "descriptions": descs}
