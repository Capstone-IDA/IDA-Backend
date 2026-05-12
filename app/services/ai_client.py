"""
AIClient
AI 서버와 HTTP 통신, 한보림 스키마 응답 파싱
"""

import logging
from typing import Optional

import httpx

from app.models.schemas import AIDetectionResponse, CANSnapshot

logger = logging.getLogger(__name__)


class AIClient:
    """AI 서버 HTTP 클라이언트"""

    def __init__(self, base_url: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client: Optional[httpx.AsyncClient] = None
        self._last_ok: bool = False

    async def start(self) -> None:
        """클라이언트 인스턴스 생성"""
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
        )
        logger.info(f"AIClient 시작: base_url={self.base_url}")

    async def close(self) -> None:
        """클라이언트 종료"""
        if self.client:
            await self.client.aclose()
            self.client = None
            logger.info("AIClient 종료")

    async def detect(
        self,
        session_id: str,
        frame_id: int,
        image_bytes: bytes,
        can_snapshot: Optional[CANSnapshot] = None,
        filename: str = "frame.jpg",
        content_type: str = "image/jpeg",
    ) -> Optional[AIDetectionResponse]:
        """AI 서버에 프레임 전송 후 응답 파싱, 실패 시 None"""
        if self.client is None:
            logger.error("AIClient가 시작되지 않음")
            return None

        files = {"file": (filename, image_bytes, content_type)}
        data = {
            "session_id": session_id,
            "frame_id": str(frame_id),
        }
        if can_snapshot:
            data["can"] = can_snapshot.model_dump_json()

        try:
            response = await self.client.post(
                "/inference/detect",
                files=files,
                data=data,
            )
            response.raise_for_status()
            self._last_ok = True
            return AIDetectionResponse.model_validate(response.json())
        except httpx.HTTPError as e:
            self._last_ok = False
            logger.warning(f"AI 호출 실패: {e}")
            return None
        except Exception as e:
            self._last_ok = False
            logger.error(f"AI 응답 파싱 실패: {e}")
            return None

    async def ping(self) -> bool:
        """AI 서버 헬스체크"""
        if self.client is None:
            return False
        try:
            r = await self.client.get("/health", timeout=2)
            return r.status_code == 200
        except Exception:
            return self._last_ok