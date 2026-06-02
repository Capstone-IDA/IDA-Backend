"""
LogRouter
GET /logs | GET /stats
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

from app.core.object_classes import (
    get_category_by_name,
    get_korean_name,
    should_display,
)

router = APIRouter(tags=["Log"])


@router.get("/logs")
async def get_logs(
    session_id: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    start_frame: Optional[int] = Query(None, ge=0),
    end_frame: Optional[int] = Query(None, ge=0),
    limit: int = Query(2000, ge=1, le=10000),
):
    """탐지 로그 조회 (session_id / 기간 / 프레임 범위 필터)"""
    from app.main import app_state
    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None
    logs = await app_state.repo.get_logs(
        session_id=session_id,
        start=start_dt,
        end=end_dt,
        start_frame=start_frame,
        end_frame=end_frame,
        limit=limit,
    )
    return {"total_count": len(logs), "frames": logs}

@router.get("/stats")
async def get_stats(
    session_id: Optional[str] = Query(None),
    period: Optional[str] = Query(None, description="예: 1h, 24h"),
):
    """통계 조회"""
    from app.main import app_state

    start_dt = None
    if period:
        hours = int(period.replace("h", "")) if "h" in period else 24
        from datetime import timedelta
        start_dt = datetime.utcnow() - timedelta(hours=hours)

    return await app_state.repo.get_stats(
        session_id=session_id,
        start=start_dt,
    )
