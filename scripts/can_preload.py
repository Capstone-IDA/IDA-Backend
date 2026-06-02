"""
scripts/can_preload.py

AIHub CAN JSON 파일을 can_data_logs 테이블에 사전 적재한다.
사용 예:
    python scripts/can_preload.py \\
        --db ida.db \\
        --session SESSION_ID \\
        --file test_scenario_1_CAN.json \\
        --scenario scenario_1

선택 인자:
    --brake-max   brake_pressure 정규화 기준 최대값 (기본 80)
    --fps         프레임 레이트 (기본 23.8, = 42ms 간격)
    --dry-run     DB INSERT 없이 변환 결과만 출력
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

FRAME_INTERVAL_MS = 42  # 실측 42ms 간격
DEFAULT_BRAKE_MAX = 80  # 실측 최대 brake_pressure


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AIHub CAN JSON -> can_data_logs 적재")
    p.add_argument("--db", required=True, help="SQLite DB 경로 (예: ida.db)")
    p.add_argument("--session", required=True, help="적재 대상 session_id")
    p.add_argument("--file", required=True, help="CAN JSON 파일 경로")
    p.add_argument("--scenario", default="file_playback", help="시나리오 레이블 (기본 file_playback)")
    p.add_argument("--brake-max", type=float, default=DEFAULT_BRAKE_MAX,
                   help=f"brake_pressure 정규화 기준 최대값 (기본 {DEFAULT_BRAKE_MAX})")
    p.add_argument("--dry-run", action="store_true", help="INSERT 없이 변환 결과만 출력")
    p.add_argument("--start-index", type=int, default=0,
                   help="첫 프레임 frame_number (AI frame_id 시작값과 일치, 기본 0)")
    return p.parse_args()


def load_can_json(path: str) -> list[dict]:
    """CAN JSON 파일 로드, frames 배열 반환"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "frames" not in data:
        raise ValueError(f"'frames' 키가 없습니다: {path}")
    return data["frames"]


def build_base_time(sys_time: dict) -> datetime:
    """sys_time 딕셔너리 -> datetime 변환"""
    year = 2000 + sys_time["year"]
    return datetime(year, sys_time["month"], sys_time["day"],
                    sys_time["hour"], sys_time["minute"], sys_time["second"])


def convert_frame(frame: dict, base_time: datetime,
                  frame_idx: int, brake_max: float,
                  scenario: str) -> dict:
    """
    AIHub 프레임 한 건을 CANSnapshot 호환 딕셔너리로 변환.

    매핑:
        speed_kmh      <- aim_micom.gps_vss         (단위: km/h, 직접 사용)
        acceleration   <- aim_gsensor.accYAve        (단위: m/s², 전후 방향)
        brake_intensity <- aim_micom.brake_pressure / brake_max  (0.0 ~ 1.0)
    """
    gsensor = frame.get("aim_gsensor", {})
    micom = frame.get("aim_micom", {})

    speed_kmh = float(micom.get("gps_vss", 0))
    acc_y = float(gsensor.get("accYAve", 0.0))
    brake_raw = float(micom.get("brake_pressure", 0))
    brake_intensity = min(1.0, brake_raw / brake_max) if brake_max > 0 else 0.0

    ts = base_time + timedelta(milliseconds=frame_idx * FRAME_INTERVAL_MS)

    return {
        "timestamp": ts.isoformat(),
        "speed_kmh": round(speed_kmh, 2),
        "acceleration": round(acc_y, 3),
        "brake_intensity": round(brake_intensity, 4),
        "scenario": scenario,
    }


async def insert_rows(db_path: str, session_id: str, rows: list[dict]) -> int:
    """can_data_logs 테이블에 배치 INSERT, 삽입 건수 반환"""
    try:
        import aiosqlite
    except ImportError:
        logger.error("aiosqlite 미설치: pip install aiosqlite")
        sys.exit(1)

    inserted = 0
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        for row in rows:
            await conn.execute(
                """INSERT INTO can_data_logs
                   (session_id, timestamp, speed_kmh, acceleration,
                    brake_intensity, scenario, frame_number)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    row["timestamp"],
                    row["speed_kmh"],
                    row["acceleration"],
                    row["brake_intensity"],
                    row["scenario"],
                    row["frame_number"],
                ),
            )
            inserted += 1
        await conn.commit()
    return inserted


async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    args = parse_args()

    can_path = Path(args.file)
    if not can_path.exists():
        logger.error(f"파일 없음: {args.file}")
        sys.exit(1)

    logger.info(f"CAN JSON 로드: {can_path}")
    frames = load_can_json(str(can_path))
    logger.info(f"총 {len(frames)} 프레임")

    # 첫 프레임의 sys_time으로 기준 시각 설정
    base_time = build_base_time(frames[0]["sys_time"])
    logger.info(f"기준 시각: {base_time.isoformat()}")

    rows = []
    for idx, frame in enumerate(frames):
        row = convert_frame(frame, base_time, idx, args.brake_max, args.scenario)
        row["frame_number"] = idx + args.start_index
        rows.append(row)

    if args.dry_run:
        print(f"[dry-run] 변환 결과 (첫 5건):")
        for r in rows[:5]:
            print(f"  {r}")
        print(f"  ... 총 {len(rows)}건 (INSERT 스킵)")
        return

    db_path = Path(args.db)
    if not db_path.exists():
        logger.error(f"DB 파일 없음: {args.db}  (uvicorn 먼저 실행하여 DB를 초기화하세요)")
        sys.exit(1)

    logger.info(f"DB INSERT 시작: session={args.session}, db={args.db}")
    count = await insert_rows(str(db_path), args.session, rows)
    logger.info(f"INSERT 완료: {count}건")


if __name__ == "__main__":
    asyncio.run(main())