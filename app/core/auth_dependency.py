"""
auth_dependency
FastAPI Depends로 토큰 추출 + 역할 검증
"""

from typing import Optional
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.auth_service import decode_token

bearer_scheme = HTTPBearer(auto_error=False)


class AuthContext:
    """인증된 사용자 컨텍스트"""

    def __init__(self, account_id: str, role: str,
                 company_id: Optional[str] = None):
        self.account_id = account_id
        self.role = role
        self.company_id = company_id

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_company(self) -> bool:
        return self.role == "company"

    def get_company_filter(self) -> Optional[str]:
        """
        Company 계정이면 자기 company_id 반환 (데이터 격리용)
        Admin이면 None 반환 (전체 조회 가능)
        """
        if self.is_admin:
            return None
        return self.company_id


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> AuthContext:
    """토큰에서 현재 사용자 추출"""
    if not credentials:
        raise HTTPException(status_code=401, detail="인증 토큰이 필요합니다")

    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 토큰입니다")

    return AuthContext(
        account_id=payload["account_id"],
        role=payload["role"],
        company_id=payload.get("company_id"),
    )

async def require_admin(
    auth: AuthContext = Depends(get_current_user),
) -> AuthContext:
    """Admin 역할 필수"""
    if not auth.is_admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")
    return auth


async def require_company_or_admin(
    auth: AuthContext = Depends(get_current_user),
) -> AuthContext:
    """Company 또는 Admin 역할 필수"""
    if auth.role not in ("admin", "company"):
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다")
    return auth
