"""BOOTSTRAP_ADMIN_EMAIL/PASSWORD 가 설정돼 있으면 첫 실행 시 admin 계정 자동 생성.

활성 admin 이 0명이면(예: 마지막 admin 을 실수로 강등) 부트스트랩 이메일 계정을 admin 으로 복구한다.
복구 시에는 비밀번호가 없어도 된다(이메일로 계정만 특정).

주의: 다른 계정이 이미 admin 이면(active admin ≥ 1) 자동 복구하지 않는다.
"""

from __future__ import annotations

import logging

from server.auth.users import (
    count_active_admins,
    create_user,
    get_user_by_email,
    list_active_admin_emails,
    recover_bootstrap_account_to_admin,
)
from server.config import BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD

logger = logging.getLogger(__name__)


def ensure_bootstrap_admin() -> None:
    n_admins = count_active_admins()
    emails = list_active_admin_emails()
    if emails:
        logger.info("[auth] 활성 admin %s명: %s", n_admins, ", ".join(emails))
    else:
        logger.info("[auth] 활성 admin 계정 없음")

    email = BOOTSTRAP_ADMIN_EMAIL
    password = BOOTSTRAP_ADMIN_PASSWORD

    # ── A) 활성 admin 0명: 부트스트랩 이메일 계정만 admin 으로 승격 (비밀번호 불필요)
    if n_admins == 0 and email:
        row = get_user_by_email(email)
        if row is not None:
            if str(row["role"]) != "admin":
                recover_bootstrap_account_to_admin(user_id=int(row["id"]))
                logger.info(
                    "[auth] 활성 admin 이 없어 부트스트랩 이메일 계정을 admin 으로 복구했습니다 "
                    "(id=%s, email=%s)",
                    row["id"],
                    email,
                )
            return
        # 해당 이메일 사용자 없음 → 아래에서 신규 생성 시도

    # ── B) 신규 admin 계정 생성 (이메일 + 비밀번호 필수)
    if not email or not password:
        if n_admins == 0:
            logger.warning(
                "[auth] 활성 admin 이 없습니다. "
                "(1) DB 에 BOOTSTRAP_ADMIN_EMAIL 과 동일한 이메일 사용자가 있으면 비밀번호 없이도 "
                "기동 시 admin 으로 승격됩니다. "
                "(2) 없으면 BOOTSTRAP_ADMIN_EMAIL 과 BOOTSTRAP_ADMIN_PASSWORD 를 모두 설정하세요."
            )
        return

    existing = get_user_by_email(email)
    if existing is not None:
        if str(existing["role"]) != "admin":
            logger.warning(
                "[auth] BOOTSTRAP_ADMIN_EMAIL(%s) 계정은 admin 이 아닙니다 (현재 역할=%s). "
                "활성 admin 이 %s명 있어 자동 승격하지 않습니다: %s",
                email,
                existing["role"],
                n_admins,
                ", ".join(emails) if emails else "(없음)",
            )
        return

    try:
        user_id = create_user(
            email=email,
            password=password,
            role="admin",
            is_active=True,
        )
        logger.info("[auth] 부트스트랩 admin 계정 생성 (id=%s, %s)", user_id, email)
    except ValueError as e:
        logger.error("[auth] 부트스트랩 admin 생성 실패: %s", e)
    except Exception as e:
        logger.exception("[auth] 부트스트랩 admin 생성 예외: %s", e)
