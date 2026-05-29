"""
IDA 시스템 - FastAPI 진입점
라이프사이클 + 전역 상태 + 라우터 등록
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.db.database import DatabaseManager
from app.db.repository import LogRepository
from app.models.schemas import ScoringConfig
from app.services.alert_manager import AlertManager
from app.services.can_simulator import CANSimulator
from app.services.dashboard_hub import DashboardHub
from app.services.driving_scorer import DrivingScorer
from app.services.risk_evaluator import RiskEvaluator
from app.routers.frame_router import router as frame_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class SessionContext:
    """세션별 런타임 상태 (점수, CAN 시뮬레이터, 프레임 카운터)"""
    session_id: str
    scorer: DrivingScorer
    can_simulator: CANSimulator
    frame_counter: int = 0
    last_activity: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AppState:
    """전역 애플리케이션 상태"""
    db: DatabaseManager = field(
        default_factory=lambda: DatabaseManager(os.getenv("IDA_DB_PATH", "ida.db"))
    )
    repo: Optional[LogRepository] = field(default=None)
    risk_evaluator: RiskEvaluator = field(default_factory=RiskEvaluator)
    alert_manager: AlertManager = field(default_factory=AlertManager)
    dashboard_hub: DashboardHub = field(default_factory=DashboardHub)
    config: Optional[ScoringConfig] = None
    sessions: dict[str, "SessionContext"] = field(default_factory=dict)

    def __post_init__(self):
        if self.repo is None:
            self.repo = LogRepository(self.db)

    def create_session_context(self, session_id: str) -> "SessionContext":
        """세션 런타임 컨텍스트 생성 및 등록"""
        scorer = DrivingScorer()
        if self.config:
            scorer.reload_config(self.config)
        ctx = SessionContext(
            session_id=session_id,
            scorer=scorer,
            can_simulator=CANSimulator(),
        )
        self.sessions[session_id] = ctx
        return ctx


# 전역 상태 인스턴스
app_state = AppState()


# 유휴 세션 자동 정리 설정
SESSION_IDLE_TIMEOUT_SEC = 1800  # 30분 무활동 시 자동 종료
REAPER_INTERVAL_SEC = 60


async def _session_reaper() -> None:
    """유휴 세션 정리 루프: 일정 시간 무활동 세션을 자동 종료"""
    from app.routers.session_router import finalize_session

    try:
        while True:
            await asyncio.sleep(REAPER_INTERVAL_SEC)
            now = datetime.utcnow()
            stale = [
                sid for sid, ctx in list(app_state.sessions.items())
                if (now - ctx.last_activity).total_seconds() > SESSION_IDLE_TIMEOUT_SEC
            ]
            for sid in stale:
                logger.info(f"유휴 세션 자동 종료: {sid}")
                try:
                    await finalize_session(sid)
                except Exception as e:
                    logger.error(f"유휴 세션 정리 실패 {sid}: {e}")
    except asyncio.CancelledError:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 라이프사이클"""
    logger.info("IDA 서버 시작 중")

    await app_state.db.connect()
    await app_state.db.init_tables()

    config_row = await app_state.repo.get_config()
    if config_row:
        cfg = ScoringConfig(**{
            k: v for k, v in config_row.items()
            if k in ScoringConfig.model_fields
        })
        app_state.config = cfg
        app_state.risk_evaluator.reload_config(cfg)
        app_state.alert_manager.min_interval_sec = cfg.alert_min_interval_sec

    app_state.alert_manager.set_save_callback(app_state.repo.save_notification)

    # 서버 재시작 시 DB의 active 세션 복원
    active_sessions = await app_state.db.fetch_all(
        "SELECT session_id, scenario FROM driving_sessions WHERE status = 'active'"
    )
    for row in active_sessions:
        ctx = app_state.create_session_context(row["session_id"])
        if row["scenario"]:
            try:
                ctx.can_simulator.load_scenario(row["scenario"])
            except ValueError:
                pass
        logger.info(f"세션 복원: {row['session_id']}")

    reaper_task = asyncio.create_task(_session_reaper())

    logger.info("IDA 서버 준비 완료")
    yield

    logger.info("IDA 서버 종료 중")
    reaper_task.cancel()
    for ctx in list(app_state.sessions.values()):
        ctx.can_simulator.stop()
    app_state.sessions.clear()
    await app_state.db.disconnect()
    logger.info("IDA 서버 종료 완료")


app = FastAPI(
    title="IDA - Indoor Detection & Assistance",
    description="실내 주차장 렌터카 운전 행동 평가 시스템 API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """처리되지 않은 예외를 error_logs에 기록"""
    logger.exception(f"처리되지 않은 예외: {request.method} {request.url.path}")
    try:
        await app_state.repo.save_error(
            session_id=None,
            error_type=type(exc).__name__,
            error_message=f"{request.method} {request.url.path}: {exc}",
            severity="error",
        )
    except Exception as log_err:
        logger.error(f"error_logs 기록 실패: {log_err}")
    return JSONResponse(status_code=500, content={"detail": "내부 서버 오류"})

from app.routers.auth_router import router as auth_router
from app.routers.detection_router import router as detection_router
from app.routers.session_router import router as session_router
from app.routers.can_router import router as can_router
from app.routers.scoring_router import router as scoring_router
from app.routers.report_router import router as report_router
from app.routers.blacklist_router import router as blacklist_router
from app.routers.config_router import router as config_router
from app.routers.log_router import router as log_router
from app.routers.company_router import router as company_router
from app.routers.admin_router import router as admin_router
from app.routers.websocket import router as websocket_router

app.include_router(auth_router)
app.include_router(detection_router)
app.include_router(session_router)
app.include_router(can_router)
app.include_router(scoring_router)
app.include_router(report_router)
app.include_router(blacklist_router)
app.include_router(config_router)
app.include_router(log_router)
app.include_router(company_router)
app.include_router(admin_router)
app.include_router(websocket_router)
app.include_router(frame_router)

@app.get("/")
async def root():
    return {
        "service": "IDA - Indoor Detection & Assistance",
        "version": "1.0.0",
        "status": "running",
        "active_sessions": list(app_state.sessions.keys()),
    }