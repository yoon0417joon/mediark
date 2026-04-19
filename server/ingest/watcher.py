"""갤러리 폴더 watchdog 자동 감지 인제스천 — Sprint 11.

GALLERY_ROOT를 재귀 감시하며 신규 파일이 추가되면 자동으로 파이프라인을 실행한다.

설계 원칙:
- 이벤트 핸들러는 debounce 예약만 갱신 (블로킹 없음)
- 단일 debounce 스케줄러 스레드가 만료 시점에 큐로 push (H20: 파일당 Timer 금지)
- 파이프라인 실행은 단일 워커 스레드가 큐를 순차 처리
- 수동 인제스천(`/ingest`)이 실행 중이면 _stop_event.wait 로 대기 (H2)
- indexed_at 체크로 이미 인덱싱된 파일은 재처리하지 않음
- 단일 파일 임베딩은 해당 id 만 대상으로 실행 (H5: 전체 재임베딩 금지)
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver  # L11: 정확한 인스턴스 타입

from server.config import (
    GALLERY_ROOT,
    GIF_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MAX_MEDIA_DURATION,
    VIDEO_EXTENSIONS,
    WATCHDOG_DEBOUNCE_SECONDS,
)

logger = logging.getLogger(__name__)


# ── 이벤트 핸들러 + 단일 debounce 스케줄러 (H20) ──────────────────────────────

class _DebounceScheduler:
    """파일 경로별 ready_at 시점을 관리하는 단일 스케줄러.

    H20: 파일마다 threading.Timer 를 만들면 N개 동시 생성 시 스레드 폭증.
    대신 {path: ready_at_monotonic} 맵 + condition variable 로 단일 워커가 처리.
    """

    def __init__(self, work_queue: queue.Queue, delay: float) -> None:
        self._queue = work_queue
        self._delay = delay
        self._pending: dict[str, float] = {}
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="watcher-debounce", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._pending.clear()
            self._cv.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def schedule(self, path: str) -> None:
        ready_at = time.monotonic() + self._delay
        with self._cv:
            self._pending[path] = ready_at
            self._cv.notify_all()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                if not self._pending:
                    self._cv.wait(timeout=5.0)
                    continue
                now = time.monotonic()
                next_ready = min(self._pending.values())
                if next_ready > now:
                    self._cv.wait(timeout=max(0.05, next_ready - now))
                    continue
                ready = [p for p, t in self._pending.items() if t <= now]
                for p in ready:
                    self._pending.pop(p, None)
            for p in ready:
                self._queue.put(p)


class _GalleryEventHandler(FileSystemEventHandler):
    """파일시스템 이벤트를 debounce 스케줄러에 전달한다."""

    def __init__(self, scheduler: _DebounceScheduler) -> None:
        super().__init__()
        self._scheduler = scheduler

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._scheduler.schedule(event.src_path.replace("\\", "/"))

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._scheduler.schedule(event.dest_path.replace("\\", "/"))


# ── 워커 스레드 ───────────────────────────────────────────────────────────────

def _classify(filepath: str) -> str | None:
    ext = Path(filepath).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in GIF_EXTENSIONS:
        return "gif"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def _process_single_file(
    filepath: str,
    is_ingest_running: Callable[[], bool],
    stop_event: threading.Event,
    should_skip: Callable[[str], bool] | None = None,
    requeue: Callable[[str], None] | None = None,
) -> None:
    """단일 파일 인제스천 파이프라인을 실행한다."""
    if should_skip is not None and should_skip(filepath):
        logger.info("[watcher] 업로드 처리 중, 스킵: %s", filepath)
        return

    if not os.path.isfile(filepath):
        logger.info("[watcher] 파일 없음, 스킵: %s", filepath)
        return
    if os.path.getsize(filepath) == 0:
        logger.info("[watcher] 빈 파일 스킵 (0 bytes): %s", filepath)
        return

    media_type = _classify(filepath)
    if media_type is None:
        return

    if media_type in ("gif", "video"):
        try:
            from server.ingest.video import get_video_duration
            dur = get_video_duration(filepath)
            if dur > MAX_MEDIA_DURATION:
                logger.info(
                    "[watcher] 길이 초과 스킵 (%.0f초 > %d초): %s",
                    dur, MAX_MEDIA_DURATION, filepath,
                )
                return
        except Exception as e:
            logger.warning("[watcher] 길이 확인 실패 (%s): %s", filepath, e)
            return

    from server.db.sqlite import get_media_by_filepath, insert_media

    media_id = insert_media(filepath, media_type)

    if media_id is None:
        row = get_media_by_filepath(filepath)
        if row is None:
            logger.warning("[watcher] DB 조회 실패, 스킵: %s", filepath)
            return
        if row["indexed_at"] is not None:
            return
        media_id = row["id"]

    logger.info("[watcher] 신규 파일 감지 → 파이프라인 시작 (id=%s): %s", media_id, filepath)

    # H2: self._stop_event.wait 로 cooperative sleep — shutdown 시 즉시 깨어남
    waited = 0.0
    interval = 2.0
    while is_ingest_running() and waited < 120.0:
        if stop_event.wait(interval):
            logger.info("[watcher] 종료 신호 수신, 처리 중단: %s", filepath)
            return
        waited += interval
    if is_ingest_running():
        # M9: 타임아웃 시 파일 드롭 금지 — 재큐로 나중에 재시도
        logger.warning("[watcher] 수동 인제스천 대기 타임아웃, 재큐: %s", filepath)
        if requeue is not None:
            requeue(filepath)
        return

    item = {"id": media_id, "filepath": filepath, "media_type": media_type}
    try:
        # M25: repair_qdrant_consistency 제거 — 단일 파일 경로에서는 Qdrant/SQLite
        # 양쪽 모두 방금 이 함수가 직접 write. 전체 스크롤 비용(O(컬렉션 크기))을
        # 매 파일마다 지불할 근거 없음. run_full_pipeline 에서만 수행.
        from server.ingest.pipeline import (
            run_ocr_and_thumbnail_pipeline,
            run_embed_pipeline,
        )

        run_ocr_and_thumbnail_pipeline([item])
        # H5: 전체 재임베딩 금지 — 방금 처리한 단일 항목만 임베딩
        run_embed_pipeline([item])
        # H4: tag_stats 는 _process_media 내부에서 증분 반영되므로 rebuild 불필요
        logger.info("[watcher] 파이프라인 완료 (id=%s): %s", media_id, filepath)
    except Exception as e:
        # M22: 일시적 실패(네트워크·락 등)는 재시도. 횟수 초과 시만 드롭.
        logger.exception("[watcher] 파이프라인 예외 (id=%s, %s): %s", media_id, filepath, e)
        if requeue is not None:
            requeue(filepath)


# ── GalleryWatcher 퍼블릭 클래스 ─────────────────────────────────────────────

class GalleryWatcher:
    """GALLERY_ROOT를 감시하고 신규 파일을 자동 인제스천한다."""

    def __init__(
        self,
        is_ingest_running: Callable[[], bool],
        should_skip: Callable[[str], bool] | None = None,
    ) -> None:
        self._is_ingest_running = is_ingest_running
        self._should_skip = should_skip
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        # L11: watchdog.observers.Observer 는 플랫폼별 구현을 돌려주는 factory 이므로
        # 실제 인스턴스 타입은 BaseObserver 서브클래스. 타입 힌트는 BaseObserver 사용.
        self._observer: BaseObserver | None = None
        self._handler: _GalleryEventHandler | None = None
        self._scheduler: _DebounceScheduler | None = None
        self._worker_thread: threading.Thread | None = None
        self._processed = 0
        self._errors = 0
        # M22: filepath → 남은 재시도 횟수. 최대 2회 재시도.
        self._retry_budget: dict[str, int] = {}
        self._retry_lock = threading.Lock()
        self._MAX_RETRY = 2

    def start(self) -> None:
        if self._observer is not None and self._observer.is_alive():
            return

        if not os.path.isdir(GALLERY_ROOT):
            logger.warning("[watcher] GALLERY_ROOT 없음, 감시 스킵: %s", GALLERY_ROOT)
            return

        self._stop_event.clear()
        self._scheduler = _DebounceScheduler(self._queue, WATCHDOG_DEBOUNCE_SECONDS)
        self._scheduler.start()
        self._handler = _GalleryEventHandler(self._scheduler)

        self._observer = Observer()
        self._observer.schedule(self._handler, GALLERY_ROOT, recursive=True)
        self._observer.start()

        self._worker_thread = threading.Thread(
            target=self._worker, name="watcher-worker", daemon=True,
        )
        self._worker_thread.start()
        logger.info("[watcher] 감시 시작: %s", GALLERY_ROOT)

    def stop(self) -> None:
        self._stop_event.set()

        if self._scheduler is not None:
            self._scheduler.stop()
            self._scheduler = None

        if self._observer is not None and self._observer.is_alive():
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5)
            self._worker_thread = None

        logger.info("[watcher] 감시 중지")

    def is_alive(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def stats(self) -> dict:
        return {
            "running": self.is_alive(),
            "watch_path": GALLERY_ROOT,
            "processed": self._processed,
            "errors": self._errors,
            "queue_size": self._queue.qsize(),
        }

    def _requeue(self, filepath: str) -> None:
        """M22: 재시도 예산이 남은 경우만 큐에 재투입."""
        with self._retry_lock:
            remaining = self._retry_budget.get(filepath, self._MAX_RETRY)
            if remaining <= 0:
                logger.warning("[watcher] 재시도 예산 소진, 드롭: %s", filepath)
                self._retry_budget.pop(filepath, None)
                return
            self._retry_budget[filepath] = remaining - 1
        if self._scheduler is not None:
            self._scheduler.schedule(filepath)  # debounce 재적용 후 큐로 복귀

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                filepath = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                _process_single_file(
                    filepath,
                    self._is_ingest_running,
                    self._stop_event,
                    should_skip=self._should_skip,
                    requeue=self._requeue,
                )
                self._processed += 1
                with self._retry_lock:
                    self._retry_budget.pop(filepath, None)
            except Exception as e:
                self._errors += 1
                logger.exception("[watcher] 워커 예외 (%s): %s", filepath, e)
                self._requeue(filepath)
            finally:
                self._queue.task_done()
