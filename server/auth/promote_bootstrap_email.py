"""부트스트랩 이메일 계정을 admin 으로 승격 (원샷 유지보수).

다른 admin 이 남아 있어도 실행 가능. 중복 admin 이 되면 관리 화면에서 정리하면 된다.

  python -m server.auth.promote_bootstrap_email

환경: BOOTSTRAP_ADMIN_EMAIL (.env)
"""

from __future__ import annotations

import sys

# config 가 .env 로드
from server.config import BOOTSTRAP_ADMIN_EMAIL
from server.auth.users import get_user_by_email, recover_bootstrap_account_to_admin


def main() -> int:
    if not BOOTSTRAP_ADMIN_EMAIL:
        print("BOOTSTRAP_ADMIN_EMAIL 이 비어 있습니다. .env 를 확인하세요.", file=sys.stderr)
        return 1
    row = get_user_by_email(BOOTSTRAP_ADMIN_EMAIL)
    if row is None:
        print(f"사용자를 찾을 수 없습니다: {BOOTSTRAP_ADMIN_EMAIL}", file=sys.stderr)
        return 1
    uid = int(row["id"])
    recover_bootstrap_account_to_admin(user_id=uid)
    print(f"OK: id={uid} ({BOOTSTRAP_ADMIN_EMAIL}) → admin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
