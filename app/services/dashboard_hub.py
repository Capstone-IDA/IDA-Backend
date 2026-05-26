"""
DashboardHub
FE 대시보드 WebSocket 구독자 관리 + 프레임 결과 브로드캐스트
"""

import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class DashboardHub:
    """세션별 FE 구독자 관리"""

    def __init__(self):
        self._subscribers: dict[str, set[WebSocket]] = {}

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        """FE 구독자 등록 (핸드셰이크 포함)"""
        await websocket.accept()
        self._subscribers.setdefault(session_id, set()).add(websocket)
        logger.info(
            f"대시보드 구독 등록: session={session_id}, "
            f"구독자={self.count(session_id)}"
        )

    def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        """FE 구독자 해제"""
        subs = self._subscribers.get(session_id)
        if subs:
            subs.discard(websocket)
            if not subs:
                self._subscribers.pop(session_id, None)
        logger.info(f"대시보드 구독 해제: session={session_id}")

    def count(self, session_id: str) -> int:
        """세션 구독자 수"""
        return len(self._subscribers.get(session_id, ()))

    async def broadcast(self, session_id: str, message: dict) -> None:
        """세션 구독자 전원에게 전송, 끊긴 연결은 정리"""
        subs = self._subscribers.get(session_id)
        if not subs:
            return
        dead: list[WebSocket] = []
        for ws in list(subs):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            subs.discard(ws)
        if dead:
            logger.info(f"끊긴 대시보드 구독 {len(dead)}건 정리: session={session_id}")
