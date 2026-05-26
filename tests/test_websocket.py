"""
WebSocket 라우터 통합 테스트 (session_id 기반 검증)
Starlette TestClient (sync) 기반 — websocket_connect 사용
"""

import os
from datetime import datetime
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

os.environ.setdefault("IDA_DB_PATH", "test_ida.db")


# ══════════════════════════════════════
# Fixtures & helpers
# ══════════════════════════════════════

@pytest.fixture(scope="module")
def ws_client():
    """sync TestClient — lifespan 자동 트리거"""
    from app.main import app
    with TestClient(app) as client:
        yield client


def _start_session(client: TestClient, user_id: str,
                   scenario: Optional[str] = None) -> str:
    """세션 시작 후 session_id 반환"""
    body = {"user_id": user_id, "vehicle_id": f"veh_{user_id}"}
    if scenario:
        body["scenario"] = scenario
    r = client.post("/session/start", json=body)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _end_session(client: TestClient, session_id: str) -> None:
    """세션 정리"""
    client.post("/session/end", json={"session_id": session_id})


def _detection_msg(frame_id: int, depth: float = 0.5,
                   is_moving: bool = True) -> dict:
    """detection 메시지 생성"""
    return {
        "type": "detection",
        "frame_id": frame_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "fps": 30.0,
        "inference_time_ms": 25.0,
        "ego_motion": {"vx": 0.0, "vy": 0.0, "speed": 0.0},
        "objects": [
            {
                "track_id": 1,
                "class_id": 14,
                "class_name": "Vehicle",
                "confidence": 0.9,
                "bbox": {"x": 0.5, "y": 0.5, "w": 0.2, "h": 0.2},
                "depth_val": depth,
                "bbox_area_ratio": 0.04,
                "bbox_velocity_x": 0.01,
                "bbox_velocity_y": 0.0,
                "obj_speed_px": 0.01,
                "is_moving": is_moving,
            }
        ],
    }


# ══════════════════════════════════════
# 핸드셰이크 거부 케이스
# ══════════════════════════════════════

def test_ws_session_id_mismatch(ws_client: TestClient):
    """path의 session_id가 active와 다르면 1008"""
    session_id = _start_session(ws_client, "u_mismatch")
    try:
        with pytest.raises(WebSocketDisconnect) as exc:
            with ws_client.websocket_connect(
                "/ws/detect/sess_doesnt_exist"
            ) as ws:
                ws.receive_json()
        assert exc.value.code == 1008
    finally:
        _end_session(ws_client, session_id)


def test_ws_no_active_session(ws_client: TestClient):
    """존재하지 않는 session_id로 연결 시 1008"""
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect("/ws/detect/sess_any") as ws:
            ws.receive_json()
    assert exc.value.code == 1008


# ══════════════════════════════════════
# 정상 흐름
# ══════════════════════════════════════

def test_ws_connected_message(ws_client: TestClient):
    """정상 연결 시 connected 메시지 수신"""
    session_id = _start_session(ws_client, "u_connect")
    try:
        with ws_client.websocket_connect(f"/ws/detect/{session_id}") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "connected"
            assert msg["session_id"] == session_id
            assert "server_time" in msg
    finally:
        _end_session(ws_client, session_id)


def test_ws_invalid_payload(ws_client: TestClient):
    """잘못된 payload 보내면 error, 연결은 유지"""
    session_id = _start_session(ws_client, "u_invalid")
    try:
        with ws_client.websocket_connect(f"/ws/detect/{session_id}") as ws:
            ws.receive_json()  # connected

            # 알 수 없는 타입
            ws.send_json({"type": "nonsense"})
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["code"] == "INVALID_PAYLOAD"

            # 필수 필드 빠진 detection
            ws.send_json({"type": "detection", "frame_id": 1})
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["code"] == "INVALID_PAYLOAD"
    finally:
        _end_session(ws_client, session_id)


def test_ws_detection_to_event_push(ws_client: TestClient):
    """detection 송신 후 CAN 이벤트 발생 시 driving_event push 수신"""
    from app.models.schemas import CANSnapshot
    from app.main import app_state

    # scenario 미지정 — simulator 비활성 상태에서 buffer만 직접 제어
    session_id = _start_session(ws_client, "u_event")

    # 세션 컨텍스트의 simulator stop + buffer 초기화 + 임계 도달 스냅샷 주입 (deterministic)
    ctx = app_state.sessions[session_id]
    ctx.can_simulator.stop()
    ctx.can_simulator.buffer.clear()
    ctx.can_simulator.buffer.append(CANSnapshot(
        timestamp=datetime.utcnow(),
        speed_kmh=20.0,
        acceleration=4.5,  # accel_threshold(2.0) 초과
        brake_intensity=0.0,
        scenario="sudden_start",
    ))

    try:
        with ws_client.websocket_connect(f"/ws/detect/{session_id}") as ws:
            ws.receive_json()  # connected

            ws.send_json(_detection_msg(frame_id=1, depth=0.1, is_moving=True))

            msg = ws.receive_json()
            assert msg["type"] == "driving_event"
            assert msg["frame_id"] == 1
            assert msg["event"]["event_type"] == "sudden_start"
            assert msg["event"]["is_proximate"] is True
            assert msg["event"]["deduction"] > 0
            assert "score" in msg
            assert msg["score"]["current_score"] < 100
    finally:
        _end_session(ws_client, session_id)


def test_ws_session_end_message(ws_client: TestClient):
    """session_end 보내면 session_closed 받고 연결 종료"""
    session_id = _start_session(ws_client, "u_sessend")
    try:
        with ws_client.websocket_connect(f"/ws/detect/{session_id}") as ws:
            ws.receive_json()  # connected

            ws.send_json({"type": "session_end"})
            msg = ws.receive_json()
            assert msg["type"] == "session_closed"
            assert "final_score" in msg

            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    finally:
        _end_session(ws_client, session_id)
