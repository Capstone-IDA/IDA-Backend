"""
CompanyRouter (CompanyDashboard 전용)
로그인한 업체의 데이터만 조회 가능
모든 엔드포인트에 company_id 자동 필터링 적용
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.auth_dependency import AuthContext, require_company_or_admin

router = APIRouter(prefix="/company", tags=["CompanyDashboard"])


@router.get("/dashboard")
async def company_dashboard(auth: AuthContext = Depends(require_company_or_admin)):
    """업체 대시보드 요약 데이터"""
    from app.main import app_state
    company_id = auth.get_company_filter()
    stats = await app_state.repo.get_stats_by_company(company_id)
    return {
        "company_id": auth.company_id,
        **stats,
    }


@router.get("/sessions")
async def company_sessions(
    status: Optional[str] = Query(None, description="active / completed"),
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_company_or_admin),
):
    """업체별 세션 목록"""
    from app.main import app_state
    company_id = auth.get_company_filter()
    return await app_state.repo.get_sessions_by_company(
        company_id=company_id, status=status, limit=limit
    )


@router.get("/events")
async def company_events(
    limit: int = Query(100, ge=1, le=500),
    auth: AuthContext = Depends(require_company_or_admin),
):
    """업체별 운전 이벤트"""
    from app.main import app_state
    company_id = auth.get_company_filter()
    return await app_state.repo.get_events_by_company(
        company_id=company_id, limit=limit
    )


@router.get("/reports")
async def company_reports(
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_company_or_admin),
):
    """업체별 반납 리포트"""
    from app.main import app_state
    company_id = auth.get_company_filter()
    return await app_state.repo.get_reports_by_company(
        company_id=company_id, limit=limit
    )


@router.get("/blacklist")
async def company_blacklist(
    grade: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_company_or_admin),
):
    """업체별 블랙리스트"""
    from app.main import app_state
    company_id = auth.get_company_filter()
    return await app_state.repo.get_blacklist_by_company(
        company_id=company_id, grade=grade,
        is_active=is_active, limit=limit
    )


@router.get("/scores")
async def company_scores(
    limit: int = Query(100, ge=1, le=500),
    auth: AuthContext = Depends(require_company_or_admin),
):
    """업체별 점수 이력"""
    from app.main import app_state
    company_id = auth.get_company_filter()
    return await app_state.repo.get_scores_by_company(
        company_id=company_id, limit=limit
    )


@router.get("/notifications")
async def company_notifications(
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_company_or_admin),
):
    """업체별 알림 이력"""
    from app.main import app_state
    company_id = auth.get_company_filter()
    return await app_state.repo.get_notifications_by_company(
        company_id=company_id, limit=limit
    )
