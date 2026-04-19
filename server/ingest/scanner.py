"""갤러리 디렉토리 스캔 및 신규 파일 감지."""

import logging
import os
from pathlib import Path
from typing import Iterator

from server.config import (
    GALLERY_ROOT,
    IMAGE_EXTENSIONS,
    GIF_EXTENSIONS,
    VIDEO_EXTENSIONS,
    MAX_MEDIA_DURATION,
)
from server.db.sqlite import get_all_filepaths, insert_media
from server.ingest.video import get_video_duration

logger = logging.getLogger(__name__)


def classify_media_type(path: Path) -> str | None:
    """파일 확장자로 media_type을 반환. 미지원 확장자면 None."""
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in GIF_EXTENSIONS:
        return "gif"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def scan_directory(directory: str) -> Iterator[tuple[str, str]]:
    """
    directory 안의 모든 미디어 파일을 재귀 탐색한다.
    yields: (filepath, media_type)
    """
    for root, _, files in os.walk(directory):
        for filename in files:
            full_path = os.path.join(root, filename)
            normalized = full_path.replace("\\", "/")
            media_type = classify_media_type(Path(filename))
            if media_type is None:
                continue
            yield normalized, media_type


def scan_new_media() -> list[dict]:
    """
    GALLERY_ROOT 하위 전체를 스캔하여 DB에 없는 신규 파일만 삽입한다.
    삽입된 레코드 정보 목록을 반환한다.
    """
    if not os.path.isdir(GALLERY_ROOT):
        logger.warning("[scanner] 디렉토리 없음, 스킵: %s", GALLERY_ROOT)
        return []

    known = get_all_filepaths()
    inserted = []

    for filepath, media_type in scan_directory(GALLERY_ROOT):
        if filepath in known:
            continue
        if os.path.getsize(filepath) == 0:
            logger.info("[scanner] 빈 파일 스킵 (0 bytes): %s", filepath)
            continue
        if media_type in ("gif", "video"):
            dur = get_video_duration(filepath)
            if dur > MAX_MEDIA_DURATION:
                logger.info(
                    "[scanner] 길이 초과 스킵 (%.0f초 > %d초): %s",
                    dur, MAX_MEDIA_DURATION, filepath,
                )
                continue
        media_id = insert_media(filepath, media_type)
        if media_id is not None:
            inserted.append(
                {"id": media_id, "filepath": filepath, "media_type": media_type}
            )
            known.add(filepath)

    logger.info("[scanner] 신규 미디어 %d개 등록", len(inserted))
    return inserted


if __name__ == "__main__":
    from server.db.sqlite import init_db

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_db()
    results = scan_new_media()
    for r in results[:10]:
        logger.info("%s", r)
