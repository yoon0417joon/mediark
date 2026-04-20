"""POST /auth/login, /auth/register, /auth/logout + GET /me, /me/permissions, /auth/registration-options."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from server.auth.deps import (
    current_user,
    extract_access_token,
    load_moderator_permissions,
    require_login,
)
from server.auth.passwords import verify_password
from server.auth.roles import ROLE_LEVEL
from server.auth.tokens import decode_token, issue_token, revoke_jti
from server.auth.registration_settings import get_effective_registration_policy
from server.auth.users import (
    claim_invite_code,
    create_user,
    get_user_by_email,
    normalize_email,
    touch_last_login,
)
from server.config import (
    JWT_TTL_MINUTES,
    LOGIN_RATE_LIMIT,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE,
)
from server.http_utils import client_ip
from server.rate_limit import rate_limit_bucket

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/auth/registration-options")
async def registration_options() -> dict:
    """가입 페이지용 — 인증 불필요. DB·env 를 반영한 공개 가입 여부와 초대 코드 필요 여부."""
    open_reg, role = await run_in_threadpool(get_effective_registration_policy)
    return {
        "open_registration":      open_reg,
        "open_registration_role": role,
        "invite_required":      not open_reg,
    }


class LoginRequest(BaseModel):
    email:    str = Field(min_length=3, max_length=256)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    email:       str = Field(min_length=3, max_length=256)
    password:    str = Field(min_length=8, max_length=256)
    invite_code: str | None = Field(default=None, max_length=256)


@router.post("/auth/login")
async def login(payload: LoginRequest, request: Request):
    rate_limit_bucket(client_ip(request), "login", LOGIN_RATE_LIMIT)

    row = await run_in_threadpool(lambda: get_user_by_email(payload.email))
    if row is None or not row["is_active"]:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다")

    ok = await run_in_threadpool(
        lambda: verify_password(payload.password, row["password_hash"])
    )
    if not ok:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다")

    user_id = int(row["id"])
    email   = str(row["email"])
    role    = str(row["role"])
    token, claims = issue_token(user_id=user_id, email=email, role=role)
    await run_in_threadpool(lambda: touch_last_login(user_id))

    body = {
        "access_token": token,
        "token_type":   "bearer",
        "expires_at":   claims.exp,
        "user": {
            "id":    user_id,
            "email": email,
            "role":  role,
        },
    }
    response = JSONResponse(content=body)
    # HttpOnly — <img src="/thumb/…"> 가 동일 출처에서 자동 전송
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=JWT_TTL_MINUTES * 60,
        path="/",
        samesite=SESSION_COOKIE_SAMESITE,
        secure=SESSION_COOKIE_SECURE,
    )
    return response


@router.post("/auth/register", status_code=201)
async def register(payload: RegisterRequest, request: Request) -> dict:
    """초대 코드 또는 공개 가입(DB·환경 설정) 시 무초대 가입."""
    rate_limit_bucket(client_ip(request), "register", LOGIN_RATE_LIMIT)

    email = normalize_email(payload.email)
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="유효하지 않은 이메일")
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="비밀번호는 최소 8자 이상이어야 합니다")

    existing = await run_in_threadpool(lambda: get_user_by_email(email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="이미 등록된 이메일입니다")

    invite = (payload.invite_code or "").strip()
    open_reg, reg_role = await run_in_threadpool(get_effective_registration_policy)
    use_open = open_reg and not invite

    if not use_open and not invite:
        raise HTTPException(status_code=400, detail="초대 코드가 필요합니다")

    def _do_register() -> dict:
        if use_open:
            role = reg_role
            if role not in ROLE_LEVEL or role in ("admin", "moderator"):
                raise HTTPException(
                    status_code=500,
                    detail="공개 가입 부여 역할이 유효하지 않습니다",
                )
            user_id = create_user(
                email=email,
                password=payload.password,
                role=role,
                is_active=True,
            )
            return {"id": user_id, "email": email, "role": role}

        # 초대 코드 유효성 선검증
        from server.db.sqlite import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT role, revoked_at, max_uses, use_count FROM invite_codes WHERE code = ?",
                (invite,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise HTTPException(status_code=400, detail="유효하지 않은 초대 코드")
        if row["revoked_at"] is not None:
            raise HTTPException(status_code=400, detail="회수된 초대 코드입니다")
        mu = row["max_uses"]
        uc = int(row["use_count"] or 0)
        if mu is not None and uc >= int(mu):
            raise HTTPException(status_code=400, detail="초대 코드 사용 횟수를 모두 소진했습니다")

        assigned_role = str(row["role"]) if str(row["role"]) in ROLE_LEVEL else "viewer"
        if assigned_role in ("admin", "moderator"):
            assigned_role = "viewer"

        user_id = create_user(
            email=email,
            password=payload.password,
            role=assigned_role,
            is_active=True,
        )
        claimed = claim_invite_code(invite, user_id)
        if claimed is None:
            from server.db.sqlite import connect

            with connect() as c:
                c.execute("DELETE FROM users WHERE id = ?", (user_id,))
            raise HTTPException(status_code=400, detail="초대 코드를 사용할 수 없습니다")
        return {"id": user_id, "email": email, "role": assigned_role}

    result = await run_in_threadpool(_do_register)
    return {"user": result}


@router.post("/auth/logout")
async def logout(request: Request, user: dict = Depends(require_login)):
    """Bearer 또는 세션 쿠키의 jti 를 denylist 에 추가하고 쿠키를 삭제한다."""
    token = extract_access_token(request)
    if token:
        claims = decode_token(token)
        if claims is not None:
            await run_in_threadpool(
                lambda: revoke_jti(claims.jti, claims.sub, claims.exp)
            )
    response = JSONResponse(content={"status": "ok"})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@router.get("/me")
async def me(user: dict = Depends(require_login)) -> dict:
    permissions: list[str] = []
    if user["role"] in ("moderator", "admin"):
        permissions = await run_in_threadpool(
            lambda: load_moderator_permissions(user["id"])
        )
    return {
        "id":          user["id"],
        "email":       user["email"],
        "role":        user["role"],
        "permissions": permissions if user["role"] == "moderator" else [],
    }


@router.get("/me/permissions")
async def my_permissions(user: dict = Depends(require_login)) -> dict:
    """로그인한 moderator 가 자신의 권한 목록을 조회. 그 외 역할은 빈 배열 또는 전체(admin)."""
    if user["role"] == "admin":
        # admin 은 모든 권한을 내포 — 응답은 빈 배열 + admin 플래그
        return {"role": "admin", "permissions": [], "all": True}
    if user["role"] == "moderator":
        perms = await run_in_threadpool(lambda: load_moderator_permissions(user["id"]))
        return {"role": "moderator", "permissions": perms, "all": False}
    return {"role": user["role"], "permissions": [], "all": False}


@router.get("/auth/whoami")
async def whoami(user: dict | None = Depends(current_user)) -> dict:
    """로그인 상태 확인용 — 비로그인 시 authenticated=false 반환(401 아님)."""
    if user is None:
        return {"authenticated": False}
    is_anon = bool(user.get("is_anon"))
    return {
        "authenticated": True,
        "is_anon": is_anon,
        "id":    user["id"],
        "email": user["email"],
        "role":  user["role"],
    }
