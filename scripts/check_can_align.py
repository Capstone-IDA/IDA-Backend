"""
scripts/check_can_align.py

검증용: 세션의 can_data_logs frame_number 적재 상태를 출력한다.
사용 예:
    python scripts/check_can_align.py --db ida.db --session SID
"""

import argparse
import sqlite3


def main() -> None:
    p = argparse.ArgumentParser(description="CAN 적재 정합 확인")
    p.add_argument("--db", required=True)
    p.add_argument("--session", required=True)
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    total = cur.execute(
        "SELECT COUNT(*) AS c FROM can_data_logs WHERE session_id = ?",
        (args.session,)
    ).fetchone()["c"]
    nulls = cur.execute(
        "SELECT COUNT(*) AS c FROM can_data_logs WHERE session_id = ? AND frame_number IS NULL",
        (args.session,)
    ).fetchone()["c"]
    rng = cur.execute(
        "SELECT MIN(frame_number) AS mn, MAX(frame_number) AS mx FROM can_data_logs WHERE session_id = ?",
        (args.session,)
    ).fetchone()

    print(f"can_data_logs 행 수: {total}")
    print(f"frame_number NULL 행: {nulls}  (0이어야 정상)")
    print(f"frame_number 범위: {rng['mn']} ~ {rng['mx']}")
    print("앞 5행 (frame_number, speed_kmh, acceleration, brake_intensity):")
    for r in cur.execute(
        """SELECT frame_number, speed_kmh, acceleration, brake_intensity
           FROM can_data_logs WHERE session_id = ?
           ORDER BY frame_number LIMIT 5""",
        (args.session,)
    ):
        print(f"  {r['frame_number']}, {r['speed_kmh']}, {r['acceleration']}, {r['brake_intensity']}")
    conn.close()


if __name__ == "__main__":
    main()