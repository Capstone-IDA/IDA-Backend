"""
scripts/post_detect_test.py

검증용: 빈 객체 프레임을 /detect로 연속 POST하여 CAN 정합과 스코어링을 확인한다.
사용 예:
    python scripts/post_detect_test.py --session SID --frames 300 --start 0
"""

import argparse
import json
import urllib.request
from datetime import datetime, timezone


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="detect 프레임 연속 POST (검증용)")
    p.add_argument("--url", default="http://localhost:8000", help="서버 베이스 URL")
    p.add_argument("--session", required=True, help="대상 session_id")
    p.add_argument("--frames", type=int, default=300, help="POST할 프레임 수")
    p.add_argument("--start", type=int, default=0, help="시작 frame_id (can_preload의 start-index와 일치)")
    return p.parse_args()


def post_frame(url: str, session_id: str, frame_id: int) -> int:
    payload = {
        "frame_id": frame_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fps": 23.8,
        "inference_time_ms": 20.0,
        "session_id": session_id,
        "objects": [],
        "ego_motion": {"vx": 0.0, "vy": 0.0, "speed": 0.0},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{url}/detect", data=data,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status


def main() -> None:
    args = parse_args()
    ok = 0
    for i in range(args.frames):
        fid = args.start + i
        try:
            if post_frame(args.url, args.session, fid) == 200:
                ok += 1
        except Exception as e:
            print(f"frame {fid} 실패: {e}")
            break
    print(f"POST 완료: {ok}/{args.frames} 프레임")


if __name__ == "__main__":
    main()