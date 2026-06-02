"""scenario 4 재적재 + danger 검증 (한 번에)"""
import json
import uuid
from collections import Counter

import requests

BASE = "http://localhost:8000"   # ngrok로 확인하려면 여기만 교체
DETECT_FILE = "시나리오4.json"     # 파일 경로 맞춰줘

# 1) 세션 시작 (CAN 시뮬레이터는 안 켜고 depth만 본다)
r = requests.post(f"{BASE}/session/start", json={
    "user_id": "test_user",
    "vehicle_id": "test_car",
    "session_id": f"sess_test_{uuid.uuid4().hex[:8]}",
}, timeout=15)
r.raise_for_status()
sid = r.json()["session_id"]
print(f"세션: {sid}")

# 2) 프레임 재적재
data = json.load(open(DETECT_FILE, encoding="utf-8"))
frames = data["frames"] if isinstance(data, dict) and "frames" in data else data
ok = 0
for i, fr in enumerate(frames):
    payload = dict(fr)
    payload["session_id"] = sid
    payload.setdefault("frame_id", i)
    resp = requests.post(f"{BASE}/detect", json=payload, timeout=30)
    if resp.status_code == 200:
        ok += 1
    else:
        print(f"frame {payload.get('frame_id')} 실패: {resp.status_code} {resp.text[:150]}")
print(f"적재: {ok}/{len(frames)}")

# 3) danger 검증 (check.py와 동일 로직)
r = requests.get(f"{BASE}/logs", params={"session_id": sid, "limit": 1000}, timeout=15)
flogs = r.json()["frames"]
danger = [f["frame_number"] for f in flogs
          if any(o["risk_level"] == "danger" for o in f["objects"])]
print(f"총 프레임: {len(flogs)}")
print(f"danger 프레임 수: {len(danger)}")
print(f"danger 프레임: {danger}")

cc = Counter()
for f in flogs:
    for o in f["objects"]:
        if o["risk_level"] == "danger":
            cc[o.get("class_name", "?")] += 1
print(f"danger 유발 클래스: {dict(cc)}")