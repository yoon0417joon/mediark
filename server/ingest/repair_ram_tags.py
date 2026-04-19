"""Sprint 8B 수동 재처리 스크립트.

ram_tags IS NULL + thumb_path IS NOT NULL 항목만 골라
RAM++ 태깅 → update_ram_tags → reset_indexed_at → 임베딩 재실행.

사용법:
    python -m server.ingest.repair_ram_tags
"""

from __future__ import annotations

import logging
import shutil
import tempfile

from server.db.sqlite import (
    get_missing_ram_tags_media,
    update_ram_tags,
    reset_indexed_at,
    init_db,
)
from server.ingest.ram import tag_image as ram_tag_image, tag_frames as ram_tag_frames
from server.ingest.video import extract_keyframes

logger = logging.getLogger(__name__)


def _retag_item(item: dict) -> bool:
    """단일 항목에 RAM++ 재태깅을 수행한다. 성공 시 True 반환."""
    media_id = item["id"]
    filepath = item["filepath"]
    media_type = item["media_type"]

    try:
        if media_type == "image":
            ram_tags = ram_tag_image(filepath)
        elif media_type in ("gif", "video"):
            tmp_dir = tempfile.mkdtemp()
            try:
                frames = extract_keyframes(filepath, tmp_dir)
                if not frames:
                    raise RuntimeError("키프레임 추출 실패")
                ram_tags = ram_tag_frames(frames)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            logger.warning("[repair] 알 수 없는 media_type=%s (id=%s)", media_type, media_id)
            return False

        update_ram_tags(media_id, ram_tags)
        reset_indexed_at(media_id)
        logger.info("[repair] OK id=%s (%s) tags=%s...", media_id, media_type, ram_tags[:60])
        return True

    except Exception as e:
        logger.exception("[repair] FAIL id=%s (%s): %s", media_id, filepath, e)
        return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()

    targets = get_missing_ram_tags_media()
    total = len(targets)
    logger.info("[repair] ram_tags 누락 항목: %d개", total)

    if total == 0:
        logger.info("[repair] 재처리 대상 없음 — 종료")
        return

    ok = sum(_retag_item(item) for item in targets)
    logger.info("[repair] 재태깅 완료: 성공 %d/%d", ok, total)

    if ok == 0:
        logger.info("[repair] 성공 항목 없음 — 임베딩 재실행 건너뜀")
        return

    logger.info("[repair] 임베딩 재실행...")
    from server.ingest.pipeline import run_embed_pipeline
    run_embed_pipeline()
    logger.info("[repair] 완료")


if __name__ == "__main__":
    main()
