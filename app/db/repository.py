"""
LogRepository
설계 레퍼런스 [4] LogRepository 오퍼레이션 기반
모든 데이터 저장/조회 담당 (단일 Repository)
"""

import json
import logging
from datetime import datetime
from typing import Optional

from app.db.database import DatabaseManager
from app.models.schemas import (
    BlacklistRecord,
    CANSnapshot,
    DrivingEvent,
    NotificationLog,
    RentalReport,
    ScoreRecord,
    ScoringConfig,
)

logger = logging.getLogger(__name__)


class LogRepository:
    """모든 데이터 저장/조회 담당"""

    def __init__(self, db: DatabaseManager):
        self.db = db

    # ── Detection ──

    async def save_detection(self, session_id: str, timestamp: datetime,
                             frame_number: int, object_count: int,
                             fps: float, inference_time_ms: float) -> int:
        """탐지 로그 저장, log_id 반환. 같은 (session, frame)이면 덮어쓴다."""
        # 동일 프레임의 기존 로그/객체 제거 (재적재 시 중복 누적 방지)
        existing = await self.db.fetch_all(
            "SELECT log_id FROM detection_logs WHERE session_id = ? AND frame_number = ?",
            (session_id, frame_number)
        )
        if existing:
            ids = [r["log_id"] for r in existing]
            placeholders = ",".join("?" * len(ids))
            await self.db.execute(
                f"DELETE FROM detected_objects WHERE log_id IN ({placeholders})",
                tuple(ids)
            )
            await self.db.execute(
                "DELETE FROM detection_logs WHERE session_id = ? AND frame_number = ?",
                (session_id, frame_number)
            )

        log_id = await self.db.execute(
            """INSERT INTO detection_logs
               (session_id, timestamp, frame_number, object_count, fps, inference_time_ms)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, timestamp.isoformat(), frame_number, object_count,
             fps, inference_time_ms)
        )
        return log_id

    async def save_detected_object(self, log_id: int, track_id: int,
                                   class_name: str, confidence: float,
                                   bbox_x: float, bbox_y: float,
                                   bbox_w: float, bbox_h: float,
                                   depth_value: float, distance_zone: str,
                                   risk_level: str) -> int:
        """탐지 객체 저장"""
        return await self.db.execute(
            """INSERT INTO detected_objects
               (log_id, track_id, class_name, confidence,
                bbox_x, bbox_y, bbox_w, bbox_h,
                depth_value, distance_zone, risk_level)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (log_id, track_id, class_name, confidence,
             bbox_x, bbox_y, bbox_w, bbox_h,
             depth_value, distance_zone, risk_level)
        )

    async def get_stats(self, session_id: Optional[str] = None,
                        start: Optional[datetime] = None,
                        end: Optional[datetime] = None) -> dict:
        """통계 데이터 조회 (mAP, FPS 등)"""
        where_clauses = []
        params: list = []

        if session_id:
            where_clauses.append("dl.session_id = ?")
            params.append(session_id)
        if start:
            where_clauses.append("dl.timestamp >= ?")
            params.append(start.isoformat())
        if end:
            where_clauses.append("dl.timestamp <= ?")
            params.append(end.isoformat())

        where = " AND ".join(where_clauses) if where_clauses else "1=1"

        # 기본 통계
        stats_row = await self.db.fetch_one(
            f"""SELECT COUNT(*) as total_frames,
                       AVG(fps) as avg_fps,
                       AVG(inference_time_ms) as avg_inference_ms
                FROM detection_logs dl WHERE {where}""",
            tuple(params)
        )

        # 위험도 분포
        risk_rows = await self.db.fetch_all(
            f"""SELECT do.risk_level, COUNT(*) as cnt
                FROM detected_objects do
                JOIN detection_logs dl ON do.log_id = dl.log_id
                WHERE {where}
                GROUP BY do.risk_level""",
            tuple(params)
        )
        risk_dist = {r["risk_level"]: r["cnt"] for r in risk_rows}

        # 클래스 분포
        class_rows = await self.db.fetch_all(
            f"""SELECT do.class_name, COUNT(*) as cnt
                FROM detected_objects do
                JOIN detection_logs dl ON do.log_id = dl.log_id
                WHERE {where}
                GROUP BY do.class_name""",
            tuple(params)
        )
        class_dist = {r["class_name"]: r["cnt"] for r in class_rows}

        # 경고 수
        alert_row = await self.db.fetch_one(
            f"""SELECT COUNT(*) as alert_count
                FROM alert_records ar
                {'WHERE ar.session_id = ?' if session_id else ''}""",
            (session_id,) if session_id else ()
        )

        # 에러 수
        error_row = await self.db.fetch_one(
            f"""SELECT COUNT(*) as error_count
                FROM error_logs el
                {'WHERE el.session_id = ?' if session_id else ''}""",
            (session_id,) if session_id else ()
        )

        return {
            "total_frames": stats_row["total_frames"] if stats_row else 0,
            "avg_fps": round(stats_row["avg_fps"] or 0, 1) if stats_row else 0,
            "avg_inference_ms": round(stats_row["avg_inference_ms"] or 0, 1) if stats_row else 0,
            "risk_distribution": risk_dist,
            "class_distribution": class_dist,
            "alert_count": alert_row["alert_count"] if alert_row else 0,
            "error_count": error_row["error_count"] if error_row else 0,
        }

    # ── Driving Events ──

    async def save_driving_event(self, event: DrivingEvent) -> int:
        """운전 이벤트 저장, event_id 반환"""
        return await self.db.execute(
            """INSERT INTO driving_events
               (session_id, timestamp, event_type, severity,
                speed, acceleration, is_proximate, deduction,
                track_id, can_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event.session_id, event.timestamp.isoformat(),
             event.event_type, event.severity,
             event.speed, event.acceleration,
             1 if event.is_proximate else 0,
             event.deduction, event.track_id, event.can_id)
        )

    async def get_events_by_session(self, session_id: str) -> list[dict]:
        """세션별 운전 이벤트 조회"""
        return await self.db.fetch_all(
            """SELECT * FROM driving_events
               WHERE session_id = ?
               ORDER BY timestamp""",
            (session_id,)
        )

    # ── Score ──

    async def save_score(self, record: ScoreRecord) -> int:
        """점수 기록 저장"""
        return await self.db.execute(
            """INSERT INTO score_history
               (session_id, timestamp, previous_score, deduction,
                current_score, grade, event_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (record.session_id, record.timestamp.isoformat(),
             record.previous_score, record.deduction,
             record.current_score, record.grade, record.event_id)
        )

    async def get_driver_score(self, session_id: str) -> Optional[dict]:
        """세션의 최신 점수 조회"""
        return await self.db.fetch_one(
            """SELECT * FROM score_history
               WHERE session_id = ?
               ORDER BY timestamp DESC LIMIT 1""",
            (session_id,)
        )

    async def get_score_timeline(self, session_id: str) -> list[dict]:
        """세션의 점수 타임라인 조회"""
        return await self.db.fetch_all(
            """SELECT * FROM score_history
               WHERE session_id = ?
               ORDER BY timestamp""",
            (session_id,)
        )

    # ── Report ──

    async def generate_report(self, session_id: str) -> Optional[dict]:
        """세션 데이터 기반 리포트 데이터 수집"""
        session = await self.db.fetch_one(
            "SELECT * FROM driving_sessions WHERE session_id = ?",
            (session_id,)
        )
        if not session:
            return None

        events = await self.get_events_by_session(session_id)
        score_data = await self.get_driver_score(session_id)
        timeline = await self.get_score_timeline(session_id)

        start_time = datetime.fromisoformat(session["start_time"])
        end_time = (datetime.fromisoformat(session["end_time"])
                    if session["end_time"] else datetime.utcnow())
        duration = (end_time - start_time).total_seconds() / 60.0

        sudden_start = sum(1 for e in events if e["event_type"] == "sudden_start")
        sudden_brake = sum(1 for e in events if e["event_type"] == "sudden_brake")
        overspeeding = sum(1 for e in events if e["event_type"] == "overspeeding")
        proximate = sum(1 for e in events if e["is_proximate"])

        final_score = score_data["current_score"] if score_data else 100.0
        final_grade = score_data["grade"] if score_data else "Green"

        return {
            "session_id": session_id,
            "user_id": session["user_id"],
            "duration_minutes": round(duration, 1),
            "initial_score": 100.0,
            "final_score": final_score,
            "final_grade": final_grade,
            "total_events": len(events),
            "sudden_start_count": sudden_start,
            "sudden_brake_count": sudden_brake,
            "overspeeding_count": overspeeding,
            "proximate_event_count": proximate,
            "score_timeline": [
                {"timestamp": t["timestamp"], "score": t["current_score"],
                 "grade": t["grade"]}
                for t in timeline
            ],
        }

    async def save_report(self, report: RentalReport) -> int:
        """리포트 저장"""
        return await self.db.execute(
            """INSERT OR REPLACE INTO session_reports
               (session_id, user_id, created_at, duration_minutes,
                initial_score, final_score, final_grade,
                total_events, sudden_start_count, sudden_brake_count,
                overspeeding_count, proximate_event_count,
                score_timeline_json, is_complete)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (report.session_id, report.user_id,
             report.created_at.isoformat(),
             report.duration_minutes, report.initial_score,
             report.final_score, report.final_grade,
             report.total_events, report.sudden_start_count,
             report.sudden_brake_count, report.overspeeding_count,
             report.proximate_event_count,
             json.dumps(report.score_timeline),
             1 if report.is_complete else 0)
        )

    async def get_report(self, session_id: str) -> Optional[dict]:
        """리포트 조회"""
        row = await self.db.fetch_one(
            "SELECT * FROM session_reports WHERE session_id = ?",
            (session_id,)
        )
        if row and row.get("score_timeline_json"):
            row = dict(row)
            row["score_timeline"] = json.loads(row["score_timeline_json"])
        return row

    async def get_reports_by_user(self, user_id: str,
                                  rental_id: Optional[str] = None) -> list[dict]:
        """사용자별 리포트 목록 조회. rental_id 지정 시 해당 렌트 건만."""
        query = """SELECT sr.*, ds.rental_id FROM session_reports sr
                   JOIN driving_sessions ds ON sr.session_id = ds.session_id
                   WHERE sr.user_id = ?"""
        params: list = [user_id]
        if rental_id is not None:
            query += " AND ds.rental_id = ?"
            params.append(rental_id)
        query += " ORDER BY sr.created_at DESC"

        rows = await self.db.fetch_all(query, tuple(params))
        for row in rows:
            if row.get("score_timeline_json"):
                row["score_timeline"] = json.loads(row["score_timeline_json"])
        return rows

    # ── Blacklist ──

    async def save_blacklist(self, record: BlacklistRecord) -> int:
        """블랙리스트 등록/업데이트"""
        existing = await self.db.fetch_one(
            "SELECT * FROM blacklist WHERE user_id = ? AND is_active = 1",
            (record.user_id,)
        )
        if existing:
            # 기존 기록 업데이트 (history_count 증가)
            return await self.db.execute(
                """UPDATE blacklist
                   SET final_score = ?, blacklist_grade = ?,
                       session_id = ?, updated_at = ?,
                       history_count = history_count + 1
                   WHERE blacklist_id = ?""",
                (record.final_score, record.blacklist_grade,
                 record.session_id, datetime.utcnow().isoformat(),
                 existing["blacklist_id"])
            )
        else:
            return await self.db.execute(
                """INSERT INTO blacklist
                   (user_id, session_id, final_score, blacklist_grade,
                    created_at, is_active, history_count)
                   VALUES (?, ?, ?, ?, ?, 1, 1)""",
                (record.user_id, record.session_id,
                 record.final_score, record.blacklist_grade,
                 record.created_at.isoformat())
            )

    async def get_blacklist(self, grade: Optional[str] = None,
                            is_active: Optional[bool] = None,
                            limit: int = 50) -> list[dict]:
        """블랙리스트 조회"""
        query = "SELECT * FROM blacklist WHERE 1=1"
        params: list = []
        if grade:
            query += " AND blacklist_grade = ?"
            params.append(grade)
        if is_active is not None:
            query += " AND is_active = ?"
            params.append(1 if is_active else 0)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return await self.db.fetch_all(query, tuple(params))

    async def get_blacklist_by_user(self, user_id: str) -> Optional[dict]:
        """사용자별 블랙리스트 조회"""
        return await self.db.fetch_one(
            "SELECT * FROM blacklist WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,)
        )

    async def delete_blacklist(self, user_id: str) -> bool:
        """블랙리스트 해제 (soft delete)"""
        await self.db.execute(
            """UPDATE blacklist SET is_active = 0, updated_at = ?
               WHERE user_id = ? AND is_active = 1""",
            (datetime.utcnow().isoformat(), user_id)
        )
        return True

    # ── Notification ──

    async def save_notification(self, log: NotificationLog) -> int:
        """알림 로그 저장"""
        return await self.db.execute(
            """INSERT INTO notification_logs
               (session_id, timestamp, grade, score,
                notification_type, company_id, status, retry_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (log.session_id, log.timestamp.isoformat(),
             log.grade, log.score, log.notification_type,
             log.company_id, log.status, log.retry_count)
        )

    # ── CAN Data ──

    async def save_can_data(self, session_id: str, snapshot: CANSnapshot) -> int:
        """CAN 데이터 저장, can_id 반환"""
        return await self.db.execute(
            """INSERT INTO can_data_logs
               (session_id, timestamp, speed_kmh, acceleration,
                brake_intensity, scenario)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, snapshot.timestamp.isoformat(),
             snapshot.speed_kmh, snapshot.acceleration,
             snapshot.brake_intensity, snapshot.scenario)
        )

    async def get_can_by_frame(self, session_id: str, frame_number: int) -> Optional[CANSnapshot]:
        """frame_number 순서 기반으로 can_data_logs에서 해당 행 조회.
        적재된 CAN 데이터가 있으면 CANSnapshot으로 반환, 없으면 None."""
        row = await self.db.fetch_one(
            """SELECT speed_kmh, acceleration, brake_intensity, scenario, timestamp
            FROM can_data_logs
            WHERE session_id = ?
            ORDER BY can_id ASC
            LIMIT 1 OFFSET ?""",
            (session_id, frame_number - 1)
        )
        if not row:
            return None
        return CANSnapshot(
            timestamp=datetime.fromisoformat(row["timestamp"]),
            speed_kmh=row["speed_kmh"],
            acceleration=row["acceleration"],
            brake_intensity=row["brake_intensity"],
            scenario=row["scenario"] or "file_playback",
        )

    # ── Frame Image ──

    async def save_frame_image(self, session_id: str, frame_number: int,
                                image_data: bytes, log_id: Optional[int] = None,
                                content_type: str = "image/jpeg") -> int:
        """프레임 이미지 저장, image_id 반환"""
        return await self.db.execute(
            """INSERT OR REPLACE INTO frame_images
            (session_id, frame_number, log_id, image_data, content_type)
            VALUES (?, ?, ?, ?, ?)""",
            (session_id, frame_number, log_id, image_data, content_type)
        )

    async def get_frame_image(self, session_id: str,
                            frame_number: int) -> Optional[dict]:
        """프레임 이미지 조회"""
        return await self.db.fetch_one(
            """SELECT image_data, content_type FROM frame_images
            WHERE session_id = ? AND frame_number = ?""",
            (session_id, frame_number)
        )

    # ── Config ──

    async def get_config(self) -> Optional[dict]:
        """스코어링 설정 조회"""
        return await self.db.fetch_one(
            "SELECT * FROM scoring_config ORDER BY config_id DESC LIMIT 1"
        )

    async def update_config(self, config: dict, changed_by: str = "system") -> None:
        """스코어링 설정 업데이트 (변경 로그 기록)"""
        current = await self.get_config()
        if not current:
            return

        config_id = current["config_id"]
        update_fields = []
        params: list = []

        for field, value in config.items():
            if field in ("config_id", "updated_at", "updated_by"):
                continue
            if value is not None and current.get(field) != value:
                # 변경 로그 기록
                await self.db.execute(
                    """INSERT INTO config_change_logs
                       (config_id, changed_by, field_name,
                        before_value, after_value)
                       VALUES (?, ?, ?, ?, ?)""",
                    (config_id, changed_by, field,
                     str(current.get(field)), str(value))
                )
                update_fields.append(f"{field} = ?")
                params.append(value)

        if update_fields:
            update_fields.append("updated_at = ?")
            params.append(datetime.utcnow().isoformat())
            update_fields.append("updated_by = ?")
            params.append(changed_by)
            params.append(config_id)

            await self.db.execute(
                f"""UPDATE scoring_config
                    SET {', '.join(update_fields)}
                    WHERE config_id = ?""",
                tuple(params)
            )

    async def reset_config(self) -> dict:
        """설정을 기본값으로 리셋"""
        defaults = {
            "accel_threshold": 3.0,
            "brake_threshold": 3.0,
            "speed_limit": 20.0,
            "proximity_distance": 0.2,
            "deduction_sudden_start": 5.0,
            "deduction_sudden_brake": 5.0,
            "deduction_proximate": 10.0,
            "deduction_overspeeding": 8.0,
            "green_min": 80,
            "yellow_min": 50,
            "orange_min": 30,
            "blacklist_threshold": 30,
            "alert_min_interval_sec": 30,
            "event_cooldown_sec": 3.0,
        }
        await self.update_config(defaults, changed_by="system_reset")
        return await self.get_config()

    # ── Session ──

    async def create_session(self, session_id: str, user_id: str,
                             vehicle_id: str, scenario: Optional[str] = None,
                             company_id: Optional[str] = None,
                             rental_id: Optional[str] = None) -> None:
        """운전 세션 생성"""
        # 사용자가 없으면 자동 생성 (캡스톤 편의)
        existing_user = await self.db.fetch_one(
            "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
        )
        if not existing_user:
            await self.db.execute(
                "INSERT INTO users (user_id, name) VALUES (?, ?)",
                (user_id, user_id)
            )

        # 차량도 자동 생성 (company_id 연결)
        existing_vehicle = await self.db.fetch_one(
            "SELECT vehicle_id FROM vehicles WHERE vehicle_id = ?",
            (vehicle_id,)
        )
        if not existing_vehicle:
            await self.db.execute(
                "INSERT INTO vehicles (vehicle_id, plate_number, company_id) VALUES (?, ?, ?)",
                (vehicle_id, vehicle_id, company_id)
            )
        elif company_id:
            # 기존 차량에 company_id 미설정 시 업데이트
            await self.db.execute(
                "UPDATE vehicles SET company_id = ? WHERE vehicle_id = ? AND company_id IS NULL",
                (company_id, vehicle_id)
            )

        await self.db.execute(
            """INSERT INTO driving_sessions
               (session_id, user_id, vehicle_id, rental_id, start_time, scenario, status)
               VALUES (?, ?, ?, ?, ?, ?, 'active')""",
            (session_id, user_id, vehicle_id, rental_id,
             datetime.utcnow().isoformat(), scenario)
        )

    async def end_session(self, session_id: str) -> None:
        """세션 종료"""
        await self.db.execute(
            """UPDATE driving_sessions
               SET end_time = ?, status = 'completed'
               WHERE session_id = ?""",
            (datetime.utcnow().isoformat(), session_id)
        )

    async def get_session(self, session_id: str) -> Optional[dict]:
        """세션 조회"""
        return await self.db.fetch_one(
            "SELECT * FROM driving_sessions WHERE session_id = ?",
            (session_id,)
        )

    # ── Error Logs ──

    async def save_error(self, session_id: Optional[str],
                         error_type: str, error_message: str,
                         severity: str = "warning") -> int:
        """에러 로그 저장"""
        return await self.db.execute(
            """INSERT INTO error_logs
               (session_id, error_type, error_message, severity)
               VALUES (?, ?, ?, ?)""",
            (session_id, error_type, error_message, severity)
        )

    # ── Logs 조회 (LogRouter) ──

    async def get_logs(self, session_id: Optional[str] = None,
                   start: Optional[datetime] = None,
                   end: Optional[datetime] = None,
                   limit: int = 100) -> list[dict]:
        """프레임별 탐지 로그 조회 (detected_objects 포함)"""
        where_clauses = ["1=1"]
        params: list = []

        if session_id:
            where_clauses.append("dl.session_id = ?")
            params.append(session_id)
        if start:
            where_clauses.append("dl.timestamp >= ?")
            params.append(start.isoformat())
        if end:
            where_clauses.append("dl.timestamp <= ?")
            params.append(end.isoformat())

        where = " AND ".join(where_clauses)

        frames = await self.db.fetch_all(
            f"""SELECT dl.log_id, dl.session_id, dl.frame_number,
                    dl.timestamp, dl.object_count, dl.fps, dl.inference_time_ms
                FROM detection_logs dl
                WHERE {where}
                ORDER BY dl.frame_number ASC
                LIMIT ?""",
            tuple(params + [limit])
        )

        if not frames:
            return []

        log_ids = [f["log_id"] for f in frames]
        placeholders = ",".join("?" * len(log_ids))
        objects = await self.db.fetch_all(
            f"""SELECT log_id, track_id, class_name, confidence,
                    bbox_x, bbox_y, bbox_w, bbox_h,
                    depth_value, distance_zone, risk_level
                FROM detected_objects
                WHERE log_id IN ({placeholders})""",
            tuple(log_ids)
        )

        obj_map: dict[int, list] = {}
        for obj in objects:
            obj_map.setdefault(obj["log_id"], []).append(dict(obj))

        result = []
        for frame in frames:
            row = dict(frame)
            row["objects"] = obj_map.get(frame["log_id"], [])
            result.append(row)

        return result

    # ══════════════════════════════════════
    # 인증 & 계정 관리
    # ══════════════════════════════════════

    async def get_account_by_username(self, username: str) -> Optional[dict]:
        """username으로 계정 조회"""
        return await self.db.fetch_one(
            """SELECT a.*, c.name as company_name
               FROM accounts a
               LEFT JOIN companies c ON a.company_id = c.company_id
               WHERE a.username = ? AND a.is_active = 1""",
            (username,)
        )

    async def get_account_by_id(self, account_id: str) -> Optional[dict]:
        """account_id로 계정 조회"""
        return await self.db.fetch_one(
            """SELECT a.*, c.name as company_name
               FROM accounts a
               LEFT JOIN companies c ON a.company_id = c.company_id
               WHERE a.account_id = ?""",
            (account_id,)
        )

    async def create_account(self, account_id: str, username: str,
                             password_hash: str, role: str,
                             company_id: Optional[str] = None) -> int:
        """계정 생성"""
        return await self.db.execute(
            """INSERT INTO accounts (account_id, username, password_hash, role, company_id)
               VALUES (?, ?, ?, ?, ?)""",
            (account_id, username, password_hash, role, company_id)
        )

    async def get_all_accounts(self) -> list[dict]:
        """전체 계정 목록 (Admin용)"""
        return await self.db.fetch_all(
            """SELECT a.account_id, a.username, a.role, a.company_id,
                      c.name as company_name, a.created_at, a.is_active
               FROM accounts a
               LEFT JOIN companies c ON a.company_id = c.company_id
               ORDER BY a.created_at DESC"""
        )

    async def get_all_companies(self) -> list[dict]:
        """전체 업체 목록"""
        return await self.db.fetch_all(
            "SELECT * FROM companies ORDER BY name"
        )

    # Vehicles

    async def create_vehicle(self, vehicle_id: str, plate_number: str,
                             model: Optional[str], company_id: Optional[str]) -> int:
        """차량 신규 등록"""
        return await self.db.execute(
            """INSERT INTO vehicles (vehicle_id, plate_number, model, company_id)
               VALUES (?, ?, ?, ?)""",
            (vehicle_id, plate_number, model, company_id)
        )

    async def get_vehicle_by_id(self, vehicle_id: str) -> Optional[dict]:
        """vehicle_id 단건 조회"""
        return await self.db.fetch_one(
            "SELECT * FROM vehicles WHERE vehicle_id = ?", (vehicle_id,)
        )

    async def get_vehicles_by_company(self, company_id: Optional[str] = None,
                                      limit: int = 100) -> list[dict]:
        """업체별 차량 목록 (company_id=None이면 전체)"""
        query = """
            SELECT v.*, c.name as company_name
            FROM vehicles v
            LEFT JOIN companies c ON v.company_id = c.company_id
            WHERE 1=1
        """
        params: list = []
        if company_id:
            query += " AND v.company_id = ?"
            params.append(company_id)
        query += " ORDER BY v.vehicle_id DESC LIMIT ?"
        params.append(limit)
        return await self.db.fetch_all(query, tuple(params))

    # Users (고객)

    async def create_user(self, user_id: str, name: str,
                          phone: Optional[str], company_id: Optional[str]) -> int:
        """고객 신규 등록"""
        return await self.db.execute(
            """INSERT INTO users (user_id, name, phone, company_id)
               VALUES (?, ?, ?, ?)""",
            (user_id, name, phone, company_id)
        )

    async def get_user_by_id(self, user_id: str) -> Optional[dict]:
        """user_id 단건 조회"""
        return await self.db.fetch_one(
            """SELECT u.*, c.name as company_name
               FROM users u
               LEFT JOIN companies c ON u.company_id = c.company_id
               WHERE u.user_id = ?""",
            (user_id,)
        )

    async def get_users_by_company(self, company_id: Optional[str] = None,
                                   limit: int = 100) -> list[dict]:
        """업체별 고객 목록"""
        query = """
            SELECT u.*, c.name as company_name
            FROM users u
            LEFT JOIN companies c ON u.company_id = c.company_id
            WHERE 1=1
        """
        params: list = []
        if company_id:
            query += " AND u.company_id = ?"
            params.append(company_id)
        query += " ORDER BY u.created_at DESC LIMIT ?"
        params.append(limit)
        return await self.db.fetch_all(query, tuple(params))


    # ══════════════════════════════════════
    # 업체별 필터링 조회 (CompanyDashboard + AdminDashboard)
    # ══════════════════════════════════════

    async def get_sessions_by_company(self, company_id: Optional[str] = None,
                                      status: Optional[str] = None,
                                      limit: int = 50) -> list[dict]:
        """업체별 세션 목록 (company_id=None이면 전체: Admin용)"""
        query = """
            SELECT ds.*, v.company_id, c.name as company_name
            FROM driving_sessions ds
            JOIN vehicles v ON ds.vehicle_id = v.vehicle_id
            LEFT JOIN companies c ON v.company_id = c.company_id
            WHERE 1=1
        """
        params: list = []
        if company_id:
            query += " AND v.company_id = ?"
            params.append(company_id)
        if status:
            query += " AND ds.status = ?"
            params.append(status)
        query += " ORDER BY ds.start_time DESC LIMIT ?"
        params.append(limit)
        return await self.db.fetch_all(query, tuple(params))

    async def get_reports_by_company(self, company_id: Optional[str] = None,
                                     limit: int = 50) -> list[dict]:
        """업체별 리포트 목록"""
        query = """
            SELECT sr.*, v.company_id, c.name as company_name
            FROM session_reports sr
            JOIN driving_sessions ds ON sr.session_id = ds.session_id
            JOIN vehicles v ON ds.vehicle_id = v.vehicle_id
            LEFT JOIN companies c ON v.company_id = c.company_id
            WHERE 1=1
        """
        params: list = []
        if company_id:
            query += " AND v.company_id = ?"
            params.append(company_id)
        query += " ORDER BY sr.created_at DESC LIMIT ?"
        params.append(limit)
        rows = await self.db.fetch_all(query, tuple(params))
        for row in rows:
            if row.get("score_timeline_json"):
                row["score_timeline"] = json.loads(row["score_timeline_json"])
        return rows

    async def get_events_by_company(self, company_id: Optional[str] = None,
                                    limit: int = 100) -> list[dict]:
        """업체별 운전 이벤트"""
        query = """
            SELECT de.*, v.company_id
            FROM driving_events de
            JOIN driving_sessions ds ON de.session_id = ds.session_id
            JOIN vehicles v ON ds.vehicle_id = v.vehicle_id
            WHERE 1=1
        """
        params: list = []
        if company_id:
            query += " AND v.company_id = ?"
            params.append(company_id)
        query += " ORDER BY de.timestamp DESC LIMIT ?"
        params.append(limit)
        return await self.db.fetch_all(query, tuple(params))

    async def get_blacklist_by_company(self, company_id: Optional[str] = None,
                                       grade: Optional[str] = None,
                                       is_active: Optional[bool] = None,
                                       limit: int = 50) -> list[dict]:
        """업체별 블랙리스트"""
        query = """
            SELECT bl.*, v.company_id, c.name as company_name
            FROM blacklist bl
            JOIN driving_sessions ds ON bl.session_id = ds.session_id
            JOIN vehicles v ON ds.vehicle_id = v.vehicle_id
            LEFT JOIN companies c ON v.company_id = c.company_id
            WHERE 1=1
        """
        params: list = []
        if company_id:
            query += " AND v.company_id = ?"
            params.append(company_id)
        if grade:
            query += " AND bl.blacklist_grade = ?"
            params.append(grade)
        if is_active is not None:
            query += " AND bl.is_active = ?"
            params.append(1 if is_active else 0)
        query += " ORDER BY bl.created_at DESC LIMIT ?"
        params.append(limit)
        return await self.db.fetch_all(query, tuple(params))

    async def get_scores_by_company(self, company_id: Optional[str] = None,
                                    limit: int = 100) -> list[dict]:
        """업체별 점수 이력"""
        query = """
            SELECT sh.*, v.company_id
            FROM score_history sh
            JOIN driving_sessions ds ON sh.session_id = ds.session_id
            JOIN vehicles v ON ds.vehicle_id = v.vehicle_id
            WHERE 1=1
        """
        params: list = []
        if company_id:
            query += " AND v.company_id = ?"
            params.append(company_id)
        query += " ORDER BY sh.score_id DESC LIMIT ?"
        params.append(limit)
        return await self.db.fetch_all(query, tuple(params))

    async def get_notifications_by_company(self, company_id: Optional[str] = None,
                                           limit: int = 50) -> list[dict]:
        """업체별 알림 이력"""
        query = "SELECT * FROM notification_logs WHERE 1=1"
        params: list = []
        if company_id:
            query += " AND company_id = ?"
            params.append(company_id)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return await self.db.fetch_all(query, tuple(params))

    async def get_stats_by_company(self, company_id: Optional[str] = None) -> dict:
        """업체별 통합 통계 (Admin 대시보드)"""
        def _where(alias: str = "v") -> tuple[str, list]:
            if company_id:
                return f" AND {alias}.company_id = ?", [company_id]
            return "", []

        w, p = _where()
        active = await self.db.fetch_one(
            f"""SELECT COUNT(*) as cnt FROM driving_sessions ds
                JOIN vehicles v ON ds.vehicle_id = v.vehicle_id
                WHERE ds.status = 'active'{w}""", tuple(p))
        total = await self.db.fetch_one(
            f"""SELECT COUNT(*) as cnt FROM driving_sessions ds
                JOIN vehicles v ON ds.vehicle_id = v.vehicle_id WHERE 1=1{w}""", tuple(p))
        avg_row = await self.db.fetch_one(
            f"""SELECT AVG(sr.final_score) as avg_score FROM session_reports sr
                JOIN driving_sessions ds ON sr.session_id = ds.session_id
                JOIN vehicles v ON ds.vehicle_id = v.vehicle_id WHERE 1=1{w}""", tuple(p))
        events = await self.db.fetch_one(
            f"""SELECT COUNT(*) as cnt FROM driving_events de
                JOIN driving_sessions ds ON de.session_id = ds.session_id
                JOIN vehicles v ON ds.vehicle_id = v.vehicle_id WHERE 1=1{w}""", tuple(p))
        bl_count = await self.db.fetch_one(
            f"""SELECT COUNT(*) as cnt FROM blacklist bl
                JOIN driving_sessions ds ON bl.session_id = ds.session_id
                JOIN vehicles v ON ds.vehicle_id = v.vehicle_id
                WHERE bl.is_active = 1{w}""", tuple(p))

        # 차량/고객 카운트 (등록 결과 시각화용)
        vehicle_row = await self.db.fetch_one(
            f"SELECT COUNT(*) as cnt FROM vehicles v WHERE 1=1{w}", tuple(p))
        w_u, p_u = _where("u")
        customer_row = await self.db.fetch_one(
            f"SELECT COUNT(*) as cnt FROM users u WHERE 1=1{w_u}", tuple(p_u))

        return {
            "active_sessions": active["cnt"] if active else 0,
            "total_sessions": total["cnt"] if total else 0,
            "avg_final_score": round(avg_row["avg_score"] or 0, 1) if avg_row else 0,
            "total_events": events["cnt"] if events else 0,
            "blacklist_count": bl_count["cnt"] if bl_count else 0,
            "vehicle_count": vehicle_row["cnt"] if vehicle_row else 0,
            "customer_count": customer_row["cnt"] if customer_row else 0,
        }
