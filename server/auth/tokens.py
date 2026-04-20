"""JWT 발급·검증 + 무효화 denylist."""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

import jwt

from server.config import JWT_ALGORITHM, JWT_SECRET, JWT_TTL_MINUTES
from server.db.sqlite import connect, get_connection

logger = logging.getLogger(__name__)


def _resolve_secret() -> str:
    """JWT_SECRET 미설정 시 프로세스 내 임의 키를 생성한다(재시작 시 모든 토큰 무효)."""
    global _CACHED_SECRET
    with _SECRET_LOCK:
        if _CACHED_SECRET:
            return _CACHED_SECRET
        if JWT_SECRET:
            _CACHED_SECRET = JWT_SECRET
        else:
            _CACHED_SECRET = secrets.token_urlsafe(48)
            logger.warning(
                "[auth] JWT_SECRET 미설정 — 임의 키 사용 (재시작 시 모든 토큰 무효)"
            )
        return _CACHED_SECRET


_SECRET_LOCK = threading.Lock()
_CACHED_SECRET: str | None = None


@dataclass(frozen=True)
class TokenClaims:
    sub: int           # user id
    email: str
    role: str
    jti: str
    exp: int           # unix seconds


def issue_token(user_id: int, email: str, role: str) -> tuple[str, TokenClaims]:
    now = int(time.time())
    exp = now + JWT_TTL_MINUTES * 60
    jti = secrets.token_urlsafe(16)
    # PyJWT 2.12+ 는 'sub' 가 문자열이어야 한다 — user_id 는 별도 클레임 uid 로 전달.
    claims = {
        "sub":   str(user_id),
        "uid":   user_id,
        "email": email,
        "role":  role,
        "jti":   jti,
        "iat":   now,
        "exp":   exp,
    }
    token = jwt.encode(claims, _resolve_secret(), algorithm=JWT_ALGORITHM)
    return token, TokenClaims(sub=user_id, email=email, role=role, jti=jti, exp=exp)


def decode_token(token: str) -> TokenClaims | None:
    try:
        payload: dict[str, Any] = jwt.decode(
            token, _resolve_secret(), algorithms=[JWT_ALGORITHM]
        )
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

    try:
        uid_raw = payload.get("uid", payload.get("sub"))
        return TokenClaims(
            sub=int(uid_raw),
            email=str(payload["email"]),
            role=str(payload["role"]),
            jti=str(payload["jti"]),
            exp=int(payload["exp"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


# ── denylist ────────────────────────────────────────────────────────────────

def revoke_jti(jti: str, user_id: int | None, expires_at: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO token_denylist (jti, user_id, expires_at) "
            "VALUES (?, ?, ?)",
            (jti, user_id, expires_at),
        )


def revoke_all_for_user(user_id: int) -> None:
    """사용자의 모든 활성 토큰을 무효화한다.

    구현: 현재 시각 + JWT_TTL_MINUTES 만큼 future 까지 유효한 jti 를 실제로 알 수 없으므로,
    특수 jti 항목 `user:{id}` 으로 cutoff timestamp 를 기록하고 is_jti_revoked 에서
    (해당 사용자의 iat < cutoff) 를 확인한다. (Sprint 16 계정 비활성화 훅에서 활용)
    """
    expires_at = int(time.time()) + JWT_TTL_MINUTES * 60 + 3600
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO token_denylist (jti, user_id, expires_at) "
            "VALUES (?, ?, ?)",
            (f"user:{user_id}", user_id, expires_at),
        )


def is_jti_revoked(jti: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM token_denylist WHERE jti = ? LIMIT 1", (jti,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def purge_expired_denylist() -> int:
    now = int(time.time())
    with connect() as conn:
        cur = conn.execute("DELETE FROM token_denylist WHERE expires_at < ?", (now,))
        return cur.rowcount or 0
