"""SQLite 연결, 마이그레이션, media 테이블 헬퍼.

QA 2026-04-18 해결:
- H4 : tag_stats 증분 갱신 (add_tag_stats / remove_tag_stats)
- H7 : FTS5(trigram) 가상 테이블 + 트리거로 substring 검색 가속
- H8 : get_random_media 오프셋 기반 근사 샘플링
- H18: 커넥션 풀 (LIFO) — 재사용으로 connect() 비용 제거
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import sqlite3
import threading

from server.config import SQLITE_PATH as _DEFAULT_SQLITE_PATH

logger = logging.getLogger(__name__)


def _sqlite_path() -> str:
    """SQLITE_PATH 를 환경변수에서 즉시 재평가 — 테스트 격리 지원."""
    return os.environ.get("SQLITE_PATH", _DEFAULT_SQLITE_PATH)


CREATE_MEDIA_TABLE = """
CREATE TABLE IF NOT EXISTS media (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath    TEXT NOT NULL UNIQUE,
    media_type  TEXT NOT NULL,
    ocr_text    TEXT,
    tags        TEXT,
    ram_tags    TEXT,
    audio_text  TEXT,
    thumb_path  TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    indexed_at  DATETIME
);
"""

CREATE_TAG_STATS_TABLE = """
CREATE TABLE IF NOT EXISTS tag_stats (
    tag     TEXT NOT NULL,
    source  TEXT NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tag, source)
);
"""

CREATE_MEDIA_REPORTS_TABLE = """
CREATE TABLE IF NOT EXISTS media_reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id     INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    reporter_id  INTEGER REFERENCES users(id),
    reason       TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    reviewed_by  INTEGER REFERENCES users(id),
    reviewed_at  DATETIME,
    notes        TEXT
);
"""

# ── 커넥션 풀 (H18) ────────────────────────────────────────────────────────────

_POOL_MAX = 16
_pool: queue.LifoQueue = queue.LifoQueue(maxsize=_POOL_MAX)
_pool_path: str | None = None
_pool_lock = threading.Lock()


class _PooledConnection:
    """sqlite3.Connection 래퍼. close() 시 풀에 반환한다."""

    __slots__ = ("_conn", "_closed")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._closed = False

    # 자주 쓰는 API 는 명시 위임 — IDE/타입 추적용 (LOW-4)
    def execute(self, sql: str, parameters=()):
        return self._conn.execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters):
        return self._conn.executemany(sql, seq_of_parameters)

    def executescript(self, sql_script: str) -> None:
        return self._conn.executescript(sql_script)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    @property
    def in_transaction(self) -> bool:
        return self._conn.in_transaction

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, *a):
        return self._conn.__exit__(*a)

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        conn = self._conn
        try:
            # 트랜잭션 미종료 시 롤백 (write 는 명시적 commit 되어 있어야 함)
            if conn.in_transaction:
                conn.rollback()
        except Exception:
            pass
        try:
            _pool.put_nowait(conn)
        except queue.Full:
            try:
                conn.close()
            except Exception:
                pass


def _new_raw_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(_sqlite_path(), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # synchronous=NORMAL: 속도·내구성 절충 — 정전 시 FULL보다 마지막 커밋 손실 가능성 큼 (MEDIUM-7)
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _drain_pool_if_path_changed() -> None:
    global _pool_path
    path = _sqlite_path()
    with _pool_lock:
        if _pool_path != path:
            # SQLITE_PATH 변경 (테스트) — 기존 풀 폐기
            while True:
                try:
                    c = _pool.get_nowait()
                except queue.Empty:
                    break
                try:
                    c.close()
                except Exception:
                    pass
            _pool_path = path


def get_connection() -> _PooledConnection:
    """풀에서 연결을 꺼내 반환한다 (없으면 새로 생성)."""
    _drain_pool_if_path_changed()
    try:
        raw = _pool.get_nowait()
    except queue.Empty:
        raw = _new_raw_connection()
    return _PooledConnection(raw)


@contextlib.contextmanager
def connect():
    """컨텍스트 매니저. 정상 종료 시 commit, 예외 시 rollback."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ── 마이그레이션 ───────────────────────────────────────────────────────────────

_INIT_LOCK = threading.Lock()
_INITIALIZED = False


def _has_column(conn: sqlite3.Connection, col: str) -> bool:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(media)").fetchall()}
    return col in cols


def _migrate_add_column(col: str, sql_type: str) -> None:
    with connect() as conn:
        if not _has_column(conn, col):
            conn.execute(f"ALTER TABLE media ADD COLUMN {col} {sql_type}")
            logger.info("[sqlite] %s 컬럼 추가", col)


def _migrate_tag_stats() -> None:
    with connect() as conn:
        conn.execute(CREATE_TAG_STATS_TABLE)


def _migrate_indexes() -> None:
    """자주 사용되는 필터 컬럼에 인덱스 추가 (H7/H8 일부 완화)."""
    stmts = [
        "CREATE INDEX IF NOT EXISTS idx_media_media_type ON media(media_type)",
        "CREATE INDEX IF NOT EXISTS idx_media_thumb_indexed ON media(thumb_path, indexed_at)",
        "CREATE INDEX IF NOT EXISTS idx_media_indexed_at ON media(indexed_at)",
        "CREATE INDEX IF NOT EXISTS idx_media_file_hash ON media(file_hash)",
    ]
    with connect() as conn:
        for s in stmts:
            conn.execute(s)


# ── FTS5 (H7) ─────────────────────────────────────────────────────────────────

_FTS_ENABLED: bool | None = None


def _fts_supported(conn: sqlite3.Connection) -> bool:
    """현재 SQLite 바이너리가 FTS5 trigram 을 지원하는지 확인."""
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x, tokenize='trigram')"
        )
        conn.execute("DROP TABLE _fts_probe")
        return True
    except sqlite3.DatabaseError:
        return False


def _migrate_fts() -> None:
    global _FTS_ENABLED
    with connect() as conn:
        if not _fts_supported(conn):
            _FTS_ENABLED = False
            logger.warning("[sqlite] FTS5 trigram 미지원 — LIKE 폴백 사용")
            return
        _FTS_ENABLED = True
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS media_fts USING fts5("
            "ocr_text, tags, ram_tags, audio_text, "
            "content='media', content_rowid='id', tokenize='trigram')"
        )
        # 트리거: INSERT / UPDATE / DELETE 동기화
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS media_ai AFTER INSERT ON media BEGIN
                INSERT INTO media_fts(rowid, ocr_text, tags, ram_tags, audio_text)
                VALUES (new.id, new.ocr_text, new.tags, new.ram_tags, new.audio_text);
            END;
            CREATE TRIGGER IF NOT EXISTS media_ad AFTER DELETE ON media BEGIN
                INSERT INTO media_fts(media_fts, rowid, ocr_text, tags, ram_tags, audio_text)
                VALUES ('delete', old.id, old.ocr_text, old.tags, old.ram_tags, old.audio_text);
            END;
            CREATE TRIGGER IF NOT EXISTS media_au AFTER UPDATE ON media BEGIN
                INSERT INTO media_fts(media_fts, rowid, ocr_text, tags, ram_tags, audio_text)
                VALUES ('delete', old.id, old.ocr_text, old.tags, old.ram_tags, old.audio_text);
                INSERT INTO media_fts(rowid, ocr_text, tags, ram_tags, audio_text)
                VALUES (new.id, new.ocr_text, new.tags, new.ram_tags, new.audio_text);
            END;
            """
        )
        # 백필 (비어있으면만)
        cnt = conn.execute("SELECT COUNT(*) FROM media_fts").fetchone()[0]
        if cnt == 0:
            rowcount = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
            if rowcount > 0:
                conn.execute(
                    "INSERT INTO media_fts(rowid, ocr_text, tags, ram_tags, audio_text) "
                    "SELECT id, ocr_text, tags, ram_tags, audio_text FROM media"
                )
                logger.info("[sqlite] media_fts 백필 완료 (%d rows)", rowcount)


def fts_enabled() -> bool:
    return bool(_FTS_ENABLED)


def init_db() -> None:
    """테이블·인덱스·FTS5·마이그레이션을 멱등하게 적용한다."""
    global _INITIALIZED
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        with connect() as conn:
            conn.execute(CREATE_MEDIA_TABLE)
        for col, typ in (
            ("ram_tags", "TEXT"),
            ("audio_text", "TEXT"),
            ("index_error", "TEXT"),
            ("file_hash", "TEXT"),
            ("hidden", "INTEGER NOT NULL DEFAULT 0"),
        ):
            _migrate_add_column(col, typ)
        _migrate_tag_stats()
        _migrate_indexes()
        _migrate_fts()
        with connect() as conn:
            conn.execute(CREATE_MEDIA_REPORTS_TABLE)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_reports_status "
                "ON media_reports(status)"
            )
        _INITIALIZED = True
        logger.info("[sqlite] init_db 완료: %s (fts=%s)", _sqlite_path(), _FTS_ENABLED)


# ── 기본 CRUD ─────────────────────────────────────────────────────────────────

def insert_media(filepath: str, media_type: str) -> int | None:
    """INSERT OR IGNORE. 중복 시 None."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO media (filepath, media_type) VALUES (?, ?)",
            (filepath, media_type),
        )
        if cur.lastrowid and cur.rowcount > 0:
            return cur.lastrowid
        return None


def get_media_by_id(media_id: int) -> sqlite3.Row | None:
    conn = get_connection()
    try:
        return conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
    finally:
        conn.close()


def get_media_by_filepath(filepath: str) -> sqlite3.Row | None:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM media WHERE filepath = ?", (filepath,)
        ).fetchone()
    finally:
        conn.close()


def get_all_filepaths() -> set[str]:
    conn = get_connection()
    try:
        return {r["filepath"] for r in conn.execute("SELECT filepath FROM media")}
    finally:
        conn.close()


def _update_field(col: str, media_id: int, value) -> None:
    with connect() as conn:
        conn.execute(f"UPDATE media SET {col} = ? WHERE id = ?", (value, media_id))


def update_ocr_text(media_id: int, ocr_text: str | None) -> None:
    _update_field("ocr_text", media_id, ocr_text)


def update_thumb_path(media_id: int, thumb_path: str | None) -> None:
    _update_field("thumb_path", media_id, thumb_path)


def update_tags(media_id: int, tags: str | None) -> None:
    _update_field("tags", media_id, tags)


def update_ram_tags(media_id: int, ram_tags: str | None) -> None:
    _update_field("ram_tags", media_id, ram_tags)


def update_audio_text(media_id: int, audio_text: str | None) -> None:
    _update_field("audio_text", media_id, audio_text)


def update_index_error(media_id: int, err: str | None) -> None:
    _update_field("index_error", media_id, err)


def update_file_hash(media_id: int, file_hash: str) -> None:
    _update_field("file_hash", media_id, file_hash)


def set_media_hidden(media_id: int, hidden: bool) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE media SET hidden = ? WHERE id = ?",
            (1 if hidden else 0, media_id),
        )
        return (cur.rowcount or 0) > 0


def is_media_hidden(media_id: int) -> bool:
    row = get_media_by_id(media_id)
    if row is None:
        return False
    try:
        return int(row["hidden"] or 0) != 0
    except (KeyError, IndexError):
        return False


def delete_media_row(media_id: int) -> sqlite3.Row | None:
    """행 삭제 전 스냅샷 반환. 없으면 None."""
    row = get_media_by_id(media_id)
    if row is None:
        return None
    with connect() as conn:
        conn.execute("DELETE FROM media WHERE id = ?", (media_id,))
    return row


def insert_media_report(
    *,
    media_id: int,
    reporter_id: int | None,
    reason: str | None,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO media_reports (media_id, reporter_id, reason, status) "
            "VALUES (?, ?, ?, 'pending')",
            (media_id, reporter_id, (reason or "").strip() or None),
        )
        if cur.lastrowid is None:
            raise RuntimeError("media_reports insert 실패")
        return int(cur.lastrowid)


def list_media_reports(*, status: str | None = None) -> list[sqlite3.Row]:
    conn = get_connection()
    try:
        if status:
            return list(
                conn.execute(
                    "SELECT * FROM media_reports WHERE status = ? ORDER BY id DESC",
                    (status,),
                )
            )
        return list(conn.execute("SELECT * FROM media_reports ORDER BY id DESC"))
    finally:
        conn.close()


def get_media_report_by_id(report_id: int) -> sqlite3.Row | None:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM media_reports WHERE id = ?", (report_id,)
        ).fetchone()
    finally:
        conn.close()


def resolve_media_report(
    *,
    report_id: int,
    reviewer_id: int,
    status: str,
    notes: str | None,
) -> bool:
    if status not in ("reviewed", "dismissed"):
        raise ValueError("status 는 reviewed 또는 dismissed")
    with connect() as conn:
        cur = conn.execute(
            "UPDATE media_reports SET status = ?, reviewed_by = ?, "
            "reviewed_at = CURRENT_TIMESTAMP, notes = ? "
            "WHERE id = ? AND status = 'pending'",
            (status, reviewer_id, (notes or "").strip() or None, report_id),
        )
        return (cur.rowcount or 0) > 0


def get_media_by_hash(file_hash: str) -> sqlite3.Row | None:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM media WHERE file_hash = ?", (file_hash,)
        ).fetchone()
    finally:
        conn.close()


def update_indexed_at(media_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE media SET indexed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (media_id,),
        )


def reset_indexed_at(media_id: int) -> None:
    _update_field("indexed_at", media_id, None)


def update_media_atomic(
    media_id: int,
    *,
    ocr_text: str | None,
    tags: str | None,
    ram_tags: str | None,
    audio_text: str | None,
    thumb_path: str | None,
    skip_audio: bool = False,
) -> None:
    """여러 컬럼을 단일 UPDATE 로 원자적 갱신 (H13)."""
    if skip_audio:
        with connect() as conn:
            conn.execute(
                "UPDATE media SET ocr_text=?, tags=?, ram_tags=?, thumb_path=? "
                "WHERE id=?",
                (ocr_text, tags, ram_tags, thumb_path, media_id),
            )
    else:
        with connect() as conn:
            conn.execute(
                "UPDATE media SET ocr_text=?, tags=?, ram_tags=?, audio_text=?, "
                "thumb_path=? WHERE id=?",
                (ocr_text, tags, ram_tags, audio_text, thumb_path, media_id),
            )


# ── CLI (L4: 스크립트 진입점을 main() 으로 분리해 모듈 본체와 구분) ────────────


def main() -> None:
    """로컬 개발용: DB 스키마 초기화."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_db()
    logger.info("[sqlite] init_db 완료")


from server.db.sqlite_queries import (  # noqa: E402  — 순환 참조 회피, 풀·마이그레이션 후 재노출
    apply_tag_stats_delta,
    fts_match_ids,
    get_audio_unprocessed_videos,
    get_indexed_media_ids,
    get_media_page,
    get_missing_ram_tags_media,
    get_random_media,
    get_unembedded_media,
    get_unembedded_media_by_ids,
    get_unindexed_ids_from,
    get_unprocessed_media,
    rebuild_tag_stats,
    suggest_tags,
)


if __name__ == "__main__":
    main()
