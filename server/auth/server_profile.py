"""서버 프로필 — app_settings 에 name/description/icon_url 저장."""

from __future__ import annotations

from server.db.sqlite import connect, get_connection

KEY_NAME = "profile_name"
KEY_DESC = "profile_description"
KEY_ICON = "profile_icon_url"


def _get(key: str) -> str | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row else None
    finally:
        conn.close()


def get_server_profile() -> dict:
    return {
        "name":        _get(KEY_NAME) or "",
        "description": _get(KEY_DESC) or "",
        "icon_url":    _get(KEY_ICON) or "",
    }


def save_server_profile(*, name: str, description: str, icon_url: str) -> None:
    with connect() as conn:
        for key, val in ((KEY_NAME, name), (KEY_DESC, description), (KEY_ICON, icon_url)):
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, val),
            )
