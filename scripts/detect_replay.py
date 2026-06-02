"""
detect_replay.py
오프라인 저장된 탐지 결과 JSON을 로컬 /detect로 재생 적재.
포맷: {"frames": [payload, ...]} 또는 payload 리스트.
"""

import argparse
import json
import sys

import requests


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--url", default="http://localhost:8000/detect")
    ap.add_argument("--start-index", type=int, default=0)
    args = ap.parse_args()

    with open(args.file, encoding="utf-8") as f:
        data = json.load(f)

    frames = data["frames"] if isinstance(data, dict) and "frames" in data else data
    if not isinstance(frames, list):
        print("JSON은 payload 리스트이거나 {frames: [...]} 형태여야 함")
        sys.exit(1)

    total = len(frames)
    print(f"{total} 프레임 재생 시작: session={args.session}")

    ok = 0
    for i, fr in enumerate(frames):
        payload = dict(fr)
        payload["session_id"] = args.session
        if "frame_id" not in payload:
            payload["frame_id"] = args.start_index + i
        try:
            r = requests.post(args.url, json=payload, timeout=30)
            if r.status_code == 200:
                ok += 1
            else:
                print(f"frame {payload.get('frame_id')} 실패: {r.status_code} {r.text[:200]}")
        except requests.RequestException as e:
            print(f"frame {payload.get('frame_id')} 오류: {e}")
        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/{total}")

    print(f"완료: {ok}/{total} 성공")


if __name__ == "__main__":
    main()