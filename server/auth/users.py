"""유저/초대 코드/moderator 권한 DB 헬퍼."""

from __future__ import annotations

import logging
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from server.auth.passwords import hash_password
from server.auth.roles import ALL_MODERATOR_PERMISSIONS, ROLE_LEVEL
from server.db.sqlite import connect, get_connection

logger = logging.getLogger(__name__)

_VALID_ROLES = set(ROLE_LEVEL.keys())


@dataclass(frozen=True)
class UserRow:
    id: int
    email: str
    role: str
    is_active: bool


def _row_to_user(row: sqlite3.Row | None) -> UserRow | None:
    if row is None:
        return None
    return UserRow(
        id=int(row["id"]),
        email=str(row["email"]),
        role=str(row["role"]),
        is_active=bool(row["is_active"]),
    )


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# ── 사용자 CRUD ────────────────────────────────────────────────────────────

def create_user(
    *,
    email: str,
    password: str,
    role: str,
    is_active: bool = True,
) -> int:
    if role not in _VALID_ROLES:
        raise ValueError(f"허용되지 않은 역할: {role}")
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        raise ValueError("유효하지 않은 이메일")

    password_hash = hash_password(password)
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, role, is_active) "
            "VALUES (?, ?, ?, ?)",
            (normalized, password_hash, role, 1 if is_active else 0),
        )
        if cur.lastrowid is None:
            raise RuntimeError("사용자 생성 실패")
        return int(cur.lastrowid)


def get_user_by_email(email: str) -> sqlite3.Row | None:
    normalized = normalize_email(email)
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM users WHERE email = ?", (normalized,)
        ).fetchone()
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> sqlite3.Row | None:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()


def touch_last_login(user_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id,),
        )


def count_users() -> int:
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


def count_active_admins() -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
        ).fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


def list_active_admin_emails() -> list[str]:
    """활성 admin 계정 이메일 목록(진단·로그용)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT email FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id"
        ).fetchall()
        return [str(r["email"]) for r in rows]
    finally:
        conn.close()


def _search_email_like(raw: str | None) -> str | None:
    t = normalize_email(raw or "")
    if not t:
        return None
    # LIKE 와일드카드 제거 후 부분 일치
    return "%" + t.replace("%", "").replace("_", "") + "%"


def list_users_page(
    *,
    search: str | None,
    page: int,
    per_page: int,
) -> tuple[list[sqlite3.Row], int, int]:
    """(rows, total_rows, active_admin_count). email 부분 검색(대소문자 무시)."""
    page = max(1, page)
    per_page = min(100, max(1, per_page))
    offset = (page - 1) * per_page
    like = _search_email_like(search)
    conn = get_connection()
    try:
        admin_n = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
        ).fetchone()
        active_admins = int(admin_n["n"] or 0)
        if like:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE email LIKE ?",
                (like,),
            ).fetchone()
            rows = list(
                conn.execute(
                    "SELECT id, email, role, is_active, created_at FROM users "
                    "WHERE email LIKE ? ORDER BY id ASC LIMIT ? OFFSET ?",
                    (like, per_page, offset),
                )
            )
        else:
            total = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
            rows = list(
                conn.execute(
                    "SELECT id, email, role, is_active, created_at FROM users "
                    "ORDER BY id ASC LIMIT ? OFFSET ?",
                    (per_page, offset),
                )
            )
        return rows, int(total["n"] or 0), active_admins
    finally:
        conn.close()


def apply_user_role_change(*, user_id: int, new_role: str) -> None:
    """역할 변경. 마지막 admin 강등은 거부. 단일 트랜잭션."""
    if new_role not in _VALID_ROLES:
        raise ValueError("허용되지 않은 역할입니다")
    with connect() as conn:
        row = conn.execute(
            "SELECT id, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise LookupError("사용자를 찾을 수 없습니다")
        old = str(row["role"])
        if old == "admin" and new_role != "admin":
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
            ).fetchone()
            if int(n["n"] or 0) <= 1:
                raise ValueError("마지막 admin 계정의 역할은 강등할 수 없습니다")
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        if new_role != "moderator":
            conn.execute(
                "DELETE FROM moderator_permissions WHERE user_id = ?", (user_id,)
            )


def set_user_is_active(
    *,
    user_id: int,
    is_active: bool,
    actor_user_id: int,
) -> None:
    """관리자가 계정 활성/비활성 전환. 비활성 시 해당 사용자 JWT 전부 무효."""
    from server.auth.tokens import revoke_all_for_user

    if user_id == actor_user_id and not is_active:
        raise ValueError("자기 자신을 비활성화할 수 없습니다")

    with connect() as conn:
        row = conn.execute(
            "SELECT id, role, is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise LookupError("사용자를 찾을 수 없습니다")

        if not is_active:
            if str(row["role"]) == "admin":
                n = conn.execute(
                    "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
                ).fetchone()
                if int(n["n"] or 0) <= 1:
                    raise ValueError("마지막 활성 admin 계정을 비활성화할 수 없습니다")

        conn.execute(
            "UPDATE users SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, user_id),
        )

    if not is_active:
        revoke_all_for_user(user_id)


def recover_bootstrap_account_to_admin(*, user_id: int) -> None:
    """활성 admin 이 0명일 때 부트스트랩 이메일 계정을 admin 으로 복구."""
    with connect() as conn:
        conn.execute(
            "UPDATE users SET role = 'admin', is_active = 1 WHERE id = ?",
            (user_id,),
        )
        conn.execute("DELETE FROM moderator_permissions WHERE user_id = ?", (user_id,))


# ── 초대 코드 ──────────────────────────────────────────────────────────────

def _random_code() -> str:
    return secrets.token_urlsafe(18)


def create_invite_code(
    *, role: str, created_by: int, max_uses: int | None = 1
) -> str:
    """max_uses: 1=단일, N>1=N회까지, None=무제한(회수 전까지)."""
    if role not in _VALID_ROLES:
        raise ValueError(f"허용되지 않은 역할: {role}")
    if max_uses is not None and max_uses < 1:
        raise ValueError("max_uses 는 1 이상이거나 무제한(None)이어야 합니다")
    code = _random_code()
    with connect() as conn:
        conn.execute(
            "INSERT INTO invite_codes (code, role, created_by, max_uses, use_count) "
            "VALUES (?, ?, ?, ?, 0)",
            (code, role, created_by, max_uses),
        )
    return code


def revoke_invite_code(code: str) -> bool:
    """미회수 코드면 회수. 이미 소진·사용 중이어도 revoked_at 이 비어 있으면 회수 가능."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE invite_codes SET revoked_at = CURRENT_TIMESTAMP "
            "WHERE code = ? AND revoked_at IS NULL",
            (code,),
        )
        return (cur.rowcount or 0) > 0


def list_invite_codes() -> list[sqlite3.Row]:
    conn = get_connection()
    try:
        return list(
            conn.execute(
                "SELECT code, role, created_by, created_at, used_by, used_at, revoked_at, "
                "max_uses, use_count "
                "FROM invite_codes ORDER BY created_at DESC"
            )
        )
    finally:
        conn.close()


def claim_invite_code(code: str, used_by: int) -> sqlite3.Row | None:
    """초대 코드 1회 소비. 무제한이 아니면 max_uses 도달 시 실패."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM invite_codes WHERE code = ?", (code,)
        ).fetchone()
        if row is None:
            return None
        if row["revoked_at"] is not None:
            return None
        max_u = row["max_uses"]
        use_c = int(row["use_count"] or 0)
        if max_u is not None and use_c >= int(max_u):
            return None
        cur = conn.execute(
            "UPDATE invite_codes SET "
            "use_count = use_count + 1, "
            "used_by = ?, "
            "used_at = CURRENT_TIMESTAMP "
            "WHERE code = ? AND revoked_at IS NULL "
            "AND (max_uses IS NULL OR use_count < max_uses)",
            (used_by, code),
        )
        if (cur.rowcount or 0) == 0:
            return None
        return conn.execute(
            "SELECT * FROM invite_codes WHERE code = ?", (code,)
        ).fetchone()


# ── moderator 권한 ────────────────────────────────────────────────────────

def set_moderator_permissions(
    *, user_id: int, permissions: Iterable[str], granted_by: int
) -> list[str]:
    """대상 user_id 의 권한 집합을 `permissions` 로 치환. 반환값은 실제 저장된 권한 목록."""
    target = [p for p in set(permissions) if p in ALL_MODERATOR_PERMISSIONS]
    with connect() as conn:
        conn.execute("DELETE FROM moderator_permissions WHERE user_id = ?", (user_id,))
        for perm in target:
            conn.execute(
                "INSERT INTO moderator_permissions (user_id, permission, granted_by) "
                "VALUES (?, ?, ?)",
                (user_id, perm, granted_by),
            )
    return target


def get_moderator_permissions(user_id: int) -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT permission FROM moderator_permissions WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return [r["permission"] for r in rows]
    finally:
        conn.close()


def has_moderator_permission(user_id: int, permission: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM moderator_permissions "
            "WHERE user_id = ? AND permission = ? LIMIT 1",
            (user_id, permission),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
