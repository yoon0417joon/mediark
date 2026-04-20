"""공개 가입 정책 — app_settings 에 저장, 미저장 항목은 환경변수 기본값."""

from __future__ import annotations

from server.auth.roles import ROLE_LEVEL
from server.config import OPEN_REGISTRATION as ENV_OPEN
from server.config import OPEN_REGISTRATION_ROLE as ENV_ROLE
from server.db.sqlite import connect, get_connection

KEY_OPEN = "open_registration"
KEY_ROLE = "open_registration_role"


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _get_raw(key: str) -> str | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row else None
    finally:
        conn.close()


def get_effective_registration_policy() -> tuple[bool, str]:
    """(공개 가입 허용, 부여 역할). 키별로 DB 없으면 해당 항목만 env."""
    o_raw = _get_raw(KEY_OPEN)
    r_raw = _get_raw(KEY_ROLE)
    open_reg = ENV_OPEN if o_raw is None else _parse_bool(o_raw)
    role = ENV_ROLE if r_raw is None else (r_raw.strip() or ENV_ROLE)
    if role not in ROLE_LEVEL or role in ("admin", "moderator"):
        role = "viewer"
    return open_reg, role


def get_registration_settings_for_admin() -> dict:
    o_raw = _get_raw(KEY_OPEN)
    r_raw = _get_raw(KEY_ROLE)
    open_eff, role_eff = get_effective_registration_policy()
    return {
        "open_registration":           open_eff,
        "open_registration_role":      role_eff,
        "env_open_registration":       ENV_OPEN,
        "env_open_registration_role":  ENV_ROLE,
        "database_overrides":          o_raw is not None or r_raw is not None,
    }


def save_registration_settings(*, open_registration: bool, open_registration_role: str) -> None:
    if open_registration_role not in ("viewer", "uploader"):
        raise ValueError("open_registration_role 은 viewer 또는 uploader 만 허용됩니다")
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (KEY_OPEN, "1" if open_registration else "0"),
        )
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (KEY_ROLE, open_registration_role),
        )
