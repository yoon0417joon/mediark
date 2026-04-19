"""POST /upload, GET /upload/status — main.py 에서 분리."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from starlette.concurrency import run_in_threadpool

from server.config import (
    GIF_EXTENSIONS,
    GALLERY_ROOT,
    IMAGE_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    UPLOAD_RATE_LIMIT,
    VIDEO_EXTENSIONS,
)
from server.db.sqlite import get_media_by_id, insert_media
from server.http_utils import client_ip
from server.ingest.scanner import classify_media_type
from server.rate_limit import rate_limit_bucket
from server.upload_tracking import mark_upload_done, mark_upload_start

logger = logging.getLogger(__name__)

router = APIRouter()

_UPLOAD_ALLOWED = IMAGE_EXTENSIONS | GIF_EXTENSIONS | VIDEO_EXTENSIONS


def _sanitize_upload_filename(raw: str | None) -> str:
    name = (raw or "").replace("\x00", "").strip()
    name = name.replace("\\", "/").split("/")[-1]
    name = name.strip()
    if not name or name in (".", ".."):
        name = "upload"
    return name


def _open_atomic_unique(gallery_root: Path, filename: str) -> tuple[int, Path]:
    stem = Path(filename).stem
    suffix = Path(filename).suffix.lower()
    last_err: OSError | None = None
    for v in range(0, 1000):
        candidate = gallery_root / (filename if v == 0 else f"{stem}_v{v + 1}{suffix}")
        try:
            fd = os.open(
                str(candidate),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o644,
            )
            return fd, candidate
        except FileExistsError:
            continue
        except OSError as e:
            last_err = e
            break
    raise RuntimeError(f"업로드 대상 경로 생성 실패: {filename} ({last_err})")


async def _stream_upload_to_disk(
    upload: UploadFile,
    gallery_root: Path,
    filename: str,
    max_bytes: int,
) -> Path:
    fd, dest = _open_atomic_unique(gallery_root, filename)
    written = 0
    chunk_size = 1024 * 1024
    try:
        with os.fdopen(fd, "wb") as f:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"업로드 크기 상한 초과 ({max_bytes} bytes)",
                    )
                f.write(chunk)
        if written == 0:
            raise HTTPException(status_code=400, detail="빈 파일은 업로드할 수 없습니다")
        return dest
    except Exception:
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _ingest_uploaded_file(media_id: int, filepath: str, media_type: str) -> None:
    from server.db.sqlite import update_index_error

    try:
        from server.ingest.pipeline import _process_media, run_embed_pipeline
        from server.db.sqlite import get_media_by_id as _get_row

        result = _process_media(media_id, filepath, media_type)
        if result.get("tmp_dir"):
            import shutil
            shutil.rmtree(result["tmp_dir"], ignore_errors=True)
        if result["error"]:
            logger.warning("[upload] 처리 실패 (id=%s): %s", media_id, result["error"])
            update_index_error(media_id, f"process_failed: {result['error']}")
            return

        row = _get_row(media_id)
        if row:
            run_embed_pipeline([dict(row)])

        logger.info("[upload] 인덱싱 완료 (id=%s, %s)", media_id, filepath)
    except Exception as e:
        logger.exception("[upload] 인덱싱 예외 (id=%s): %s", media_id, e)
        try:
            update_index_error(media_id, f"exception: {e}")
        except Exception:
            pass
    finally:
        mark_upload_done(filepath)


@router.post("/upload", status_code=202)
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> dict:
    rate_limit_bucket(client_ip(request), "upload", UPLOAD_RATE_LIMIT)

    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_UPLOAD_BYTES + (1024 * 1024):
        raise HTTPException(
            status_code=413,
            detail=f"업로드 크기 상한 초과 ({MAX_UPLOAD_BYTES} bytes)",
        )

    safe_name = _sanitize_upload_filename(file.filename)
    suffix = Path(safe_name).suffix.lower()

    if suffix not in _UPLOAD_ALLOWED:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 파일 형식: '{suffix}'. "
            f"허용: {', '.join(sorted(_UPLOAD_ALLOWED))}",
        )

    media_type = classify_media_type(Path(safe_name))
    if media_type is None:
        raise HTTPException(status_code=400, detail="파일 형식 판별 실패")

    gallery_root = Path(GALLERY_ROOT).resolve()
    gallery_root.mkdir(parents=True, exist_ok=True)

    try:
        dest = await _stream_upload_to_disk(
            file, gallery_root, safe_name, MAX_UPLOAD_BYTES
        )
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        dest_resolved = dest.resolve()
        dest_resolved.relative_to(gallery_root)
    except ValueError:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="허용되지 않은 저장 경로")

    filepath_normalized = str(dest_resolved).replace("\\", "/")

    mark_upload_start(filepath_normalized)

    media_id = await run_in_threadpool(
        lambda: insert_media(filepath_normalized, media_type)
    )
    if media_id is None:
        mark_upload_done(filepath_normalized)
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="이미 존재하는 파일입니다")

    try:
        background_tasks.add_task(
            _ingest_uploaded_file, media_id, filepath_normalized, media_type
        )
    except Exception:
        mark_upload_done(filepath_normalized)
        raise

    return {
        "status": "accepted",
        "media_id": media_id,
        "filename": dest.name,
        "media_type": media_type,
    }


@router.get("/upload/status/{media_id}")
async def upload_status(media_id: int):
    row = await run_in_threadpool(lambda: get_media_by_id(media_id))
    if row is None:
        raise HTTPException(status_code=404, detail="미디어를 찾을 수 없습니다")

    raw_err = row["index_error"]
    indexed_at = row["indexed_at"]

    if raw_err:
        state = "error"
    elif indexed_at:
        state = "indexed"
    else:
        state = "pending"

    public_err: str | None
    if raw_err:
        if raw_err == "empty_text":
            public_err = "empty_text"
        elif raw_err.startswith("process_failed"):
            public_err = "processing_failed"
        else:
            public_err = "indexing_failed"
    else:
        public_err = None

    return {
        "media_id": media_id,
        "state": state,
        "error": public_err,
        "indexed_at": indexed_at,
        "thumb_ready": bool(row["thumb_path"]),
    }
