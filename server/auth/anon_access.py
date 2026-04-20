"""익명 접근 정책 — app_settings 저장, 미저장 시 환경변수 기본값."""

from __future__ import annotations

from server.config import DEFAULT_ANON_ROLE as ENV_ANON_ROLE
from server.db.sqlite import connect, get_connection

KEY = "default_anon_role"
_VALID = {"none", "viewer", "uploader"}


def _get_raw() -> str | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (KEY,)
        ).fetchone()
        return str(row["value"]) if row else None
    finally:
        conn.close()


def get_effective_anon_role() -> str:
    """현재 유효한 익명 역할 반환. DB 미설정 시 환경변수 기본값."""
    raw = _get_raw()
    role = ENV_ANON_ROLE if raw is None else raw.strip().lower()
    return role if role in _VALID else "none"


def set_anon_role(role: str) -> None:
    if role not in _VALID:
        raise ValueError(f"default_anon_role 은 {_VALID} 중 하나여야 합니다")
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (KEY, role),
        )


def get_anon_access_settings_for_admin() -> dict:
    raw = _get_raw()
    effective = get_effective_anon_role()
    return {
        "default_anon_role":     effective,
        "env_default_anon_role": ENV_ANON_ROLE,
        "database_override":     raw is not None,
    }
