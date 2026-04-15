"""
AlertManager
등급 변동 시 알림 발송, min_interval로 알림 폭주 방지, 재시도 큐
"""

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Optional

from app.models.schemas import NotificationLog

logger = logging.getLogger(__name__)


class AlertManager:
    """등급 변동 시 업체 알림 관리"""

    def __init__(self, min_interval_sec: int = 30, max_retry: int = 3):
        self.min_interval_sec: int = min_interval_sec
        self.last_sent: dict[str, datetime] = {}  # session_id → 마지막 발송 시각
        self.retry_queue: deque[NotificationLog] = deque(maxlen=100)
        self.max_retry: int = max_retry
        # 콜백: 실제 알림 저장용 (main에서 주입)
        self._save_callback = None

    def set_save_callback(self, callback) -> None:
        """알림 저장 콜백 설정 (LogRepository.save_notification)"""
        self._save_callback = callback

    async def on_grade_change(self, session_id: str, grade: str,
                              score: float, company_id: Optional[str] = None) -> None:
        """등급 변동 시 호출 — 알림 유형 결정 후 발송"""
        if not self.should_send(session_id):
            logger.debug(f"알림 스킵 (min_interval): session={session_id}")
            return

        if grade in ("Red", "Orange"):
            await self.send_critical(session_id, score, grade, company_id)
        elif grade == "Yellow":
            await self.send_warning(session_id, score, grade, company_id)
        # Green은 알림 불필요

    def should_send(self, session_id: str) -> bool:
        """min_interval 기반 발송 여부 판단"""
        last = self.last_sent.get(session_id)
        if last is None:
            return True
        elapsed = (datetime.utcnow() - last).total_seconds()
        return elapsed >= self.min_interval_sec

    async def send_warning(self, session_id: str, score: float,
                           grade: str, company_id: Optional[str] = None) -> bool:
        """경고 알림 발송"""
        return await self._send(session_id, score, grade, "warning", company_id)

    async def send_critical(self, session_id: str, score: float,
                            grade: str, company_id: Optional[str] = None) -> bool:
        """위험 알림 발송"""
        return await self._send(session_id, score, grade, "critical", company_id)

    async def _send(self, session_id: str, score: float, grade: str,
                    notification_type: str, company_id: Optional[str]) -> bool:
        """실제 알림 발송 로직"""
        log = NotificationLog(
            session_id=session_id,
            grade=grade,
            score=score,
            notification_type=notification_type,
            company_id=company_id,
            status="sent",
            retry_count=0,
        )

        try:
            # 캡스톤 범위: 실제 외부 발송 대신 로그 저장으로 대체
            logger.info(
                f"[알림 발송] session={session_id} "
                f"type={notification_type} grade={grade} score={score}"
            )
            log.status = "sent"
            self.last_sent[session_id] = datetime.utcnow()

            if self._save_callback:
                await self._save_callback(log)
            return True

        except Exception as e:
            logger.error(f"알림 발송 실패: {e}")
            log.status = "failed"
            self.retry_queue.append(log)
            if self._save_callback:
                await self._save_callback(log)
            return False

    async def retry_failed(self) -> None:
        """실패한 알림 재시도"""
        retries = list(self.retry_queue)
        self.retry_queue.clear()

        for log in retries:
            if log.retry_count >= self.max_retry:
                logger.warning(f"알림 최대 재시도 초과: session={log.session_id}")
                continue

            log.retry_count += 1
            log.status = "retrying"
            logger.info(f"알림 재시도 ({log.retry_count}/{self.max_retry}): {log.session_id}")

            try:
                log.status = "sent"
                self.last_sent[log.session_id] = datetime.utcnow()
                if self._save_callback:
                    await self._save_callback(log)
            except Exception:
                log.status = "failed"
                self.retry_queue.append(log)
                if self._save_callback:
                    await self._save_callback(log)
