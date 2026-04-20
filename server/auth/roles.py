"""역할 계층 및 moderator 권한 키 상수."""

from __future__ import annotations

from typing import Literal

Role = Literal["viewer", "uploader", "moderator", "admin"]

ROLE_LEVEL: dict[str, int] = {
    "viewer":    0,
    "uploader":  1,
    "moderator": 2,
    "admin":     3,
}

# Sprint 15 스펙 표의 권한 키 — 이후 스프린트에서 실제 기능이 각각 활용한다.
ALL_MODERATOR_PERMISSIONS: tuple[str, ...] = (
    "report_review",
    "media_hide",
    "media_delete",
    "comment_delete",
    "user_list_view",
    "tag_edit",
    "ingest_trigger",
    "transfer_approve",
)


def role_at_least(role: str | None, required: str) -> bool:
    if not role:
        return False
    return ROLE_LEVEL.get(role, -1) >= ROLE_LEVEL.get(required, 99)
