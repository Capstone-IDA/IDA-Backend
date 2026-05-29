"""
FrameRouter
GET /frames/{session_id}/{frame_number}
GET /frames/{session_id}  (목록)
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(prefix="/frames", tags=["Frame"])


@router.get("/{session_id}/{frame_number}")
async def get_frame(session_id: str, frame_number: int):
    """저장된 프레임 이미지 반환 (FE 재생용)"""
    from app.main import app_state

    row = await app_state.repo.get_frame_image(session_id, frame_number)
    if not row:
        raise HTTPException(status_code=404, detail="프레임 없음")

    return Response(
        content=bytes(row["image_data"]),
        media_type=row["content_type"],
    )


@router.get("/{session_id}")
async def list_frames(session_id: str):
    """세션에 저장된 프레임 번호 목록"""
    from app.main import app_state

    rows = await app_state.repo.db.fetch_all(
        """SELECT frame_number, created_at FROM frame_images
           WHERE session_id = ?
           ORDER BY frame_number ASC""",
        (session_id,)
    )
    return {"session_id": session_id, "frame_count": len(rows), "frames": rows}