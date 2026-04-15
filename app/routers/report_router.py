"""
ReportRouter
POST /report/{session_id} | GET /report/{session_id} | GET /reports/{user_id}
"""

from fastapi import APIRouter, HTTPException

from app.models.schemas import RentalReport

router = APIRouter(tags=["Report"])


@router.post("/report/{session_id}")
async def create_report(session_id: str):
    """리포트 수동 생성"""
    from app.main import app_state
    data = await app_state.repo.generate_report(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

    report = RentalReport(**data, is_complete=True)
    await app_state.repo.save_report(report)
    return report


@router.get("/report/{session_id}")
async def get_report(session_id: str):
    """리포트 조회"""
    from app.main import app_state
    row = await app_state.repo.get_report(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="리포트를 찾을 수 없습니다")
    return row


@router.get("/reports/{user_id}")
async def get_user_reports(user_id: str):
    """사용자별 리포트 목록"""
    from app.main import app_state
    return await app_state.repo.get_reports_by_user(user_id)
