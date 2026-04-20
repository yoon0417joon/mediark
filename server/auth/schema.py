"""Sprint 15 — 인증 관련 테이블 DDL 및 마이그레이션."""

from __future__ import annotations

import logging

from server.db.sqlite import connect

logger = logging.getLogger(__name__)

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'viewer',
    is_active      INTEGER NOT NULL DEFAULT 1,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login_at  DATETIME
);
"""

CREATE_INVITE_CODES_TABLE = """
CREATE TABLE IF NOT EXISTS invite_codes (
    code        TEXT PRIMARY KEY,
    role        TEXT NOT NULL DEFAULT 'viewer',
    created_by  INTEGER REFERENCES users(id),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    used_by     INTEGER REFERENCES users(id),
    used_at     DATETIME,
    revoked_at  DATETIME
);
"""

CREATE_MOD_PERM_TABLE = """
CREATE TABLE IF NOT EXISTS moderator_permissions (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission  TEXT NOT NULL,
    granted_by  INTEGER NOT NULL REFERENCES users(id),
    granted_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, permission)
);
"""

# JWT 무효화용 denylist. 로그아웃·계정 비활성화·토큰 강제 회수 시 jti 를 기록한다.
CREATE_TOKEN_DENYLIST_TABLE = """
CREATE TABLE IF NOT EXISTS token_denylist (
    jti         TEXT PRIMARY KEY,
    user_id     INTEGER,
    expires_at  INTEGER NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_APP_SETTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_INDEX_STMTS = (
    "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
    "CREATE INDEX IF NOT EXISTS idx_invite_codes_used_by ON invite_codes(used_by)",
    "CREATE INDEX IF NOT EXISTS idx_mod_perm_user ON moderator_permissions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_token_denylist_expires ON token_denylist(expires_at)",
)


def _migrate_invite_codes_columns(conn) -> None:
    """max_uses(NULL=무제한)·use_count — 기존 단일 사용 행과 호환."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(invite_codes)").fetchall()}
    if "max_uses" not in cols:
        conn.execute("ALTER TABLE invite_codes ADD COLUMN max_uses INTEGER")
    if "use_count" not in cols:
        conn.execute("ALTER TABLE invite_codes ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0")

    # 이미 사용된 코드: 소비 1회로 간주
    conn.execute(
        """
        UPDATE invite_codes
        SET use_count = 1,
            max_uses = COALESCE(max_uses, 1)
        WHERE used_at IS NOT NULL AND use_count = 0
        """
    )
    # 미사용·미회수: 기본 단일 사용 (max_uses=1), 무제한은 생성 시에만 NULL
    conn.execute(
        """
        UPDATE invite_codes
        SET max_uses = 1
        WHERE max_uses IS NULL AND used_at IS NULL AND revoked_at IS NULL
        """
    )


def init_auth_schema() -> None:
    """멱등하게 인증 관련 테이블·인덱스를 생성한다."""
    with connect() as conn:
        conn.execute(CREATE_USERS_TABLE)
        conn.execute(CREATE_INVITE_CODES_TABLE)
        conn.execute(CREATE_MOD_PERM_TABLE)
        conn.execute(CREATE_TOKEN_DENYLIST_TABLE)
        conn.execute(CREATE_APP_SETTINGS_TABLE)
        for stmt in _INDEX_STMTS:
            conn.execute(stmt)
        _migrate_invite_codes_columns(conn)
    logger.info("[auth] schema 초기화 완료")
