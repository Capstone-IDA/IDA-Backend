"""
AuthRouter
POST /auth/login | GET /auth/me | POST /auth/accounts | GET /auth/accounts | GET /auth/companies
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth_dependency import AuthContext, get_current_user, require_admin
from app.models.schemas import (
    AccountCreateRequest,
    AccountInfo,
    LoginRequest,
    LoginResponse,
)
from app.services.auth_service import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """업체별/관리자 로그인"""
    from app.main import app_state

    account = await app_state.repo.get_account_by_username(req.username)
    if not account:
        raise HTTPException(status_code=401, detail="존재하지 않는 계정입니다")

    if not verify_password(req.password, account["password_hash"]):
        raise HTTPException(status_code=401, detail="비밀번호가 일치하지 않습니다")

    token = create_token(
        account_id=account["account_id"],
        role=account["role"],
        company_id=account.get("company_id"),
    )

    return LoginResponse(
        token=token,
        account_id=account["account_id"],
        role=account["role"],
        company_id=account.get("company_id"),
        company_name=account.get("company_name"),
    )


@router.get("/me", response_model=AccountInfo)
async def get_me(auth: AuthContext = Depends(get_current_user)):
    """현재 로그인한 계정 정보"""
    from app.main import app_state

    account = await app_state.repo.get_account_by_id(auth.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="계정 정보를 찾을 수 없습니다")

    return AccountInfo(
        account_id=account["account_id"],
        username=account["username"],
        role=account["role"],
        company_id=account.get("company_id"),
        company_name=account.get("company_name"),
    )


@router.post("/accounts")
async def create_account(req: AccountCreateRequest,
                         auth: AuthContext = Depends(require_admin)):
    """계정 생성 (Admin 전용)"""
    from app.main import app_state

    # username 중복 확인
    existing = await app_state.repo.get_account_by_username(req.username)
    if existing:
        raise HTTPException(status_code=409, detail="이미 존재하는 username입니다")

    account_id = f"acc_{uuid.uuid4().hex[:10]}"
    company_id = req.company_id

    # company 역할이면 업체 정보도 등록
    if req.role == "company" and company_id:
        existing_company = await app_state.db.fetch_one(
            "SELECT company_id FROM companies WHERE company_id = ?",
            (company_id,)
        )
        if not existing_company:
            # 새 업체 등록
            c_id = company_id or f"comp_{uuid.uuid4().hex[:8]}"
            await app_state.db.execute(
                """INSERT INTO companies (company_id, name, contact, notification_endpoint)
                   VALUES (?, ?, ?, ?)""",
                (c_id, req.company_name or req.username,
                 req.contact, req.notification_endpoint)
            )
            company_id = c_id

    await app_state.repo.create_account(
        account_id=account_id,
        username=req.username,
        password_hash=hash_password(req.password),
        role=req.role,
        company_id=company_id if req.role == "company" else None,
    )

    return {
        "account_id": account_id,
        "username": req.username,
        "role": req.role,
        "company_id": company_id,
    }


@router.get("/accounts")
async def list_accounts(auth: AuthContext = Depends(require_admin)):
    """전체 계정 목록 (Admin 전용)"""
    from app.main import app_state
    return await app_state.repo.get_all_accounts()


@router.get("/companies")
async def list_companies(auth: AuthContext = Depends(require_admin)):
    """전체 업체 목록 (Admin 전용)"""
    from app.main import app_state
    return await app_state.repo.get_all_companies()
