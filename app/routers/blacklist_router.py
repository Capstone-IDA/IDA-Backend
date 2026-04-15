"""
BlacklistRouter
GET /blacklist | GET /blacklist/{user_id} | DELETE /blacklist/{user_id}
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/blacklist", tags=["Blacklist"])


@router.get("")
async def get_blacklist(
    grade: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """블랙리스트 조회 (필터)"""
    from app.main import app_state
    return await app_state.repo.get_blacklist(grade=grade, is_active=is_active, limit=limit)


@router.get("/{user_id}")
async def get_blacklist_user(user_id: str):
    """사용자별 블랙리스트 조회"""
    from app.main import app_state
    record = await app_state.repo.get_blacklist_by_user(user_id)
    if not record:
        raise HTTPException(status_code=404, detail="블랙리스트 기록 없음")
    return record


@router.delete("/{user_id}")
async def delete_blacklist(user_id: str):
    """블랙리스트 해제"""
    from app.main import app_state
    await app_state.repo.delete_blacklist(user_id)
    return {"user_id": user_id, "status": "removed"}
