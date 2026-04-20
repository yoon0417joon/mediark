"""admin 전용 API — 초대 코드, moderator 권한 관리 (Sprint 15 범위)."""

from __future__ import annotations

import logging
import math

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from server.auth.deps import require_role
from server.auth.registration_settings import (
    get_registration_settings_for_admin,
    save_registration_settings,
)
from server.auth.roles import ALL_MODERATOR_PERMISSIONS, ROLE_LEVEL
from server.auth.users import (
    apply_user_role_change,
    create_invite_code,
    get_moderator_permissions,
    get_user_by_id,
    list_invite_codes,
    list_users_page,
    revoke_invite_code,
    set_moderator_permissions,
    set_user_is_active,
)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


# ── 공개 가입 정책 (app_settings, 미설정 시 env 폴백) ─────────────────────────

class RegistrationSettingsPayload(BaseModel):
    open_registration:      bool
    open_registration_role: str = Field(default="viewer")


@router.get("/registration-settings")
async def admin_get_registration_settings(
    user: dict = Depends(require_role("admin")),
) -> dict:
    return await run_in_threadpool(get_registration_settings_for_admin)


async def _save_registration_settings(payload: RegistrationSettingsPayload) -> dict:
    def _apply() -> dict:
        try:
            save_registration_settings(
                open_registration=payload.open_registration,
                open_registration_role=payload.open_registration_role,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        return get_registration_settings_for_admin()

    return await run_in_threadpool(_apply)


@router.put("/registration-settings")
async def admin_put_registration_settings(
    payload: RegistrationSettingsPayload,
    user: dict = Depends(require_role("admin")),
) -> dict:
    return await _save_registration_settings(payload)


@router.post("/registration-settings")
async def admin_post_registration_settings(
    payload: RegistrationSettingsPayload,
    user: dict = Depends(require_role("admin")),
) -> dict:
    """PUT 과 동일 — 일부 프록시·구 CORS 환경에서 POST 만 허용될 때 대비."""
    return await _save_registration_settings(payload)


# ── 사용자 목록 (검색·페이지) ───────────────────────────────────────────────

@router.get("/users")
async def admin_list_users(
    q: str | None = Query(None, description="이메일 부분 검색"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: dict = Depends(require_role("admin")),
) -> dict:
    rows, total, active_admins = await run_in_threadpool(
        lambda: list_users_page(search=q, page=page, per_page=per_page)
    )
    total_pages = 0 if total == 0 else max(1, math.ceil(total / per_page))
    return {
        "results": [
            {
                "id":         int(r["id"]),
                "email":      str(r["email"]),
                "role":       str(r["role"]),
                "is_active":  bool(r["is_active"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "total":              total,
        "page":               page,
        "per_page":           per_page,
        "total_pages":        total_pages,
        "active_admin_count": active_admins,
    }


# ── 초대 코드 ──────────────────────────────────────────────────────────────

class CreateInviteRequest(BaseModel):
    role:     str = Field(default="viewer")
    max_uses: int | None = Field(
        default=1,
        description="1=단일, N>1=N회, null=무제한(회수 전까지)",
    )

    @field_validator("max_uses")
    @classmethod
    def _v_max_uses(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("max_uses 는 1 이상이거나 무제한(null)이어야 합니다")
        return v


@router.post("/invite-codes", status_code=201)
async def admin_create_invite(payload: CreateInviteRequest, user: dict = Depends(require_role("admin"))) -> dict:
    if payload.role not in ROLE_LEVEL:
        raise HTTPException(status_code=400, detail="허용되지 않은 역할")
    if payload.role in ("admin", "moderator"):
        raise HTTPException(
            status_code=400,
            detail="admin/moderator 는 초대 코드로 부여할 수 없습니다",
        )
    code = await run_in_threadpool(
        lambda: create_invite_code(
            role=payload.role, created_by=user["id"], max_uses=payload.max_uses
        )
    )
    return {"code": code, "role": payload.role, "max_uses": payload.max_uses}


@router.get("/invite-codes")
async def admin_list_invites(user: dict = Depends(require_role("admin"))) -> dict:
    rows = await run_in_threadpool(list_invite_codes)
    def _status_row(r: object) -> str:
        if r["revoked_at"] is not None:
            return "revoked"
        mu = r["max_uses"]
        uc = int(r["use_count"] or 0)
        if mu is not None and uc >= int(mu):
            return "exhausted"
        return "active"

    return {
        "results": [
            {
                "code":       r["code"],
                "role":       r["role"],
                "created_by": r["created_by"],
                "created_at": r["created_at"],
                "used_by":    r["used_by"],
                "used_at":    r["used_at"],
                "revoked_at": r["revoked_at"],
                "max_uses":   r["max_uses"],
                "use_count":  int(r["use_count"] or 0),
                "status":     _status_row(r),
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.delete("/invite-codes/{code}")
async def admin_revoke_invite(code: str, user: dict = Depends(require_role("admin"))) -> dict:
    ok = await run_in_threadpool(lambda: revoke_invite_code(code))
    if not ok:
        raise HTTPException(status_code=404, detail="활성 상태의 초대 코드를 찾을 수 없습니다")
    return {"status": "revoked", "code": code}


# ── moderator 권한 ────────────────────────────────────────────────────────

class SetPermissionsRequest(BaseModel):
    permissions: list[str] = Field(default_factory=list)


@router.get("/permissions")
async def admin_list_permission_keys(user: dict = Depends(require_role("admin"))) -> dict:
    """사용 가능한 moderator 권한 키 목록."""
    return {"permissions": list(ALL_MODERATOR_PERMISSIONS)}


@router.get("/users/{user_id}/permissions")
async def admin_get_user_permissions(user_id: int, user: dict = Depends(require_role("admin"))) -> dict:
    row = await run_in_threadpool(lambda: get_user_by_id(user_id))
    if row is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    perms = await run_in_threadpool(lambda: get_moderator_permissions(user_id))
    return {
        "user_id":     user_id,
        "email":       row["email"],
        "role":        row["role"],
        "is_active":   bool(row["is_active"]),
        "permissions": perms,
    }


@router.put("/users/{user_id}/permissions")
async def admin_set_user_permissions(
    user_id: int,
    payload: SetPermissionsRequest,
    user: dict = Depends(require_role("admin")),
) -> dict:
    target = await run_in_threadpool(lambda: get_user_by_id(user_id))
    if target is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    if str(target["role"]) != "moderator":
        raise HTTPException(
            status_code=400,
            detail="moderator 역할의 사용자에 대해서만 권한을 설정할 수 있습니다",
        )
    # 알 수 없는 권한 키는 거부 (조용한 drop 대신 명시적 에러)
    unknown = [p for p in payload.permissions if p not in ALL_MODERATOR_PERMISSIONS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"알 수 없는 권한 키: {unknown}",
        )
    saved = await run_in_threadpool(
        lambda: set_moderator_permissions(
            user_id=user_id, permissions=payload.permissions, granted_by=user["id"]
        )
    )
    return {"user_id": user_id, "permissions": saved}


# ── 사용자 역할 변경 (Sprint 15 최소 범위: moderator 지정/해제) ──────────

class SetRoleRequest(BaseModel):
    role: str


@router.put("/users/{user_id}/role")
async def admin_set_user_role(
    user_id: int,
    payload: SetRoleRequest,
    user: dict = Depends(require_role("admin")),
) -> dict:
    if payload.role not in ROLE_LEVEL:
        raise HTTPException(status_code=400, detail="허용되지 않은 역할")

    def _apply() -> None:
        try:
            apply_user_role_change(user_id=user_id, new_role=payload.role)
        except LookupError:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다") from None
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    await run_in_threadpool(_apply)
    return {"user_id": user_id, "role": payload.role}


# ── 계정 활성/비활성 (탈퇴 처리) ────────────────────────────────────────────

class SetUserActiveRequest(BaseModel):
    active: bool


@router.api_route(
    "/users/{user_id}/active",
    methods=["PUT", "POST"],
    name="admin_set_user_active",
)
async def admin_set_user_active(
    user_id: int,
    payload: SetUserActiveRequest,
    user: dict = Depends(require_role("admin")),
) -> dict:
    """PUT 과 동일 — 일부 프록시·환경에서 POST 만 허용될 때 대비."""
    def _apply() -> None:
        try:
            set_user_is_active(
                user_id=user_id,
                is_active=payload.active,
                actor_user_id=user["id"],
            )
        except LookupError:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다") from None
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    await run_in_threadpool(_apply)
    row = await run_in_threadpool(lambda: get_user_by_id(user_id))
    if row is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    return {"user_id": user_id, "is_active": bool(row["is_active"])}


# ── 익명 접근 정책 (Sprint 15B) ───────────────────────────────────────────────

class AnonAccessPayload(BaseModel):
    default_anon_role: str = Field(default="none", description="none | viewer | uploader")

    @field_validator("default_anon_role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in ("none", "viewer", "uploader"):
            raise ValueError("default_anon_role 은 none / viewer / uploader 중 하나여야 합니다")
        return v


@router.get("/anon-access")
async def admin_get_anon_access(
    user: dict = Depends(require_role("admin")),
) -> dict:
    from server.auth.anon_access import get_anon_access_settings_for_admin
    return await run_in_threadpool(get_anon_access_settings_for_admin)


@router.put("/anon-access")
async def admin_put_anon_access(
    payload: AnonAccessPayload,
    user: dict = Depends(require_role("admin")),
) -> dict:
    from server.auth.anon_access import get_anon_access_settings_for_admin, set_anon_role

    def _apply() -> dict:
        set_anon_role(payload.default_anon_role)
        return get_anon_access_settings_for_admin()

    return await run_in_threadpool(_apply)


# ── 서버 프로필 (Sprint 15C) ──────────────────────────────────────────────────

class ServerProfilePayload(BaseModel):
    name:        str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=300)
    icon_url:    str = Field(default="", max_length=500)


@router.get("/profile")
async def admin_get_profile(
    user: dict = Depends(require_role("admin")),
) -> dict:
    from server.auth.server_profile import get_server_profile
    return await run_in_threadpool(get_server_profile)


@router.put("/profile")
async def admin_put_profile(
    payload: ServerProfilePayload,
    user: dict = Depends(require_role("admin")),
) -> dict:
    from server.auth.server_profile import get_server_profile, save_server_profile

    def _apply() -> dict:
        save_server_profile(
            name=payload.name,
            description=payload.description,
            icon_url=payload.icon_url,
        )
        return get_server_profile()

    return await run_in_threadpool(_apply)
