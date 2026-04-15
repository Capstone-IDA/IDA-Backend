"""
DatabaseManager
ERD 기반 테이블 스키마 + WAL 모드 + asyncio.Lock
"""

import asyncio
import logging
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


class DatabaseManager:
    """비동기 SQLite 데이터베이스 관리자"""

    def __init__(self, db_path: str = "ida.db"):
        self.db_path: str = db_path
        self.connection: Optional[aiosqlite.Connection] = None
        self.lock: asyncio.Lock = asyncio.Lock()

    async def connect(self) -> None:
        """DB 연결 및 WAL 모드 활성화"""
        self.connection = await aiosqlite.connect(self.db_path, timeout=15)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA busy_timeout=15000")
        await self.connection.execute("PRAGMA foreign_keys=ON")
        await self.connection.commit()
        logger.info(f"DB 연결 완료: {self.db_path} (WAL 모드)")

    async def disconnect(self) -> None:
        """DB 연결 종료"""
        if self.connection:
            await self.connection.close()
            self.connection = None
            logger.info("DB 연결 종료")

    async def execute(self, query: str, params: tuple = ()) -> int:
        """쓰기 쿼리 실행 (INSERT/UPDATE/DELETE), lastrowid 반환"""
        async with self.lock:
            cursor = await self.connection.execute(query, params)
            await self.connection.commit()
            return cursor.lastrowid

    async def execute_many(self, query: str, params_list: list[tuple]) -> None:
        """배치 쓰기"""
        async with self.lock:
            await self.connection.executemany(query, params_list)
            await self.connection.commit()

    async def fetch_all(self, query: str, params: tuple = ()) -> list[dict]:
        """여러 행 조회"""
        cursor = await self.connection.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_one(self, query: str, params: tuple = ()) -> Optional[dict]:
        """단일 행 조회"""
        cursor = await self.connection.execute(query, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def init_tables(self) -> None:
        """ERD 기반 전체 테이블 스키마 생성"""
        schema = """
        -- 회사
        CREATE TABLE IF NOT EXISTS companies (
            company_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            contact TEXT,
            notification_endpoint TEXT
        );

        -- 계정 (로그인용)
        CREATE TABLE IF NOT EXISTS accounts (
            account_id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'company',
            company_id TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1,
            FOREIGN KEY (company_id) REFERENCES companies(company_id)
        );

        -- 사용자
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- 차량
        CREATE TABLE IF NOT EXISTS vehicles (
            vehicle_id TEXT PRIMARY KEY,
            plate_number TEXT,
            model TEXT,
            company_id TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(company_id)
        );

        -- 운전 세션
        CREATE TABLE IF NOT EXISTS driving_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            vehicle_id TEXT NOT NULL,
            start_time DATETIME NOT NULL,
            end_time DATETIME,
            scenario TEXT,
            status TEXT DEFAULT 'active',
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (vehicle_id) REFERENCES vehicles(vehicle_id)
        );

        -- 탐지 로그
        CREATE TABLE IF NOT EXISTS detection_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp DATETIME NOT NULL,
            frame_number INTEGER NOT NULL,
            object_count INTEGER DEFAULT 0,
            fps REAL,
            inference_time_ms REAL,
            FOREIGN KEY (session_id) REFERENCES driving_sessions(session_id)
        );

        -- 탐지된 객체
        CREATE TABLE IF NOT EXISTS detected_objects (
            object_id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_id INTEGER NOT NULL,
            track_id INTEGER,
            class_name TEXT NOT NULL,
            confidence REAL NOT NULL,
            bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
            depth_value REAL,
            distance_zone TEXT,
            risk_level TEXT,
            FOREIGN KEY (log_id) REFERENCES detection_logs(log_id)
        );

        -- 경고 기록
        CREATE TABLE IF NOT EXISTS alert_records (
            alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            track_id INTEGER,
            risk_level TEXT NOT NULL,
            consecutive_frames INTEGER DEFAULT 0,
            score REAL,
            grade TEXT,
            FOREIGN KEY (session_id) REFERENCES driving_sessions(session_id)
        );

        -- CAN 데이터 로그
        CREATE TABLE IF NOT EXISTS can_data_logs (
            can_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp DATETIME NOT NULL,
            speed_kmh REAL NOT NULL,
            acceleration REAL NOT NULL,
            brake_intensity REAL NOT NULL,
            scenario TEXT,
            FOREIGN KEY (session_id) REFERENCES driving_sessions(session_id)
        );

        -- 운전 이벤트
        CREATE TABLE IF NOT EXISTS driving_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp DATETIME NOT NULL,
            event_type TEXT NOT NULL,
            severity TEXT DEFAULT 'normal',
            speed REAL,
            acceleration REAL,
            is_proximate BOOLEAN DEFAULT 0,
            deduction REAL NOT NULL,
            track_id INTEGER,
            can_id INTEGER,
            FOREIGN KEY (session_id) REFERENCES driving_sessions(session_id),
            FOREIGN KEY (can_id) REFERENCES can_data_logs(can_id)
        );

        -- 점수 이력
        CREATE TABLE IF NOT EXISTS score_history (
            score_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp DATETIME NOT NULL,
            previous_score REAL NOT NULL,
            deduction REAL NOT NULL,
            current_score REAL NOT NULL,
            grade TEXT NOT NULL,
            event_id INTEGER,
            FOREIGN KEY (session_id) REFERENCES driving_sessions(session_id),
            FOREIGN KEY (event_id) REFERENCES driving_events(event_id)
        );

        -- 알림 로그
        CREATE TABLE IF NOT EXISTS notification_logs (
            notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            grade TEXT NOT NULL,
            score REAL NOT NULL,
            notification_type TEXT NOT NULL,
            company_id TEXT,
            status TEXT DEFAULT 'sent',
            retry_count INTEGER DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES driving_sessions(session_id),
            FOREIGN KEY (company_id) REFERENCES companies(company_id)
        );

        -- 세션 리포트
        CREATE TABLE IF NOT EXISTS session_reports (
            report_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            duration_minutes REAL DEFAULT 0,
            initial_score REAL DEFAULT 100,
            final_score REAL NOT NULL,
            final_grade TEXT NOT NULL,
            total_events INTEGER DEFAULT 0,
            sudden_start_count INTEGER DEFAULT 0,
            sudden_brake_count INTEGER DEFAULT 0,
            overspeeding_count INTEGER DEFAULT 0,
            proximate_event_count INTEGER DEFAULT 0,
            score_timeline_json TEXT,
            is_complete BOOLEAN DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES driving_sessions(session_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        -- 블랙리스트
        CREATE TABLE IF NOT EXISTS blacklist (
            blacklist_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            final_score REAL NOT NULL,
            blacklist_grade TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME,
            is_active BOOLEAN DEFAULT 1,
            history_count INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (session_id) REFERENCES driving_sessions(session_id)
        );

        -- 스코어링 설정
        CREATE TABLE IF NOT EXISTS scoring_config (
            config_id INTEGER PRIMARY KEY AUTOINCREMENT,
            accel_threshold REAL DEFAULT 2.0,
            brake_threshold REAL DEFAULT 2.0,
            speed_limit REAL DEFAULT 30.0,
            proximity_distance REAL DEFAULT 0.2,
            deduction_sudden_start REAL DEFAULT 5.0,
            deduction_sudden_brake REAL DEFAULT 5.0,
            deduction_proximate REAL DEFAULT 10.0,
            deduction_overspeeding REAL DEFAULT 8.0,
            green_min INTEGER DEFAULT 80,
            yellow_min INTEGER DEFAULT 50,
            orange_min INTEGER DEFAULT 30,
            blacklist_threshold INTEGER DEFAULT 30,
            alert_min_interval_sec INTEGER DEFAULT 30,
            updated_at DATETIME,
            updated_by TEXT
        );

        -- 설정 변경 로그
        CREATE TABLE IF NOT EXISTS config_change_logs (
            change_id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_id INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            changed_by TEXT,
            field_name TEXT NOT NULL,
            before_value TEXT,
            after_value TEXT,
            FOREIGN KEY (config_id) REFERENCES scoring_config(config_id)
        );

        -- 에러 로그
        CREATE TABLE IF NOT EXISTS error_logs (
            error_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            error_type TEXT NOT NULL,
            error_message TEXT,
            severity TEXT,
            is_resolved BOOLEAN DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES driving_sessions(session_id)
        );

        -- 인덱스 (조회 성능)
        CREATE INDEX IF NOT EXISTS idx_detection_logs_session
            ON detection_logs(session_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_driving_events_session
            ON driving_events(session_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_score_history_session
            ON score_history(session_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_can_data_logs_session
            ON can_data_logs(session_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_blacklist_user
            ON blacklist(user_id, is_active);
        CREATE INDEX IF NOT EXISTS idx_alert_records_session
            ON alert_records(session_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_accounts_username
            ON accounts(username);
        """
        async with self.lock:
            await self.connection.executescript(schema)
            await self.connection.commit()

        # 기본 스코어링 설정 삽입 (없을 때만)
        existing = await self.fetch_one("SELECT config_id FROM scoring_config LIMIT 1")
        if not existing:
            await self.execute(
                """INSERT INTO scoring_config
                   (accel_threshold, brake_threshold, speed_limit, proximity_distance,
                    deduction_sudden_start, deduction_sudden_brake,
                    deduction_proximate, deduction_overspeeding,
                    green_min, yellow_min, orange_min,
                    blacklist_threshold, alert_min_interval_sec, updated_at)
                   VALUES (2.0, 2.0, 30.0, 0.2, 5.0, 5.0, 10.0, 8.0,
                           80, 50, 30, 30, 30, CURRENT_TIMESTAMP)"""
            )
            logger.info("기본 스코어링 설정 삽입 완료")

        # 기본 계정 시드 (없을 때만)
        existing_admin = await self.fetch_one(
            "SELECT account_id FROM accounts WHERE role = 'admin' LIMIT 1"
        )
        if not existing_admin:
            from app.services.auth_service import hash_password
            # 관리자 계정
            await self.execute(
                """INSERT INTO accounts (account_id, username, password_hash, role, company_id)
                   VALUES (?, ?, ?, 'admin', NULL)""",
                ("admin_001", "admin", hash_password("admin1234"))
            )
            # 데모 업체 계정: 스카이렌터카
            await self.execute(
                "INSERT OR IGNORE INTO companies (company_id, name, contact) VALUES (?, ?, ?)",
                ("comp_sky", "스카이렌터카", "02-1234-5678")
            )
            await self.execute(
                """INSERT INTO accounts (account_id, username, password_hash, role, company_id)
                   VALUES (?, ?, ?, 'company', ?)""",
                ("acc_sky", "sky_rental", hash_password("sky1234"), "comp_sky")
            )
            # 데모 업체 계정: 제주렌터카
            await self.execute(
                "INSERT OR IGNORE INTO companies (company_id, name, contact) VALUES (?, ?, ?)",
                ("comp_jeju", "제주렌터카", "064-9876-5432")
            )
            await self.execute(
                """INSERT INTO accounts (account_id, username, password_hash, role, company_id)
                   VALUES (?, ?, ?, 'company', ?)""",
                ("acc_jeju", "jeju_rental", hash_password("jeju1234"), "comp_jeju")
            )
            logger.info("기본 계정 시드 완료 (admin / sky_rental / jeju_rental)")

        logger.info("DB 테이블 초기화 완료")
