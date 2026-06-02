# IDA 시연 운영 명령어 모음

**전제**
- Windows PowerShell, 작업 폴더: `D:\세종대\컴공\4학년1학기\캡스톤\IDA_Project\ida-backend`
- 한보림은 Colab에서 ngrok URL로 `POST /detect` push
- frame_id 베이스 0 가정 (한보림 0부터 전송) → 적재 시 `--start-index 0`
- 한 번에 한 시나리오씩 진행 (reaper 회피)
- 터미널 3개 사용: ① uvicorn  ② ngrok  ③ 나머지 명령 (`$sid`가 사는 창)

---

## 0. 사전 준비 (시연 시작 전 1회)

```powershell
# (코드 변경했으면) 테스트 먼저 - 전부 통과 확인
pytest tests/ -v

# 창① 서버 기동 (이 창은 점유됨, --reload 없이)
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 창② ngrok (이 창도 점유됨) -> 나오는 https URL을 한보림에게
ngrok http 8000

# 같은 LAN이면 IP로도 가능 - 내 IPv4 확인
ipconfig
```

**주의:** depth 코드(distance.py / risk_evaluator.py)를 반영하려면 **세션 만들기 전에** 서버를 재시작해야 한다. 재시작하면 메모리에 있던 활성 세션이 전부 비워진다.

---

## 1. 시나리오 1개: 세션 생성 + CAN 적재 (창③, 한보림 쏘기 직전)

```powershell
# 세션 생성 (scenario 없이 = 재생 모드)
$r = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/session/start" -ContentType "application/json" -Body '{"user_id":"demo_user","vehicle_id":"demo_veh","rental_id":"demo_rental"}'
$sid = $r.session_id; $sid

# CAN 적재 (아래 표에서 file/scenario 교체, --start-index 0)
python scripts/can_preload.py --db ida.db --session $sid --file test_scenario_3_CAN.json --scenario scenario_3 --start-index 0

# 정렬 검증 (NULL 0, frame_number 범위 표대로)
python scripts/check_can_align.py --db ida.db --session $sid
```

| 시나리오 | `--file` | `--scenario` | frame_number 범위 |
|---|---|---|---|
| 1 | test_scenario_1_CAN.json | scenario_1 | 0 ~ 1453 |
| 2 | test_scenario_2_CAN.json | scenario_2 | 0 ~ 823 |
| 3 | test_scenario_3_CAN.json | scenario_3 | 0 ~ 1251 |
| 4 | test_scenario_4_CAN.json | scenario_4 | 0 ~ 215 |

---

## 2. 한보림에게 전달

- 출력된 `$sid` + ngrok URL (예: `https://xxxx.ngrok-free.app`)
- 한보림은 `https://<ngrok>/detect`로 프레임별 POST
  - **frame_id 0부터** (우리 `--start-index 0`과 일치)
  - **`frame_image_b64` 포함** (FE 영상 재생용)

---

## 3. push 직후 검증 (베이스 0 확인 포함)

```powershell
# 점수: 깎이기 시작하면 base 0 맞음. 계속 100이면 base가 1 -> 8번 참고
Invoke-RestMethod "http://localhost:8000/score/$sid"

# 이벤트 / 로그 프레임 수 / 프레임 목록
Invoke-RestMethod "http://localhost:8000/events/$sid"
(Invoke-RestMethod "http://localhost:8000/logs?session_id=$sid&limit=2000").total_count
Invoke-RestMethod "http://localhost:8000/frames/$sid"
```

---

## 4. 세션 종료 + 리포트 (해당 시나리오 push 끝나면)

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/session/end" -ContentType "application/json" -Body (@{session_id=$sid} | ConvertTo-Json)
Invoke-RestMethod "http://localhost:8000/report/$sid"

# 리포트가 비어있으면 수동 생성 후 다시 GET
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/report/$sid"
Invoke-RestMethod "http://localhost:8000/report/$sid"
```

다음 시나리오는 1번부터 새 `$sid`로 반복. 종료하면 리포트(session_reports)가 저장되어 재시작 후에도 남는다.

---

## 5. 백업 (4개 다 적재/검증/종료 끝나면 1회)

```powershell
# 서버 끈 상태에서 복사 (WAL이 합쳐져서 ida.db 하나만 복사하면 됨)
Copy-Item ida.db ida_demo_backup.db
```

시연을 반복할 거면 이 백업이 안전판. 날짜별로 여러 개 떠도 됨(예: `ida_demo_backup_0601.db`).

---

## 6. 재시연 / 서버 재시작 후

서버를 껐다 켜도 DB(ida.db)는 그대로라 FE는 GET으로 전부 읽는다.

```powershell
Invoke-RestMethod "http://localhost:8000/frames/<sid>"
Invoke-RestMethod "http://localhost:8000/logs?session_id=<sid>&limit=2000"
Invoke-RestMethod "http://localhost:8000/events/<sid>"
Invoke-RestMethod "http://localhost:8000/score/<sid>"
Invoke-RestMethod "http://localhost:8000/report/<sid>"
```

**주의:** 재시작 후에는 그 세션에 **새 프레임 push 불가**(읽기 전용). 다시 녹화하려면 1번부터 세션을 새로 만들어 적재해야 한다. 저장된 session_id가 기억 안 나면 9번으로 확인.

---

## 7. depth 위험도 박스 튜닝

```powershell
# 현재 설정 확인
Invoke-RestMethod "http://localhost:8000/config"

# danger 임계 즉시 조절 (런타임 reload, 재시작 불필요). depth가 낮을수록 가까움
Invoke-RestMethod -Method Put -Uri "http://localhost:8000/config" -ContentType "application/json" -Body '{"proximity_distance": 0.85}'
```

- danger는 위 PUT으로 즉시 반영. **warning(0.93)은 코드값**이라 바꾸려면 `risk_evaluator.py` 수정 + 재시작.
- 이미 저장된 risk_level은 안 바뀜 -> 임계 바꾼 뒤 **새 push**부터 반영.

---

## 8. 문제 상황 대응

**세션이 404 (reaper 30분 경과 / 재시작으로 사라짐)**
→ 1번으로 세션을 다시 생성하고 적재.

**점수가 계속 100 (frame_id 베이스 안 맞음)**
base가 1이었던 경우. 해당 세션을 정리하고 1로 다시 적재한 뒤 한보림이 재전송.

```powershell
python scripts/cleanup_sessions.py --db ida.db --session $sid --yes
$r = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/session/start" -ContentType "application/json" -Body '{"user_id":"demo_user","vehicle_id":"demo_veh","rental_id":"demo_rental"}'
$sid = $r.session_id; $sid
python scripts/can_preload.py --db ida.db --session $sid --file test_scenario_3_CAN.json --scenario scenario_3 --start-index 1
```

**특정 세션만 삭제**
```powershell
python scripts/cleanup_sessions.py --db ida.db --session sess_xxxxxxxx --yes
```

**백업에서 복원 (서버 끈 상태)**
```powershell
Copy-Item ida_demo_backup.db ida.db
```

**[경고] 전체 세션 삭제 - 데모 데이터 전부 사라짐. 백업 없으면 절대 금지**
```powershell
python scripts/cleanup_sessions.py --db ida.db --all --yes --vacuum
```
계정/업체/차량/scoring_config/블랙리스트는 보존되지만, 4개 시연 세션(프레임/로그/이벤트/점수/리포트)은 전부 삭제된다.

---

## 9. 조회용 (아무때나)

```powershell
# 현재 활성 세션 목록 (메모리 기준)
Invoke-RestMethod "http://localhost:8000/"

# CAN 정렬 재확인
python scripts/check_can_align.py --db ida.db --session <sid>

# 헬스체크
Invoke-RestMethod "http://localhost:8000/health"
```

---

## 부록 — 시나리오 4개 한 번에 (선택)

**~30분 안에 4개 전부 push 가능할 때만.** 한 세션이라도 30분 내 push가 안 들어오면 reaper가 종료시킨다.

```powershell
$scenarios = @(
    @{ n = 1; file = "test_scenario_1_CAN.json"; scen = "scenario_1" },
    @{ n = 2; file = "test_scenario_2_CAN.json"; scen = "scenario_2" },
    @{ n = 3; file = "test_scenario_3_CAN.json"; scen = "scenario_3" },
    @{ n = 4; file = "test_scenario_4_CAN.json"; scen = "scenario_4" }
)

$sids = @{}
foreach ($s in $scenarios) {
    $r = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/session/start" -ContentType "application/json" -Body '{"user_id":"demo_user","vehicle_id":"demo_veh","rental_id":"demo_rental"}'
    $sid = $r.session_id
    $sids[$s.n] = $sid
    Write-Host "[시나리오 $($s.n)] 세션 생성: $sid"
    python scripts/can_preload.py --db ida.db --session $sid --file $s.file --scenario $s.scen --start-index 0
    python scripts/check_can_align.py --db ida.db --session $sid
}

Write-Host "`n=== 한보림에게 전달 (시나리오 -> session_id) ==="
foreach ($k in 1..4) { Write-Host "시나리오 $k  ->  $($sids[$k])" }
```
