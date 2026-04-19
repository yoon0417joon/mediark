"""업로드 진행 중 경로 추적 — watcher 이중 인제스트 방지 (H21)."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

UPLOAD_TTL_SECONDS = 3600.0
_upload_in_progress: dict[str, float] = {}
_upload_lock = threading.Lock()
upload_sweeper_stop = threading.Event()


def normalize_watch_path(filepath: str) -> str:
    try:
        p = str(Path(filepath).resolve()).replace("\\", "/")
    except Exception:
        p = filepath.replace("\\", "/")
    return p.lower()


def mark_upload_start(filepath: str) -> None:
    with _upload_lock:
        _upload_in_progress[normalize_watch_path(filepath)] = time.monotonic()


def mark_upload_done(filepath: str) -> None:
    with _upload_lock:
        _upload_in_progress.pop(normalize_watch_path(filepath), None)


def is_upload_in_progress(filepath: str) -> bool:
    with _upload_lock:
        return normalize_watch_path(filepath) in _upload_in_progress


def upload_sweeper_loop() -> None:
    while not upload_sweeper_stop.wait(60.0):
        cutoff = time.monotonic() - UPLOAD_TTL_SECONDS
        with _upload_lock:
            stale = [p for p, t in _upload_in_progress.items() if t < cutoff]
            for p in stale:
                _upload_in_progress.pop(p, None)
        if stale:
            logger.warning("[upload_tracking] TTL 만료 %d건 제거", len(stale))
