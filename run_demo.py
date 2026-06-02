"""run_demo.py - 시연 완성본: 시나리오별 세션 시작 -> CAN 적재 -> detection 적재 -> 세션 종료 -> 리포트."""
import json
import statistics as st
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

import requests

BASE = "http://localhost:8000"
DB_PATH = "ida.db"
CAN_PRELOAD = "scripts/can_preload.py"

# (detection 파일, CAN 파일, 라벨) - 경로는 실제 위치에 맞춰
SCENARIOS = [
    ("시나리오1.json", "test_scenario_1_CAN.json", "scenario_1"),
    ("시나리오2.json", "test_scenario_2_CAN.json", "scenario_2"),
    ("시나리오3.json", "test_scenario_3_CAN.json", "scenario_3"),
    ("시나리오4.json", "test_scenario_4_CAN.json", "scenario_4"),
]


def detect_convention(frames):
    """bbox 면적 vs depth 상관계수. 음수면 low=close라 flip 필요."""
    tracks = defaultdict(list)
    for f in frames:
        for o in f.get("objects", []):
            b = o["bbox"]
            tracks[o["track_id"]].append((b["w"] * b["h"], o["depth_val"]))
    vals = []
    for seq in tracks.values():
        if len(seq) < 5:
            continue
        areas = [s[0] for s in seq]
        depths = [s[1] for s in seq]
        if max(areas) - min(areas) < 0.02:
            continue
        mx, my = st.mean(areas), st.mean(depths)
        dx = sum((x - mx) ** 2 for x in areas) ** 0.5
        dy = sum((y - my) ** 2 for y in depths) ** 0.5
        if dx == 0 or dy == 0:
            continue
        vals.append(sum((x - mx) * (y - my) for x, y in zip(areas, depths)) / (dx * dy))
    return st.mean(vals) if vals else 0.0


def process(det_file, can_file, label, sid):
    print(f"\n[{label}] 세션 {sid}")

    # 1) 세션 시작 (런타임 ctx 생성). 미지의 scenario는 sim에서 무시됨
    requests.post(f"{BASE}/session/start", json={
        "user_id": f"user_{label}", "vehicle_id": f"car_{label}",
        "scenario": label, "session_id": sid,
    }, timeout=15).raise_for_status()

    # 2) CAN 적재 (frame_number 0-base 정렬). detection보다 먼저 들어가야 매칭됨
    print(f"[{label}] CAN 적재...")
    subprocess.run([
        sys.executable, CAN_PRELOAD,
        "--db", DB_PATH, "--session", sid,
        "--file", can_file, "--scenario", label,
    ], check=True)

    # 3) detection 적재 (컨벤션 자동 정규화, frame_id 강제 0-base로 CAN과 정렬)
    data = json.load(open(det_file, encoding="utf-8"))
    frames = data["frames"] if isinstance(data, dict) and "frames" in data else data
    flip = detect_convention(frames) < 0
    print(f"[{label}] detection {len(frames)}프레임 적재 (flip={flip})")
    ok = 0
    for i, fr in enumerate(frames):
        payload = dict(fr)
        payload["session_id"] = sid
        payload["frame_id"] = i  # CAN frame_number와 정렬
        if flip:
            payload["objects"] = [
                {**o, "depth_val": round(1.0 - o["depth_val"], 4)}
                for o in payload.get("objects", [])
            ]
        if requests.post(f"{BASE}/detect", json=payload, timeout=30).status_code == 200:
            ok += 1
    print(f"[{label}] 적재 완료 {ok}/{len(frames)}")

    # 4) 세션 종료 -> 리포트 생성
    rep = requests.post(f"{BASE}/session/end", json={"session_id": sid}, timeout=60)
    rep.raise_for_status()
    return label, sid, rep.json()


def main():
    runtag = datetime.now().strftime("%m%d_%H%M%S")
    jobs = [(d, c, lb, f"sess_{lb}_{runtag}") for d, c, lb in SCENARIOS]

    print("이번 시연 세션 ID:")
    for d, c, lb, sid in jobs:
        print(f"  {lb}: {sid}")

    results = [process(d, c, lb, sid) for d, c, lb, sid in jobs]

    print("\n=== 시연 적재 완료 ===")
    for label, sid, r in results:
        print(f"{label} ({sid})")
        print(f"   final_score={r.get('final_score')}  grade={r.get('grade')}  report={r.get('report_generated')}")


if __name__ == "__main__":
    main()