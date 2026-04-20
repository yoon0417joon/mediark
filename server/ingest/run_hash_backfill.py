"""기존 파일 SHA-256 해시 일괄 계산·저장 — Sprint 14A.

실행: python -m server.ingest.run_hash_backfill
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_hash_backfill() -> int:
    """file_hash가 없는 media 레코드 전체에 SHA-256 해시를 계산해 저장한다.

    Returns: 성공적으로 처리한 레코드 수
    """
    from server.db.sqlite import get_connection, update_file_hash
    from server.ingest.hashing import compute_sha256

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, filepath FROM media WHERE file_hash IS NULL"
        ).fetchall()
    finally:
        conn.close()

    total = len(rows)
    logger.info("[backfill] 해시 백필 대상: %d개", total)

    ok = 0
    for i, row in enumerate(rows, 1):
        media_id = row["id"]
        filepath = row["filepath"]
        try:
            h = compute_sha256(filepath)
            update_file_hash(media_id, h)
            logger.info("[backfill] (%d/%d) id=%s hash=%s…", i, total, media_id, h[:8])
            ok += 1
        except FileNotFoundError:
            logger.warning(
                "[backfill] (%d/%d) id=%s 파일 없음 (스킵): %s", i, total, media_id, filepath
            )
        except Exception as e:
            logger.warning(
                "[backfill] (%d/%d) id=%s 실패: %s", i, total, media_id, e
            )

    logger.info("[backfill] 완료: 성공 %d / 전체 %d", ok, total)
    return ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from server.db.sqlite import init_db
    init_db()
    run_hash_backfill()
