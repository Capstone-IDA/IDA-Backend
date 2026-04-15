"""
인증 서비스
JWT 토큰 기반 로그인 + 비밀번호 해싱
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# 캡스톤 범위: 간단한 시크릿 키 (실제 환경에서는 환경변수 사용)
SECRET_KEY = "ida-capstone-secret-key-2026"
TOKEN_EXPIRE_HOURS = 24


def hash_password(password: str) -> str:
    """비밀번호 SHA-256 해싱 (캡스톤 범위 간소화)"""
    return hashlib.sha256(f"{password}{SECRET_KEY}".encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    """비밀번호 검증"""
    return hash_password(password) == password_hash


def create_token(account_id: str, role: str, company_id: Optional[str] = None) -> str:
    """간단한 JWT-like 토큰 생성"""
    payload = {
        "account_id": account_id,
        "role": role,
        "company_id": company_id,
        "exp": int(time.time()) + TOKEN_EXPIRE_HOURS * 3600,
    }
    payload_json = json.dumps(payload, sort_keys=True)
    signature = hmac.new(
        SECRET_KEY.encode(), payload_json.encode(), hashlib.sha256
    ).hexdigest()

    import base64
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
    return f"{payload_b64}.{signature}"


def decode_token(token: str) -> Optional[dict]:
    """토큰 디코딩 및 검증"""
    try:
        import base64
        parts = token.split(".")
        if len(parts) != 2:
            return None

        payload_b64, signature = parts
        payload_json = base64.urlsafe_b64decode(payload_b64.encode()).decode()

        # 서명 검증
        expected_sig = hmac.new(
            SECRET_KEY.encode(), payload_json.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None

        payload = json.loads(payload_json)

        # 만료 확인
        if payload.get("exp", 0) < time.time():
            return None

        return payload
    except Exception as e:
        logger.debug(f"토큰 디코딩 실패: {e}")
        return None
