"""
verify_area_gate.py
서버 없이 RiskEvaluator.assess()를 시나리오 JSON에 태워 면적 기반 근접 판정 검증.
FE 배너 조건(연속 8프레임 이상 danger) 시뮬레이션 포함.
사용 예:
  python scripts/verify_area_gate.py --file 시나리오3.json --target Pillar
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.schemas import ScoringConfig
from app.services.risk_evaluator import RiskEvaluator


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--target", default="Pillar")
    ap.add_argument("--streak", type=int, default=8, help="FE 배너 연속 프레임 기준")
    args = ap.parse_args()

    ev = RiskEvaluator()
    ev.reload_config(ScoringConfig())
    print(f"area_danger_ratio={ev.area_danger_ratio} / area_warning_ratio={ev.area_warning_ratio}")

    with open(args.file, encoding="utf-8") as f:
        data = json.load(f)
    frames = data["frames"] if isinstance(data, dict) and "frames" in data else data

    danger_frames: list[int] = []
    target_rows: list[tuple] = []
    dist: dict[str, dict[str, int]] = {}

    for fr in frames:
        fid = fr["frame_id"]
        frame_danger = False
        for o in fr["objects"]:
            r = ev.assess(
                track_id=o["track_id"],
                class_id=o["class_id"],
                depth=o["depth_val"],
                is_moving=o["is_moving"],
                bbox_area_ratio=o["bbox_area_ratio"],
            )
            dist.setdefault(o["class_name"], {"danger": 0, "warning": 0, "safe": 0})[r] += 1
            if r == "danger":
                frame_danger = True
                if o["class_name"] == args.target:
                    target_rows.append((fid, round(o["bbox_area_ratio"], 3)))
        if frame_danger:
            danger_frames.append(fid)

    # 연속 구간 계산
    runs: list[tuple[int, int]] = []
    if danger_frames:
        s = p = danger_frames[0]
        for x in danger_frames[1:]:
            if x == p + 1:
                p = x
            else:
                runs.append((s, p))
                s = p = x
        runs.append((s, p))
    banner_runs = [(a, b) for a, b in runs if b - a + 1 >= args.streak]

    print(f"danger 프레임 수: {len(danger_frames)}/{len(frames)}")
    print(f"배너 구간({args.streak}연속 이상): {len(banner_runs)}개")
    for a, b in banner_runs:
        print(f"  프레임 {a}~{b} ({b - a + 1}프레임)")
    if target_rows:
        top = sorted(target_rows, key=lambda t: -t[1])[:5]
        print(f"[{args.target}] danger {len(target_rows)}건, 최대 점유 상위:")
        for fid, area in top:
            print(f"  frame={fid} area={area}")
    else:
        print(f"[{args.target}] danger 0건")
    print("클래스별 risk 분포:")
    for name in sorted(dist):
        d = dist[name]
        print(f"  {name:<18} danger={d['danger']:<5} warning={d['warning']:<5} safe={d['safe']}")


if __name__ == "__main__":
    main()