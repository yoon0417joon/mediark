"""FastAPI 의존성 — 요청에서 현재 유저 추출 + 역할/권한 가드."""

from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, Request

from server.auth.roles import ROLE_LEVEL, role_at_least
from server.auth.tokens import decode_token, is_jti_revoked
from server.auth.users import (
    get_moderator_permissions,
    get_user_by_id,
    has_moderator_permission,
)
from server.config import SESSION_COOKIE_NAME


def extract_access_token(request: Request) -> str | None:
    """Authorization: Bearer 또는 HttpOnly 세션 쿠키에서 JWT 추출."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        t = auth[7:].strip()
        if t:
            return t
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if raw and str(raw).strip():
        return str(raw).strip()
    return None


def is_request_authenticated_jwt(request: Request) -> bool:
    """미들웨어용 — 유효한 JWT(미폐기)가 있으면 True."""
    token = extract_access_token(request)
    if not token:
        return False
    claims = decode_token(token)
    if claims is None:
        return False
    if is_jti_revoked(claims.jti):
        return False
    if is_jti_revoked(f"user:{claims.sub}"):
        return False
    return True


async def current_user(request: Request) -> dict | None:
    """유효한 JWT 가 있으면 user dict 반환. 없으면 익명 역할로 폴스루.

    반환 필드: id, email, role, is_active, (is_anon: True — 익명 전용).
    """
    token = extract_access_token(request)
    if not token:
        from server.auth.anon_access import get_effective_anon_role
        anon_role = get_effective_anon_role()
        if anon_role != "none":
            return {"id": None, "email": None, "role": anon_role, "is_active": True, "is_anon": True}
        return None
    claims = decode_token(token)
    if claims is None:
        return None
    if is_jti_revoked(claims.jti):
        return None
    if is_jti_revoked(f"user:{claims.sub}"):
        return None
    row = get_user_by_id(claims.sub)
    if row is None or not row["is_active"]:
        return None
    user = {
        "id":        int(row["id"]),
        "email":     str(row["email"]),
        "role":      str(row["role"]),
        "is_active": bool(row["is_active"]),
    }
    request.state.user = user
    return user


async def require_login(user: dict | None = Depends(current_user)) -> dict:
    if user is None:
        raise HTTPException(status_code=401, detail="인증이 필요합니다")
    return user


def require_role(minimum: str) -> Callable:
    if minimum not in ROLE_LEVEL:
        raise ValueError(f"알 수 없는 역할: {minimum}")

    async def _dep(user: dict = Depends(require_login)) -> dict:
        if not role_at_least(user["role"], minimum):
            raise HTTPException(status_code=403, detail="권한이 부족합니다")
        return user

    return _dep


def require_permission(permission: str) -> Callable:
    """admin 은 항상 통과, moderator 는 권한 테이블로 판정, 그 외 403."""

    async def _dep(user: dict = Depends(require_login)) -> dict:
        role = user["role"]
        if role == "admin":
            return user
        if role == "moderator" and has_moderator_permission(user["id"], permission):
            return user
        raise HTTPException(status_code=403, detail=f"권한 부족: {permission}")

    return _dep


def load_moderator_permissions(user_id: int) -> list[str]:
    return get_moderator_permissions(user_id)


def user_may_view_hidden_media(user: dict | None) -> bool:
    """숨김 미디어를 API 로 조회할 수 있는지 (admin 또는 media_hide 권한)."""
    if user is None:
        return False
    if user["role"] == "admin":
        return True
    if user["role"] == "moderator" and has_moderator_permission(user["id"], "media_hide"):
        return True
    return False
