"""
ScoringRouter
GET /score/{session_id} | GET /score/{session_id}/timeline | GET /events/{session_id}
"""

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["Scoring"])


@router.get("/score/{session_id}")
async def get_score(session_id: str):
    """현재 점수 조회"""
    from app.main import app_state
    record = await app_state.repo.get_driver_score(session_id)
    if not record:
        # 아직 이벤트 없으면 초기 점수 반환
        session = await app_state.repo.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")
        return {"session_id": session_id, "current_score": 100.0, "grade": "Green"}
    return {
        "session_id": session_id,
        "current_score": record["current_score"],
        "grade": record["grade"],
    }


@router.get("/score/{session_id}/timeline")
async def get_score_timeline(session_id: str):
    """점수 타임라인 조회"""
    from app.main import app_state
    timeline = await app_state.repo.get_score_timeline(session_id)
    return {"session_id": session_id, "timeline": timeline}


@router.get("/events/{session_id}")
async def get_events(session_id: str):
    """세션별 운전 이벤트 조회"""
    from app.main import app_state
    events = await app_state.repo.get_events_by_session(session_id)
    return {"session_id": session_id, "events": events}
