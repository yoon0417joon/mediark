"""인제스천 시 썸네일 미리 생성."""

from __future__ import annotations
import logging
import os
import subprocess
import tempfile

from PIL import Image

from server.config import SUBPROCESS_TIMEOUT_SEC, THUMB_DIR, THUMB_MAX_SIZE
from server.ingest.video import _safe_ffmpeg_path

logger = logging.getLogger(__name__)


def _ensure_dir() -> None:
    os.makedirs(THUMB_DIR, exist_ok=True)


def thumb_path_for(media_id: int) -> str:
    """media_id에 대한 썸네일 저장 경로를 반환한다 (파일 생성 없음)."""
    return os.path.join(THUMB_DIR, f"{media_id}.jpg").replace("\\", "/")


def _save_thumbnail(img: Image.Image, out_path: str) -> None:
    img = img.convert("RGB")
    img.thumbnail(THUMB_MAX_SIZE, Image.LANCZOS)
    img.save(out_path, "JPEG", quality=85)


def generate_thumbnail(media_id: int, source_path: str, media_type: str) -> str | None:
    """
    미디어로부터 썸네일 .jpg를 생성하고 저장 경로를 반환한다.
    - image : 직접 리사이즈
    - gif   : 첫 프레임 리사이즈
    - video : 첫 키프레임(source_path)을 리사이즈

    PIL이 파일 형식을 인식하지 못하면 ffmpeg으로 첫 프레임을 추출해 재시도한다.
    실패 시 None 반환.
    """
    _ensure_dir()
    out_path = thumb_path_for(media_id)
    try:
        img = Image.open(source_path)
        if media_type == "gif":
            try:
                img.seek(0)
            except EOFError:
                pass  # 1-frame GIF: seek(0) 불필요
        _save_thumbnail(img, out_path)
        return out_path
    except Exception as pil_err:
        # PIL이 형식을 인식하지 못할 경우 ffmpeg으로 첫 프레임 추출 후 재시도
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
            os.close(fd)
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", _safe_ffmpeg_path(source_path),
                    "-frames:v", "1", "-q:v", "2",
                    _safe_ffmpeg_path(tmp_path),
                ],
                capture_output=True,
                check=True,
                timeout=SUBPROCESS_TIMEOUT_SEC,
            )
            img = Image.open(tmp_path)
            _save_thumbnail(img, out_path)
            return out_path
        except Exception as ffmpeg_err:
            logger.warning(
                "[thumbnail] 생성 실패 (id=%s, %s): PIL=%s / ffmpeg=%s",
                media_id, source_path, pil_err, ffmpeg_err,
            )
            return None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
