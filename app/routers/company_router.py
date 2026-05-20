"""
CompanyRouter (CompanyDashboard 전용)
로그인한 업체의 데이터만 조회 가능
모든 엔드포인트에 company_id 자동 필터링 적용
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth_dependency import AuthContext, require_company_or_admin
from app.models.schemas import (
    CustomerCreateRequest,
    CustomerInfo,
    VehicleCreateRequest,
    VehicleInfo,
)

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


@router.post("/vehicles", response_model=VehicleInfo)
async def create_company_vehicle(
    req: VehicleCreateRequest,
    auth: AuthContext = Depends(require_company_or_admin),
):
    """차량 등록"""
    from app.main import app_state

    # Company 계정은 자기 업체로 강제, Admin은 req.company_id 명시 필요
    if auth.is_company:
        company_id = auth.company_id
    else:
        company_id = req.company_id
        if not company_id:
            raise HTTPException(status_code=400, detail="company_id가 필요합니다")

    # company_id 유효성 확인
    exists = await app_state.db.fetch_one(
        "SELECT name FROM companies WHERE company_id = ?", (company_id,)
    )
    if not exists:
        raise HTTPException(status_code=404, detail="존재하지 않는 company_id입니다")

    vehicle_id = f"veh_{uuid.uuid4().hex[:10]}"
    await app_state.repo.create_vehicle(
        vehicle_id=vehicle_id,
        plate_number=req.plate_number,
        model=req.model,
        company_id=company_id,
    )

    return VehicleInfo(
        vehicle_id=vehicle_id,
        plate_number=req.plate_number,
        model=req.model,
        company_id=company_id,
        company_name=exists["name"],
    )


@router.get("/vehicles")
async def list_company_vehicles(
    limit: int = Query(100, ge=1, le=500),
    auth: AuthContext = Depends(require_company_or_admin),
):
    """업체별 차량 목록"""
    from app.main import app_state
    company_id = auth.get_company_filter()
    return await app_state.repo.get_vehicles_by_company(
        company_id=company_id, limit=limit
    )


@router.post("/customers", response_model=CustomerInfo)
async def create_company_customer(
    req: CustomerCreateRequest,
    auth: AuthContext = Depends(require_company_or_admin),
):
    """고객 등록"""
    from app.main import app_state

    if auth.is_company:
        company_id = auth.company_id
    else:
        company_id = req.company_id
        if not company_id:
            raise HTTPException(status_code=400, detail="company_id가 필요합니다")

    exists = await app_state.db.fetch_one(
        "SELECT name FROM companies WHERE company_id = ?", (company_id,)
    )
    if not exists:
        raise HTTPException(status_code=404, detail="존재하지 않는 company_id입니다")

    user_id = f"user_{uuid.uuid4().hex[:10]}"
    await app_state.repo.create_user(
        user_id=user_id,
        name=req.name,
        phone=req.phone,
        company_id=company_id,
    )

    row = await app_state.repo.get_user_by_id(user_id)
    return CustomerInfo(
        user_id=user_id,
        name=req.name,
        phone=req.phone,
        company_id=company_id,
        company_name=exists["name"],
        created_at=row.get("created_at") if row else None,
    )


@router.get("/customers")
async def list_company_customers(
    limit: int = Query(100, ge=1, le=500),
    auth: AuthContext = Depends(require_company_or_admin),
):
    """업체별 고객 목록"""
    from app.main import app_state
    company_id = auth.get_company_filter()
    return await app_state.repo.get_users_by_company(
        company_id=company_id, limit=limit
    )