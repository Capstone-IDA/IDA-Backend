"""시나리오 다건 병렬 적재. 파일별 depth 컨벤션 자동 판별 후 high=close로 정규화."""
import json
import statistics as st
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE = "http://localhost:8000"

# (파일 경로, 시나리오 라벨) - 실제 파일명/경로에 맞춰
SCENARIOS = [
    ("시나리오1.json", "scenario_1"),
    ("시나리오2.json", "scenario_2"),
    ("시나리오3.json", "scenario_3"),
]


def detect_convention(frames):
    """bbox_area와 depth의 상관으로 컨벤션 판별. corr>0이면 high=close."""
    tracks = defaultdict(list)
    for f in frames:
        for o in f.get("objects", []):
            tracks[o["track_id"]].append((o["bbox_area_ratio"], o["depth_val"]))
    vals = []
    for seq in tracks.values():
        areas = [s[0] for s in seq]
        depths = [s[1] for s in seq]
        if len(seq) < 5 or max(areas) - min(areas) < 0.02:
            continue
        mx, my = st.mean(areas), st.mean(depths)
        dx = sum((x - mx) ** 2 for x in areas) ** 0.5
        dy = sum((y - my) ** 2 for y in depths) ** 0.5
        if dx == 0 or dy == 0:
            continue
        vals.append(sum((x - mx) * (y - my) for x, y in zip(areas, depths)) / (dx * dy))
    return st.mean(vals) if vals else 0.0


def ingest(path, label):
    """한 시나리오를 한 세션에 순서대로 적재."""
    data = json.load(open(path, encoding="utf-8"))
    frames = data["frames"] if isinstance(data, dict) and "frames" in data else data

    corr = detect_convention(frames)
    flip = corr < 0   # low=close면 뒤집어 high=close로 정규화
    print(f"[{label}] corr={corr:+.3f} -> {'low=close, 뒤집음' if flip else 'high=close, 그대로'}")

    r = requests.post(f"{BASE}/session/start", json={
        "user_id": f"user_{label}",
        "vehicle_id": f"car_{label}",
        "scenario": label,
        "session_id": f"sess_{label}_{uuid.uuid4().hex[:6]}",
    }, timeout=15)
    r.raise_for_status()
    sid = r.json()["session_id"]

    ok = 0
    for i, fr in enumerate(frames):
        payload = dict(fr)
        payload["session_id"] = sid
        payload.setdefault("frame_id", i)
        if flip:
            payload["objects"] = [
                {**o, "depth_val": round(1.0 - o["depth_val"], 4)}
                for o in payload.get("objects", [])
            ]
        resp = requests.post(f"{BASE}/detect", json=payload, timeout=30)
        if resp.status_code == 200:
            ok += 1
        else:
            print(f"[{label}] frame {payload.get('frame_id')} 실패: {resp.status_code}")

    lr = requests.get(f"{BASE}/logs", params={"session_id": sid, "limit": 2000}, timeout=20)
    flogs = lr.json()["frames"]
    danger = sum(1 for f in flogs if any(o["risk_level"] == "danger" for o in f["objects"]))
    return label, sid, ok, len(frames), danger


def main():
    print(f"{len(SCENARIOS)}개 시나리오 병렬 적재 시작\n")
    results = []
    with ThreadPoolExecutor(max_workers=len(SCENARIOS)) as ex:
        futures = [ex.submit(ingest, path, label) for path, label in SCENARIOS]
        for fut in as_completed(futures):
            results.append(fut.result())

    print("\n=== 적재 완료 ===")
    for label, sid, ok, total, danger in sorted(results):
        print(f"{label}: session={sid}  적재={ok}/{total}  danger프레임={danger}")


if __name__ == "__main__":
    main()
    