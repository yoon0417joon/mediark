"""비밀번호 해시/검증 — bcrypt 래퍼."""

from __future__ import annotations

import bcrypt

_ROUNDS = 12
_MAX_LEN = 72  # bcrypt 입력 상한


def _prepare(password: str) -> bytes:
    # bcrypt 는 72 바이트 이상을 잘라낸다. 일관성을 위해 명시적으로 자름.
    return password.encode("utf-8")[:_MAX_LEN]


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("비밀번호는 최소 8자 이상이어야 합니다")
    salt = bcrypt.gensalt(rounds=_ROUNDS)
    return bcrypt.hashpw(_prepare(password), salt).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    if not password or not hashed:
        return False
    try:
        return bcrypt.checkpw(_prepare(password), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
