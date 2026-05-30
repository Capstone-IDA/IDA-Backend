"""
세션 데이터 정리 스크립트
특정 session_id 또는 전체 세션 관련 데이터를 삭제한다.
사용 예:
  python scripts/cleanup_sessions.py --db ida.db --session sess_xxx sess_yyy
  python scripts/cleanup_sessions.py --db ida.db --all
"""

import argparse
import logging
import sqlite3

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# session_id로 직접 지우는 테이블 (자식 -> 부모 순서)
SESSION_TABLES = [
    "frame_images",
    "can_data_logs",
    "driving_events",
    "score_history",
    "alert_records",
    "notification_logs",
    "session_reports",
    "error_logs",
    "detection_logs",
    "driving_sessions",
]


def _delete_for_session(cur: sqlite3.Cursor, session_id: str) -> dict:
    """세션 하나의 모든 관련 행 삭제, 테이블별 삭제 건수 반환."""
    counts: dict[str, int] = {}
    # detected_objects는 log_id로 연결되므로 detection_logs보다 먼저 제거
    cur.execute(
        """DELETE FROM detected_objects
           WHERE log_id IN (SELECT log_id FROM detection_logs WHERE session_id = ?)""",
        (session_id,)
    )
    counts["detected_objects"] = cur.rowcount
    for table in SESSION_TABLES:
        cur.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
        counts[table] = cur.rowcount
    return counts


def _delete_all(cur: sqlite3.Cursor) -> dict:
    """모든 세션 관련 테이블 전체 삭제."""
    counts: dict[str, int] = {}
    cur.execute("DELETE FROM detected_objects")
    counts["detected_objects"] = cur.rowcount
    for table in SESSION_TABLES:
        cur.execute(f"DELETE FROM {table}")
        counts[table] = cur.rowcount
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="IDA 세션 데이터 정리")
    parser.add_argument("--db", required=True, help="SQLite DB 경로")
    parser.add_argument("--session", nargs="+", help="삭제할 session_id 목록")
    parser.add_argument("--all", action="store_true", help="모든 세션 데이터 삭제")
    parser.add_argument("--yes", action="store_true", help="확인 프롬프트 생략")
    parser.add_argument("--vacuum", action="store_true", help="삭제 후 VACUUM으로 파일 축소")
    args = parser.parse_args()

    if not args.all and not args.session:
        parser.error("--session 또는 --all 중 하나는 필요합니다")
    if args.all and args.session:
        parser.error("--all과 --session은 함께 쓸 수 없습니다")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = OFF")
    cur = conn.cursor()

    try:
        if args.all:
            if not args.yes:
                ans = input("모든 세션 데이터를 삭제합니다. 진행할까요? [y/N] ")
                if ans.strip().lower() != "y":
                    logger.info("취소됨")
                    return
            logger.info("전체 세션 데이터 삭제 시작")
            counts = _delete_all(cur)
            conn.commit()
            for t, c in counts.items():
                logger.info(f"  {t}: {c}건 삭제")
            logger.info("완료")
        else:
            for sid in args.session:
                counts = _delete_for_session(cur, sid)
                conn.commit()
                total = sum(counts.values())
                logger.info(f"session={sid}: 총 {total}건 삭제")
                for t, c in counts.items():
                    if c:
                        logger.info(f"  {t}: {c}건")

        if args.vacuum:
            logger.info("VACUUM 실행")
            conn.execute("VACUUM")
    finally:
        conn.close()


if __name__ == "__main__":
    main()