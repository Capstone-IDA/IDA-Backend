"""
IDA 시스템 - FastAPI 진입점
라이프사이클 + 전역 상태 + 라우터 등록
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.database import DatabaseManager
from app.db.repository import LogRepository
from app.models.schemas import ScoringConfig
from app.services.alert_manager import AlertManager
from app.services.can_simulator import CANSimulator
from app.services.driving_scorer import DrivingScorer
from app.services.risk_evaluator import RiskEvaluator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class AppState:
    """전역 애플리케이션 상태"""
    db: DatabaseManager = field(default_factory=lambda: DatabaseManager("ida.db"))
    repo: Optional[LogRepository] = field(default=None)
    can_simulator: CANSimulator = field(default_factory=CANSimulator)
    scorer: DrivingScorer = field(default_factory=DrivingScorer)
    risk_evaluator: RiskEvaluator = field(default_factory=RiskEvaluator)
    alert_manager: AlertManager = field(default_factory=AlertManager)
    active_session_id: Optional[str] = None
    frame_counter: int = 0

    def __post_init__(self):
        if self.repo is None:
            self.repo = LogRepository(self.db)


# 전역 상태 인스턴스
app_state = AppState()


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
        app_state.scorer.reload_config(cfg)
        app_state.risk_evaluator.reload_config(cfg)
        app_state.alert_manager.min_interval_sec = cfg.alert_min_interval_sec

    app_state.alert_manager.set_save_callback(app_state.repo.save_notification)

    logger.info("IDA 서버 준비 완료")
    yield

    logger.info("IDA 서버 종료 중")
    app_state.can_simulator.stop()
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


@app.get("/")
async def root():
    return {
        "service": "IDA - Indoor Detection & Assistance",
        "version": "1.0.0",
        "status": "running",
        "active_session": app_state.active_session_id,
    }