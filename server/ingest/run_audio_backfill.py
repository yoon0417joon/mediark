"""기존 인덱싱된 영상에 대해 STT(오디오 인식) 백필을 수행한다.

대상: media_type='video' + thumb_path 있음 + audio_text IS NULL

처리 후 indexed_at을 초기화하여 임베딩 파이프라인이 audio_text를 포함한 새 벡터로
재임베딩하도록 한다.

실행:
    python -m server.ingest.run_audio_backfill
"""

from __future__ import annotations

import logging

from server.db.sqlite import (
    init_db,
    get_audio_unprocessed_videos,
    update_audio_text,
    reset_indexed_at,
)
from server.ingest.audio import transcribe_video
from server.ingest.pipeline import run_embed_pipeline

logger = logging.getLogger(__name__)


def run_audio_backfill() -> None:
    """audio_text가 NULL인 영상에 STT를 적용하고 임베딩을 재생성한다."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()

    videos = get_audio_unprocessed_videos()
    total = len(videos)
    logger.info("[backfill] STT 백필 대상: %d개", total)

    if total == 0:
        logger.info("[backfill] 백필 대상 없음")
        return

    processed = 0
    for i, item in enumerate(videos, 1):
        media_id: int = item["id"]
        filepath: str = item["filepath"]
        logger.info("[backfill] (%d/%d) %s", i, total, filepath)

        text = transcribe_video(filepath)
        # 빈 문자열도 NULL 대신 ""로 저장 — "처리 완료" 표시로 재처리 방지
        update_audio_text(media_id, text if text else "")
        reset_indexed_at(media_id)
        processed += 1

    logger.info("[backfill] STT 처리 완료: %d/%d개", processed, total)

    # 재임베딩 — audio_text가 포함된 새 벡터 생성
    logger.info("[backfill] 임베딩 재생성 시작")
    run_embed_pipeline()
    logger.info("[backfill] 완료")


if __name__ == "__main__":
    run_audio_backfill()
