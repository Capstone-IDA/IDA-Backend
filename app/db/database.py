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
            license TEXT,
            company_id TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(company_id)
        );

        -- 차량
        CREATE TABLE IF NOT EXISTS vehicles (
            vehicle_id TEXT PRIMARY KEY,
            plate_number TEXT,
            model TEXT,
            company_id TEXT,
            year TEXT,
            status TEXT DEFAULT 'available',
            FOREIGN KEY (company_id) REFERENCES companies(company_id)
        );

        -- 운전 세션
        CREATE TABLE IF NOT EXISTS driving_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            vehicle_id TEXT NOT NULL,
            rental_id TEXT,
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
            collision_warning INTEGER NOT NULL DEFAULT 0,
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
            frame_number INTEGER,
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
            proximity_distance REAL DEFAULT 0.85,
            area_danger_ratio REAL DEFAULT 0.20,
            area_warning_ratio REAL DEFAULT 0.08,
            deduction_sudden_start REAL DEFAULT 5.0,
            deduction_sudden_brake REAL DEFAULT 5.0,
            deduction_proximate REAL DEFAULT 10.0,
            deduction_overspeeding REAL DEFAULT 8.0,
            green_min INTEGER DEFAULT 80,
            yellow_min INTEGER DEFAULT 50,
            orange_min INTEGER DEFAULT 30,
            blacklist_threshold INTEGER DEFAULT 30,
            alert_min_interval_sec INTEGER DEFAULT 30,
            event_cooldown_sec REAL DEFAULT 3.0,
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

        -- 프레임 이미지 저장
        CREATE TABLE IF NOT EXISTS frame_images (
            image_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            frame_number INTEGER NOT NULL,
            log_id INTEGER,
            image_data BLOB NOT NULL,
            content_type TEXT DEFAULT 'image/jpeg',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES driving_sessions(session_id),
            FOREIGN KEY (log_id) REFERENCES detection_logs(log_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_frame_images_session_frame
            ON frame_images(session_id, frame_number);
        """
        async with self.lock:
            await self.connection.executescript(schema)
            await self.connection.commit()

        # 기존 DB 호환: users.company_id 컬럼이 없으면 추가
        user_cols = await self.fetch_all("PRAGMA table_info(users)")
        if not any(c["name"] == "company_id" for c in user_cols):
            await self.execute("ALTER TABLE users ADD COLUMN company_id TEXT")
            logger.info("users 테이블에 company_id 컬럼 추가됨")

        # 기존 DB 호환: scoring_config.event_cooldown_sec 컬럼이 없으면 추가
        cfg_cols = await self.fetch_all("PRAGMA table_info(scoring_config)")
        if not any(c["name"] == "event_cooldown_sec" for c in cfg_cols):
            await self.execute(
                "ALTER TABLE scoring_config ADD COLUMN event_cooldown_sec REAL DEFAULT 3.0"
            )
            logger.info("scoring_config 테이블에 event_cooldown_sec 컬럼 추가됨")

        # 기존 DB 호환: scoring_config 면적 임계값 컬럼이 없으면 추가
        if not any(c["name"] == "area_danger_ratio" for c in cfg_cols):
            await self.execute(
                "ALTER TABLE scoring_config ADD COLUMN area_danger_ratio REAL DEFAULT 0.20"
            )
            logger.info("scoring_config 테이블에 area_danger_ratio 컬럼 추가됨")
        if not any(c["name"] == "area_warning_ratio" for c in cfg_cols):
            await self.execute(
                "ALTER TABLE scoring_config ADD COLUMN area_warning_ratio REAL DEFAULT 0.08"
            )
            logger.info("scoring_config 테이블에 area_warning_ratio 컬럼 추가됨")

        # 기존 DB 호환: driving_sessions.rental_id 컬럼이 없으면 추가
        sess_cols = await self.fetch_all("PRAGMA table_info(driving_sessions)")
        if not any(c["name"] == "rental_id" for c in sess_cols):
            await self.execute("ALTER TABLE driving_sessions ADD COLUMN rental_id TEXT")
            logger.info("driving_sessions 테이블에 rental_id 컬럼 추가됨")

        # 기존 DB 호환: can_data_logs.frame_number 컬럼이 없으면 추가
        can_cols = await self.fetch_all("PRAGMA table_info(can_data_logs)")
        if not any(c["name"] == "frame_number" for c in can_cols):
            await self.execute("ALTER TABLE can_data_logs ADD COLUMN frame_number INTEGER")
            logger.info("can_data_logs 테이블에 frame_number 컬럼 추가됨")

        # 기존 DB 호환: users.license 컬럼이 없으면 추가
        user_cols2 = await self.fetch_all("PRAGMA table_info(users)")
        if not any(c["name"] == "license" for c in user_cols2):
            await self.execute("ALTER TABLE users ADD COLUMN license TEXT")
            logger.info("users 테이블에 license 컬럼 추가됨")

        # 기존 DB 호환: vehicles.status 컬럼이 없으면 추가
        veh_cols = await self.fetch_all("PRAGMA table_info(vehicles)")
        if not any(c["name"] == "status" for c in veh_cols):
            await self.execute("ALTER TABLE vehicles ADD COLUMN status TEXT DEFAULT 'available'")
            logger.info("vehicles 테이블에 status 컬럼 추가됨")

        # 기본 스코어링 설정 삽입 (없을 때만)
        existing = await self.fetch_one("SELECT config_id FROM scoring_config LIMIT 1")
        if not existing:
            await self.execute(
                """INSERT INTO scoring_config
                   (accel_threshold, brake_threshold, speed_limit, proximity_distance,
                    area_danger_ratio, area_warning_ratio,
                    deduction_sudden_start, deduction_sudden_brake,
                    deduction_proximate, deduction_overspeeding,
                    green_min, yellow_min, orange_min,
                    blacklist_threshold, alert_min_interval_sec,
                    event_cooldown_sec, updated_at)
                    VALUES (3.0, 3.0, 20.0, 0.85, 0.20, 0.08, 5.0, 5.0, 10.0, 8.0, 80, 50, 30, 30, 30, 3.0, CURRENT_TIMESTAMP)"""
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

        await self.seed_demo_data()
        logger.info("DB 테이블 초기화 완료")

    async def seed_demo_data(self) -> None:
        """데모용 고객/차량/블랙리스트 시드 (매 부팅 idempotent)"""
        companies = [
            ("comp_sky", "스카이렌터카", "02-1234-5678"),
            ("comp_jeju", "제주렌터카", "064-9876-5432"),
        ]
        for cid, name, contact in companies:
            await self.execute(
                "INSERT OR IGNORE INTO companies (company_id, name, contact) VALUES (?, ?, ?)",
                (cid, name, contact),
            )

        users = [
            ("user_sky_01", "김철수", "010-1234-5678", "경기-12-345678", "comp_sky"),
            ("user_sky_02", "이영희", "010-9876-5432", "서울-08-112233", "comp_sky"),
            ("user_sky_03", "홍길동", "010-2345-6789", "서울-22-334455", "comp_sky"),
            ("user_sky_04", "이민재", "010-3456-7890", "경기-09-778899", "comp_sky"),
            ("user_jeju_01", "박민수", "010-5555-1234", "인천-15-667788", "comp_jeju"),
            ("user_jeju_02", "최지현", "010-2233-4455", "경남-03-990011", "comp_jeju"),
            ("user_jeju_03", "강동원", "010-4567-8901", "부산-14-223344", "comp_jeju"),
            ("user_jeju_04", "박서준", "010-5678-9012", "제주-07-112233", "comp_jeju"),
        ]
        for uid, name, phone, license_no, cid in users:
            await self.execute(
                """INSERT OR IGNORE INTO users (user_id, name, phone, license, company_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (uid, name, phone, license_no, cid),
            )

        # status: rented 대여중, available 대기, maintenance 정비중
        vehicles = [
            ("veh_sky_01", "12가 3456", "현대 아반떼", "2024", "rented", "comp_sky"),
            ("veh_sky_02", "34나 7890", "기아 K5", "2023", "rented", "comp_sky"),
            ("veh_sky_03", "56다 1234", "현대 쏘나타", "2024", "available", "comp_sky"),
            ("veh_sky_04", "78라 5678", "기아 셀토스", "2025", "maintenance", "comp_sky"),
            ("veh_sky_05", "90마 9012", "현대 투싼", "2023", "available", "comp_sky"),
            ("veh_jeju_01", "11바 1234", "기아 카니발", "2024", "rented", "comp_jeju"),
            ("veh_jeju_02", "22사 5678", "현대 스타리아", "2023", "rented", "comp_jeju"),
            ("veh_jeju_03", "33아 9012", "기아 쏘렌토", "2024", "available", "comp_jeju"),
            ("veh_jeju_04", "44아 3456", "현대 팰리세이드", "2025", "available", "comp_jeju"),
        ]
        for vid, plate, model, year, status, cid in vehicles:
            await self.execute(
                """INSERT OR IGNORE INTO vehicles
                   (vehicle_id, plate_number, model, year, status, company_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (vid, plate, model, year, status, cid),
            )

        # 블랙리스트는 업체 필터가 session-vehicle-company JOIN이라 더미 세션 필요
        # grade: blacklisted 제한, caution 관찰 중
        blacklist = [
            ("bl_sess_sky_03", "user_sky_03", "veh_sky_03", 18.0, "blacklisted"),
            ("bl_sess_sky_04", "user_sky_04", "veh_sky_05", 29.0, "caution"),
            ("bl_sess_jeju_03", "user_jeju_03", "veh_jeju_03", 21.0, "blacklisted"),
            ("bl_sess_jeju_04", "user_jeju_04", "veh_jeju_04", 34.0, "caution"),
        ]
        for sess_id, uid, vid, score, grade in blacklist:
            await self.execute(
                """INSERT OR IGNORE INTO driving_sessions
                   (session_id, user_id, vehicle_id, start_time, status)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP, 'completed')""",
                (sess_id, uid, vid),
            )
            existing = await self.fetch_one(
                "SELECT blacklist_id FROM blacklist WHERE user_id = ? AND is_active = 1",
                (uid,),
            )
            if not existing:
                await self.execute(
                    """INSERT INTO blacklist
                       (user_id, session_id, final_score, blacklist_grade,
                        created_at, is_active, history_count)
                       VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, 1, 1)""",
                    (uid, sess_id, score, grade),
                )

        logger.info("데모 데이터 시드 완료 (고객 8 / 차량 9 / 블랙리스트 4)")