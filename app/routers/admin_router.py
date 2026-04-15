"""
AdminRouter (AdminDashboard 전용)
관리자 전용 — 업체별 필터링 조회 + 시스템 전체 통계
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.auth_dependency import AuthContext, require_admin

router = APIRouter(prefix="/admin", tags=["AdminDashboard"])


@router.get("/dashboard")
async def admin_dashboard(
    company_id: Optional[str] = Query(None, description="업체별 필터 (없으면 전체)"),
    auth: AuthContext = Depends(require_admin),
):
    """관리자 대시보드 요약 (업체별 필터링 가능)"""
    from app.main import app_state
    stats = await app_state.repo.get_stats_by_company(company_id)
    companies = await app_state.repo.get_all_companies()
    return {
        "filter_company_id": company_id,
        "companies": companies,
        **stats,
    }


@router.get("/sessions")
async def admin_sessions(
    company_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    auth: AuthContext = Depends(require_admin),
):
    """전체/업체별 세션 조회"""
    from app.main import app_state
    return await app_state.repo.get_sessions_by_company(
        company_id=company_id, status=status, limit=limit
    )


@router.get("/events")
async def admin_events(
    company_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    auth: AuthContext = Depends(require_admin),
):
    """전체/업체별 이벤트 조회"""
    from app.main import app_state
    return await app_state.repo.get_events_by_company(
        company_id=company_id, limit=limit
    )


@router.get("/reports")
async def admin_reports(
    company_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    auth: AuthContext = Depends(require_admin),
):
    """전체/업체별 리포트 조회"""
    from app.main import app_state
    return await app_state.repo.get_reports_by_company(
        company_id=company_id, limit=limit
    )


@router.get("/blacklist")
async def admin_blacklist(
    company_id: Optional[str] = Query(None),
    grade: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    auth: AuthContext = Depends(require_admin),
):
    """전체/업체별 블랙리스트 조회"""
    from app.main import app_state
    return await app_state.repo.get_blacklist_by_company(
        company_id=company_id, grade=grade,
        is_active=is_active, limit=limit
    )


@router.get("/scores")
async def admin_scores(
    company_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    auth: AuthContext = Depends(require_admin),
):
    """전체/업체별 점수 이력"""
    from app.main import app_state
    return await app_state.repo.get_scores_by_company(
        company_id=company_id, limit=limit
    )


@router.get("/notifications")
async def admin_notifications(
    company_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    auth: AuthContext = Depends(require_admin),
):
    """전체/업체별 알림 이력"""
    from app.main import app_state
    return await app_state.repo.get_notifications_by_company(
        company_id=company_id, limit=limit
    )
