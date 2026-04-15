"""
IDA 시스템 - 통합 테스트
pytest + httpx AsyncClient 기반
모든 라우터 + 비즈니스 로직 검증
"""

import asyncio
import os
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# 테스트용 DB 파일 격리
os.environ.setdefault("IDA_DB_PATH", "test_ida.db")


@pytest_asyncio.fixture(scope="module")
async def client():
    """테스트용 AsyncClient (앱 라이프사이클 포함)"""
    # 기존 테스트 DB 삭제
    if os.path.exists("test_ida.db"):
        os.remove("test_ida.db")

    from app.main import app, app_state

    # DB 경로 오버라이드
    app_state.db.db_path = "test_ida.db"

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        # 수동 라이프사이클 시작
        await app_state.db.connect()
        await app_state.db.init_tables()

        from app.models.schemas import ScoringConfig
        config_row = await app_state.repo.get_config()
        if config_row:
            cfg = ScoringConfig(**{k: v for k, v in config_row.items() if k in ScoringConfig.model_fields})
            app_state.scorer.reload_config(cfg)
            app_state.risk_evaluator.reload_config(cfg)
            app_state.alert_manager.min_interval_sec = cfg.alert_min_interval_sec
        app_state.alert_manager.set_save_callback(app_state.repo.save_notification)

        yield ac

        # 정리
        app_state.can_simulator.stop()
        await app_state.db.disconnect()
        if os.path.exists("test_ida.db"):
            os.remove("test_ida.db")


# ══════════════════════════════════════
# 1. 서버 기본
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_root(client: AsyncClient):
    """루트 엔드포인트"""
    r = await client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["service"] == "IDA - Indoor Detection & Assistance"
    assert data["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    """GET /health"""
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["db_connected"] is True
    assert data["status"] == "healthy"


# ══════════════════════════════════════
# 2. Config
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_get_config(client: AsyncClient):
    """GET /config - 기본 설정 조회"""
    r = await client.get("/config")
    assert r.status_code == 200
    data = r.json()
    assert data["accel_threshold"] == 2.0
    assert data["green_min"] == 80
    assert data["blacklist_threshold"] == 30


@pytest.mark.asyncio
async def test_update_config(client: AsyncClient):
    """PUT /config - 부분 업데이트"""
    r = await client.put("/config", json={
        "accel_threshold": 2.5,
        "updated_by": "test_admin",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["accel_threshold"] == 2.5


@pytest.mark.asyncio
async def test_reset_config(client: AsyncClient):
    """POST /config/reset - 기본값 리셋"""
    r = await client.post("/config/reset")
    assert r.status_code == 200
    data = r.json()
    assert data["accel_threshold"] == 2.0


# ══════════════════════════════════════
# 3. CAN 시뮬레이터
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_can_scenarios(client: AsyncClient):
    """GET /can/scenarios"""
    r = await client.get("/can/scenarios")
    assert r.status_code == 200
    data = r.json()
    assert "normal" in data["scenarios"]
    assert "sudden_start" in data["scenarios"]
    assert "complex_danger" in data["scenarios"]
    assert len(data["scenarios"]) == 5


@pytest.mark.asyncio
async def test_can_start_stop(client: AsyncClient):
    """POST /can/start, /can/stop"""
    r = await client.post("/can/start/normal")
    assert r.status_code == 200
    assert r.json()["status"] == "started"

    # 데이터 생성 대기
    await asyncio.sleep(0.3)

    r = await client.get("/can/data")
    assert r.status_code == 200
    data = r.json()
    assert "speed_kmh" in data
    assert "acceleration" in data

    r = await client.post("/can/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"


@pytest.mark.asyncio
async def test_can_invalid_scenario(client: AsyncClient):
    """잘못된 시나리오명"""
    r = await client.post("/can/start/nonexistent")
    assert r.status_code == 400


# ══════════════════════════════════════
# 4. Session + Detection + Scoring 통합
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_session_lifecycle(client: AsyncClient):
    """세션 시작 → CAN 시작 → 탐지 → 점수 → 이벤트 → 종료 → 리포트"""

    # 4-1. 세션 시작
    r = await client.post("/session/start", json={
        "user_id": "test_user_01",
        "vehicle_id": "test_vehicle_01",
        "scenario": "sudden_start",
    })
    assert r.status_code == 200
    session_id = r.json()["session_id"]
    assert session_id.startswith("sess_")
    assert r.json()["initial_score"] == 100

    # 4-2. CAN 시작
    r = await client.post("/can/start/sudden_start")
    assert r.status_code == 200
    await asyncio.sleep(0.5)  # 데이터 생성 대기

    # 4-3. /detect 호출 (프레임 전송)
    fake_image = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # 가짜 이미지
    r = await client.post("/detect", files={
        "file": ("frame.png", fake_image, "image/png"),
    }, data={
        "frame_id": "1",
        "camera_id": "1",
    })
    assert r.status_code == 200
    detect_data = r.json()
    assert detect_data["frame_id"] == 1
    assert "score" in detect_data
    assert detect_data["score"]["current_score"] <= 100

    # 여러 프레임 전송
    for i in range(2, 6):
        await asyncio.sleep(0.15)
        r = await client.post("/detect", files={
            "file": ("frame.png", fake_image, "image/png"),
        }, data={"frame_id": str(i)})
        assert r.status_code == 200

    # 4-4. 점수 조회
    r = await client.get(f"/score/{session_id}")
    assert r.status_code == 200
    score_data = r.json()
    assert "current_score" in score_data
    assert "grade" in score_data

    # 4-5. 타임라인 조회
    r = await client.get(f"/score/{session_id}/timeline")
    assert r.status_code == 200
    timeline = r.json()["timeline"]
    assert isinstance(timeline, list)

    # 4-6. 이벤트 조회
    r = await client.get(f"/events/{session_id}")
    assert r.status_code == 200
    events = r.json()["events"]
    assert isinstance(events, list)

    # 4-7. CAN 중지
    r = await client.post("/can/stop")
    assert r.status_code == 200

    # 4-8. 세션 종료
    r = await client.post("/session/end", json={
        "session_id": session_id,
    })
    assert r.status_code == 200
    end_data = r.json()
    assert end_data["report_generated"] is True

    # 4-9. 리포트 조회
    r = await client.get(f"/report/{session_id}")
    assert r.status_code == 200
    report = r.json()
    assert report["session_id"] == session_id
    assert report["is_complete"] in (True, 1)

    # 4-10. 사용자 리포트 목록
    r = await client.get("/reports/test_user_01")
    assert r.status_code == 200
    reports = r.json()
    assert len(reports) >= 1

    return session_id  # 다음 테스트에서 활용


# ══════════════════════════════════════
# 5. 세션 없이 /detect 호출 시 에러
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_detect_no_session(client: AsyncClient):
    """활성 세션 없이 /detect → 400"""
    from app.main import app_state
    app_state.active_session_id = None

    fake_image = b"\x89PNG" + b"\x00" * 50
    r = await client.post("/detect", files={
        "file": ("f.png", fake_image, "image/png"),
    })
    assert r.status_code == 400


# ══════════════════════════════════════
# 6. Report 수동 생성
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_manual_report(client: AsyncClient):
    """POST /report/{session_id} 수동 생성"""
    # 먼저 세션 하나 만들기
    r = await client.post("/session/start", json={
        "user_id": "user_report_test",
        "vehicle_id": "v_01",
    })
    sid = r.json()["session_id"]

    await client.post("/session/end", json={"session_id": sid})

    r = await client.post(f"/report/{sid}")
    assert r.status_code == 200
    assert r.json()["session_id"] == sid


@pytest.mark.asyncio
async def test_report_not_found(client: AsyncClient):
    """존재하지 않는 세션 리포트"""
    r = await client.get("/report/nonexistent_session")
    assert r.status_code == 404


# ══════════════════════════════════════
# 7. Blacklist
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_blacklist_flow(client: AsyncClient):
    """블랙리스트 등록 → 조회 → 해제"""
    from app.main import app_state
    from app.models.schemas import BlacklistRecord

    record = BlacklistRecord(
        user_id="bl_user_01",
        session_id="bl_sess_01",
        final_score=20.0,
        blacklist_grade="blacklisted",
    )
    # 직접 DB에 세션+유저 먼저 생성
    await app_state.repo.create_session("bl_sess_01", "bl_user_01", "v_01")
    await app_state.repo.save_blacklist(record)

    # 전체 조회
    r = await client.get("/blacklist")
    assert r.status_code == 200
    bl_list = r.json()
    assert len(bl_list) >= 1

    # 사용자 조회
    r = await client.get("/blacklist/bl_user_01")
    assert r.status_code == 200
    assert r.json()["user_id"] == "bl_user_01"

    # 해제
    r = await client.delete("/blacklist/bl_user_01")
    assert r.status_code == 200
    assert r.json()["status"] == "removed"

    # 해제 후 조회 (is_active=0)
    r = await client.get("/blacklist/bl_user_01")
    assert r.status_code == 200
    assert r.json()["is_active"] in (False, 0)


@pytest.mark.asyncio
async def test_blacklist_not_found(client: AsyncClient):
    """존재하지 않는 사용자"""
    r = await client.get("/blacklist/no_such_user")
    assert r.status_code == 404


# ══════════════════════════════════════
# 8. Logs & Stats
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_get_logs(client: AsyncClient):
    """GET /logs"""
    r = await client.get("/logs")
    assert r.status_code == 200
    data = r.json()
    assert "total_count" in data
    assert "events" in data


@pytest.mark.asyncio
async def test_get_stats(client: AsyncClient):
    """GET /stats"""
    r = await client.get("/stats?period=24h")
    assert r.status_code == 200
    data = r.json()
    assert "total_frames" in data
    assert "risk_distribution" in data
    assert "class_distribution" in data


# ══════════════════════════════════════
# 9. DrivingScorer 단위 테스트
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_scorer_grade_boundaries():
    """등급 경계값 테스트 (AC007 일관성)"""
    from app.services.driving_scorer import DrivingScorer
    from app.models.schemas import ScoringConfig, DrivingEvent

    scorer = DrivingScorer()
    cfg = ScoringConfig()
    scorer.reload_config(cfg)
    scorer.reset("test")

    # 100점 → Green
    assert scorer.get_grade() == "Green"
    assert scorer.current_score == 100.0

    # 95점 → Green
    e = DrivingEvent(session_id="t", event_type="sudden_start",
                     speed=20, acceleration=3, deduction=5)
    scorer.apply_deduction(e)
    assert scorer.current_score == 95.0
    assert scorer.get_grade() == "Green"

    # 80점 → Green (경계)
    for _ in range(3):
        scorer.apply_deduction(DrivingEvent(
            session_id="t", event_type="sudden_brake",
            speed=20, acceleration=-3, deduction=5))
    assert scorer.current_score == 80.0
    assert scorer.get_grade() == "Green"

    # 79점 → Yellow
    scorer.apply_deduction(DrivingEvent(
        session_id="t", event_type="sudden_start",
        speed=20, acceleration=3, deduction=1))
    # current = 80 - 5 = 75 (deduction은 config 기준 5)
    assert scorer.get_grade() == "Yellow"

    # 0점 최소값 보장
    scorer.current_score = 3
    scorer.apply_deduction(DrivingEvent(
        session_id="t", event_type="overspeeding",
        speed=50, acceleration=0, deduction=8, is_proximate=True))
    assert scorer.current_score == 0.0
    assert scorer.get_grade() == "Red"


# ══════════════════════════════════════
# 10. RiskEvaluator 단위 테스트
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_risk_evaluator_events():
    """위험 이벤트 판정 정확성 (AC006)"""
    from app.services.risk_evaluator import RiskEvaluator
    from app.models.schemas import CANSnapshot, ScoringConfig
    from datetime import datetime

    evaluator = RiskEvaluator()
    cfg = ScoringConfig()
    evaluator.reload_config(cfg)

    # 급출발: acceleration >= 2.0
    snap = CANSnapshot(
        timestamp=datetime.utcnow(),
        speed_kmh=15, acceleration=3.5,
        brake_intensity=0, scenario="test")
    event = evaluator.evaluate_driving_event("s1", snap, "safe")
    assert event is not None
    assert event.event_type == "sudden_start"

    # 급제동: acceleration <= -2.0
    snap2 = CANSnapshot(
        timestamp=datetime.utcnow(),
        speed_kmh=20, acceleration=-3.0,
        brake_intensity=0.8, scenario="test")
    event2 = evaluator.evaluate_driving_event("s1", snap2, "danger")
    assert event2 is not None
    assert event2.event_type == "sudden_brake"
    assert event2.is_proximate is True
    assert event2.severity == "critical"

    # 과속: speed >= 30
    snap3 = CANSnapshot(
        timestamp=datetime.utcnow(),
        speed_kmh=35, acceleration=0.5,
        brake_intensity=0, scenario="test")
    event3 = evaluator.evaluate_driving_event("s1", snap3, "safe")
    assert event3 is not None
    assert event3.event_type == "overspeeding"

    # 정상: 이벤트 없음
    snap4 = CANSnapshot(
        timestamp=datetime.utcnow(),
        speed_kmh=15, acceleration=0.5,
        brake_intensity=0.1, scenario="test")
    event4 = evaluator.evaluate_driving_event("s1", snap4, "safe")
    assert event4 is None


# ══════════════════════════════════════
# 11. CAN 시뮬레이터 단위 테스트
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_can_simulator_unit():
    """CAN 시뮬레이터 시나리오 전환 + 데이터 생성"""
    from app.services.can_simulator import CANSimulator

    sim = CANSimulator()
    assert len(sim.list_scenarios()) == 5

    # 정상 시나리오
    sim.load_scenario("normal")
    for _ in range(10):
        snap = sim.generate_data()
        assert snap.speed_kmh >= 0
        assert snap.brake_intensity >= 0
        assert snap.scenario == "normal"

    # 잘못된 시나리오
    with pytest.raises(ValueError):
        sim.load_scenario("invalid_scenario")

    # 비동기 시작/중지
    sim.load_scenario("sudden_brake")
    sim.start()
    await asyncio.sleep(0.3)
    assert sim.is_running
    latest = sim.get_latest()
    assert latest is not None
    sim.stop()
    assert not sim.is_running


# ══════════════════════════════════════
# 12. AlertManager 단위 테스트
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_alert_manager_min_interval():
    """알림 폭주 방지 (min_interval)"""
    from app.services.alert_manager import AlertManager

    am = AlertManager(min_interval_sec=1)
    sent_logs = []

    async def mock_save(log):
        sent_logs.append(log)

    am.set_save_callback(mock_save)

    # 첫 알림 → 발송
    await am.on_grade_change("s1", "Yellow", 75.0)
    assert am.should_send("s1") is False  # 방금 보냈으므로

    # 즉시 재호출 → 스킵
    await am.on_grade_change("s1", "Orange", 45.0)
    # 1초 미만이므로 스킵됨

    # 1초 후 → 발송 가능
    await asyncio.sleep(1.1)
    assert am.should_send("s1") is True


# ══════════════════════════════════════
# 13. DB 동시성 테스트 (DB001, DB003)
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_db_concurrent_writes(client: AsyncClient):
    """동시 쓰기 충돌 없음 검증"""
    from app.main import app_state
    from app.models.schemas import CANSnapshot
    from datetime import datetime

    # 세션 생성
    await app_state.repo.create_session("conc_sess", "conc_user", "v_01")

    # 동시 30건 쓰기
    tasks = []
    for i in range(30):
        snap = CANSnapshot(
            timestamp=datetime.utcnow(),
            speed_kmh=float(i), acceleration=0.1 * i,
            brake_intensity=0, scenario="test")
        tasks.append(app_state.repo.save_can_data("conc_sess", snap))

    results = await asyncio.gather(*tasks)
    assert len(results) == 30
    assert all(r > 0 for r in results)


# ══════════════════════════════════════
# 14. 로그인 & 인증
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_login_admin(client: AsyncClient):
    """관리자 로그인 성공"""
    r = await client.post("/auth/login", json={
        "username": "admin",
        "password": "admin1234",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["role"] == "admin"
    assert data["token"]
    assert data["company_id"] is None


@pytest.mark.asyncio
async def test_login_company(client: AsyncClient):
    """업체 로그인 성공"""
    r = await client.post("/auth/login", json={
        "username": "sky_rental",
        "password": "sky1234",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["role"] == "company"
    assert data["company_id"] == "comp_sky"
    assert data["company_name"] == "스카이렌터카"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    """비밀번호 오류"""
    r = await client.post("/auth/login", json={
        "username": "admin",
        "password": "wrong_pass",
    })
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_user(client: AsyncClient):
    """존재하지 않는 계정"""
    r = await client.post("/auth/login", json={
        "username": "nobody",
        "password": "1234",
    })
    assert r.status_code == 401


# ══════════════════════════════════════
# 15. 인증 토큰 검증
# ══════════════════════════════════════

async def _get_token(client: AsyncClient, username: str, password: str) -> str:
    """로그인 후 토큰 반환 헬퍼"""
    r = await client.post("/auth/login", json={
        "username": username, "password": password,
    })
    return r.json()["token"]


@pytest.mark.asyncio
async def test_auth_me(client: AsyncClient):
    """GET /auth/me"""
    token = await _get_token(client, "admin", "admin1234")
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "admin"
    assert r.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_auth_no_token(client: AsyncClient):
    """토큰 없이 인증 필요 엔드포인트 호출 → 401"""
    r = await client.get("/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_auth_invalid_token(client: AsyncClient):
    """잘못된 토큰 → 401"""
    r = await client.get("/auth/me", headers={"Authorization": "Bearer invalid.token"})
    assert r.status_code == 401


# ══════════════════════════════════════
# 16. Admin 계정 관리
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_admin_create_account(client: AsyncClient):
    """Admin이 업체 계정 생성"""
    token = await _get_token(client, "admin", "admin1234")
    r = await client.post("/auth/accounts", json={
        "username": "busan_rental",
        "password": "busan1234",
        "role": "company",
        "company_id": "comp_busan",
        "company_name": "부산렌터카",
        "contact": "051-111-2222",
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "busan_rental"
    assert r.json()["company_id"] == "comp_busan"


@pytest.mark.asyncio
async def test_company_cannot_create_account(client: AsyncClient):
    """Company 역할은 계정 생성 불가 → 403"""
    token = await _get_token(client, "sky_rental", "sky1234")
    r = await client.post("/auth/accounts", json={
        "username": "new_company",
        "password": "1234",
        "role": "company",
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_list_accounts(client: AsyncClient):
    """Admin 전체 계정 목록 조회"""
    token = await _get_token(client, "admin", "admin1234")
    r = await client.get("/auth/accounts", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    accounts = r.json()
    usernames = [a["username"] for a in accounts]
    assert "admin" in usernames
    assert "sky_rental" in usernames


@pytest.mark.asyncio
async def test_admin_list_companies(client: AsyncClient):
    """Admin 전체 업체 목록"""
    token = await _get_token(client, "admin", "admin1234")
    r = await client.get("/auth/companies", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    companies = r.json()
    names = [c["name"] for c in companies]
    assert "스카이렌터카" in names
    assert "제주렌터카" in names


# ══════════════════════════════════════
# 17. CompanyDashboard — 데이터 격리
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_company_dashboard_scoped(client: AsyncClient):
    """업체 대시보드 — 자기 데이터만 조회"""
    from app.main import app_state

    # 스카이렌터카 세션 생성
    await app_state.repo.create_session(
        "sky_sess_01", "driver_a", "sky_v01",
        scenario="normal", company_id="comp_sky"
    )
    # 제주렌터카 세션 생성
    await app_state.repo.create_session(
        "jeju_sess_01", "driver_b", "jeju_v01",
        scenario="normal", company_id="comp_jeju"
    )

    # 스카이렌터카 로그인 → 세션 조회
    sky_token = await _get_token(client, "sky_rental", "sky1234")
    r = await client.get("/company/sessions",
                         headers={"Authorization": f"Bearer {sky_token}"})
    assert r.status_code == 200
    sessions = r.json()
    # 스카이 소속 세션만 나와야 함
    for s in sessions:
        assert s.get("company_id") == "comp_sky"

    # 제주렌터카 로그인 → 세션 조회
    jeju_token = await _get_token(client, "jeju_rental", "jeju1234")
    r = await client.get("/company/sessions",
                         headers={"Authorization": f"Bearer {jeju_token}"})
    assert r.status_code == 200
    sessions_jeju = r.json()
    for s in sessions_jeju:
        assert s.get("company_id") == "comp_jeju"


@pytest.mark.asyncio
async def test_company_dashboard_summary(client: AsyncClient):
    """업체 대시보드 요약"""
    token = await _get_token(client, "sky_rental", "sky1234")
    r = await client.get("/company/dashboard",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert "active_sessions" in data
    assert "total_sessions" in data
    assert data["company_id"] == "comp_sky"


# ══════════════════════════════════════
# 18. AdminDashboard — 업체별 필터링
# ══════════════════════════════════════

@pytest.mark.asyncio
async def test_admin_dashboard_all(client: AsyncClient):
    """Admin 대시보드 — 전체 조회"""
    token = await _get_token(client, "admin", "admin1234")
    r = await client.get("/admin/dashboard",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert "companies" in data
    assert data["filter_company_id"] is None
    assert data["total_sessions"] >= 2  # sky + jeju


@pytest.mark.asyncio
async def test_admin_dashboard_filtered(client: AsyncClient):
    """Admin 대시보드 — 특정 업체 필터"""
    token = await _get_token(client, "admin", "admin1234")
    r = await client.get("/admin/dashboard?company_id=comp_sky",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert data["filter_company_id"] == "comp_sky"


@pytest.mark.asyncio
async def test_admin_sessions_filtered(client: AsyncClient):
    """Admin 세션 조회 — 업체 필터"""
    token = await _get_token(client, "admin", "admin1234")

    # 전체
    r = await client.get("/admin/sessions",
                         headers={"Authorization": f"Bearer {token}"})
    all_sessions = r.json()

    # 스카이만
    r = await client.get("/admin/sessions?company_id=comp_sky",
                         headers={"Authorization": f"Bearer {token}"})
    sky_only = r.json()

    assert len(all_sessions) >= len(sky_only)
    for s in sky_only:
        assert s.get("company_id") == "comp_sky"


@pytest.mark.asyncio
async def test_company_cannot_access_admin(client: AsyncClient):
    """Company 역할은 Admin 엔드포인트 접근 불가 → 403"""
    token = await _get_token(client, "sky_rental", "sky1234")
    r = await client.get("/admin/dashboard",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
