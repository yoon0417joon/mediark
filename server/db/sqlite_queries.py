"""미디어 조회·tag_stats·FTS 헬퍼 — sqlite.py(연결·마이그레이션)에서 분리."""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter

from server.db.sqlite import connect, fts_enabled, get_connection

logger = logging.getLogger(__name__)


def get_audio_unprocessed_videos() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, filepath, media_type FROM media "
            "WHERE media_type='video' AND thumb_path IS NOT NULL AND thumb_path != '' "
            "AND audio_text IS NULL"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_unprocessed_media() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, filepath, media_type FROM media "
            "WHERE thumb_path IS NULL OR thumb_path='' OR ram_tags IS NULL"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_missing_ram_tags_media() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, filepath, media_type FROM media "
            "WHERE ram_tags IS NULL AND thumb_path IS NOT NULL AND thumb_path != ''"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_unembedded_media() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, filepath, media_type, ocr_text, tags, ram_tags, audio_text, thumb_path "
            "FROM media "
            "WHERE thumb_path IS NOT NULL AND thumb_path != '' "
            "AND indexed_at IS NULL "
            "AND (index_error IS NULL OR index_error = '')"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_unembedded_media_by_ids(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    conn = get_connection()
    try:
        ph = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, filepath, media_type, ocr_text, tags, ram_tags, audio_text, thumb_path "
            f"FROM media WHERE id IN ({ph}) "
            f"AND thumb_path IS NOT NULL AND thumb_path != '' "
            f"AND indexed_at IS NULL "
            f"AND (index_error IS NULL OR index_error = '')",
            ids,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_unindexed_ids_from(ids: list[int]) -> list[int]:
    if not ids:
        return []
    conn = get_connection()
    try:
        ph = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id FROM media WHERE id IN ({ph}) AND indexed_at IS NULL",
            ids,
        ).fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


def get_indexed_media_ids() -> list[int]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id FROM media WHERE indexed_at IS NOT NULL"
        ).fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


def _tokenize_tags(s: str | None) -> list[str]:
    if not s:
        return []
    return [t for t in (p.strip() for p in s.split(",")) if t]


def _apply_tag_stats_delta(conn: sqlite3.Connection, delta: Counter, source: str) -> None:
    for tag, n in delta.items():
        if n == 0:
            continue
        if n > 0:
            conn.execute(
                "INSERT INTO tag_stats (tag, source, count) VALUES (?, ?, ?) "
                "ON CONFLICT(tag, source) DO UPDATE SET count = count + ?",
                (tag, source, n, n),
            )
        else:
            conn.execute(
                "UPDATE tag_stats SET count = count + ? WHERE tag = ? AND source = ?",
                (n, tag, source),
            )
    conn.execute("DELETE FROM tag_stats WHERE count <= 0 AND source = ?", (source,))


def apply_tag_stats_delta(
    old_wd14: str | None,
    new_wd14: str | None,
    old_ram: str | None,
    new_ram: str | None,
) -> None:
    """단일 미디어의 태그 변화분만 tag_stats 에 반영한다 (full scan 회피)."""
    wd_delta: Counter = Counter()
    for t in _tokenize_tags(old_wd14):
        wd_delta[t] -= 1
    for t in _tokenize_tags(new_wd14):
        wd_delta[t] += 1
    ram_delta: Counter = Counter()
    for t in _tokenize_tags(old_ram):
        ram_delta[t] -= 1
    for t in _tokenize_tags(new_ram):
        ram_delta[t] += 1
    if not wd_delta and not ram_delta:
        return
    with connect() as conn:
        if wd_delta:
            _apply_tag_stats_delta(conn, wd_delta, "wd14")
        if ram_delta:
            _apply_tag_stats_delta(conn, ram_delta, "ram")


def rebuild_tag_stats() -> None:
    """tag_stats 재구성 (초기화 / 정합성 복구용). 인제스천 경로에서 일상적으로 호출하지 말 것 (H4)."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT tags, ram_tags FROM media").fetchall()
    finally:
        conn.close()

    wd14_counter: Counter = Counter()
    ram_counter: Counter = Counter()
    for row in rows:
        for t in _tokenize_tags(row["tags"]):
            wd14_counter[t] += 1
        for t in _tokenize_tags(row["ram_tags"]):
            ram_counter[t] += 1

    with connect() as conn:
        conn.execute("DELETE FROM tag_stats")
        if wd14_counter:
            conn.executemany(
                "INSERT INTO tag_stats (tag, source, count) VALUES (?, 'wd14', ?)",
                list(wd14_counter.items()),
            )
        if ram_counter:
            conn.executemany(
                "INSERT INTO tag_stats (tag, source, count) VALUES (?, 'ram', ?)",
                list(ram_counter.items()),
            )
    logger.info(
        "[sqlite] tag_stats rebuild: WD14=%d, RAM=%d",
        len(wd14_counter),
        len(ram_counter),
    )


def suggest_tags(prefix: str, source: str, limit: int = 20) -> list[dict]:
    if not prefix:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT tag, count FROM tag_stats "
            "WHERE tag LIKE ? AND source = ? "
            "ORDER BY count DESC LIMIT ?",
            (f"{prefix}%", source, limit),
        ).fetchall()
        return [{"tag": r["tag"], "count": r["count"]} for r in rows]
    finally:
        conn.close()


def get_random_media(limit: int = 50, media_type: str | None = None) -> list[dict]:
    """썸네일 있는 미디어를 근사 랜덤으로 반환한다.

    ORDER BY RANDOM() 은 전체 테이블 정렬 → 대규모에서 O(N log N) 발생.
    대신 MIN/MAX id 범위에서 무작위 ID 를 샘플링해 근사 결과를 돌려준다.
    """
    import random

    conn = get_connection()
    try:
        where = "thumb_path IS NOT NULL AND thumb_path != ''"
        params: list = []
        if media_type in ("image", "gif", "video"):
            where += " AND media_type = ?"
            params.append(media_type)

        bounds = conn.execute(
            f"SELECT MIN(id) AS lo, MAX(id) AS hi, COUNT(*) AS n FROM media WHERE {where}",
            params,
        ).fetchone()
        lo = bounds["lo"]
        hi = bounds["hi"]
        n = bounds["n"]
        if not n or lo is None or hi is None:
            return []

        if n <= max(limit * 4, 256):
            rows = conn.execute(
                f"SELECT id, filepath, media_type, thumb_path FROM media "
                f"WHERE {where} ORDER BY RANDOM() LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [dict(r) for r in rows]

        seen: dict[int, dict] = {}
        attempts = 0
        target = limit
        while len(seen) < target and attempts < 8:
            attempts += 1
            need = target - len(seen)
            seed = random.randint(lo, max(lo, hi - 1))
            rows = conn.execute(
                f"SELECT id, filepath, media_type, thumb_path FROM media "
                f"WHERE {where} AND id >= ? LIMIT ?",
                (*params, seed, need * 4),
            ).fetchall()
            for r in rows:
                if r["id"] not in seen:
                    seen[r["id"]] = dict(r)
                    if len(seen) >= target:
                        break
        items = list(seen.values())
        random.shuffle(items)
        return items[:target]
    finally:
        conn.close()


def get_media_page(page: int = 1, per_page: int = 50) -> tuple[list[dict], int]:
    offset = (page - 1) * per_page
    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM media WHERE thumb_path IS NOT NULL AND thumb_path != ''"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT id, filepath, media_type, thumb_path FROM media "
            "WHERE thumb_path IS NOT NULL AND thumb_path != '' "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (per_page, offset),
        ).fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()


def _fts_escape_phrase(q: str) -> str:
    """FTS5 phrase 인자로 안전하게 사용할 문자열 (이중따옴표 이스케이프)."""
    return q.replace('"', '""')


def fts_match_ids(field: str, q: str) -> list[int] | None:
    """FTS5 phrase MATCH — 구문 전체 일치(따옴표 phrase). 3자 미만은 None → LIKE 폴백.

    field: ocr_text | tags | ram_tags | audio_text
    """
    if not fts_enabled() or len(q) < 3:
        return None
    conn = get_connection()
    try:
        phrase = f'"{_fts_escape_phrase(q)}"'
        expr = f"{field} : {phrase}"
        try:
            rows = conn.execute(
                "SELECT rowid AS id FROM media_fts WHERE media_fts MATCH ?",
                (expr,),
            ).fetchall()
        except sqlite3.DatabaseError as e:
            logger.warning("[sqlite] FTS5 MATCH 실패 (%s) → LIKE 폴백: %s", q, e)
            return None
        return [r["id"] for r in rows]
    finally:
        conn.close()
