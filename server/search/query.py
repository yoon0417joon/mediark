"""벡터 검색 + 소스별 strict 필터 로직.

검색 파이프라인:
  1. 각 활성 소스(ocr/wd14/ram/stt)별로 FTS5 MATCH 필터링 → AND 교집합 id 목록 (H7)
  2. 각 소스별로 개별 임베딩 후 Qdrant 검색 → 소스별 최대값 융합 (H9)
  3. strict id 목록을 청크로 나눠 Qdrant 에 전달하여 전 후보 획득 (H6)
  4. 전체 정렬 후 page/per_page 슬라이싱
"""

from __future__ import annotations

import heapq
import logging
import time
from typing import Optional

from qdrant_client.models import Filter, HasIdCondition

from server.config import LIKE_FALLBACK_MAX_ROWS, QDRANT_COLLECTION
from server.db.qdrant import collection_exists, get_client
from server.db.sqlite import fts_match_ids, get_connection
from server.search.embed import get_embedding

logger = logging.getLogger(__name__)

# Qdrant 1회 요청당 최대 후보 수 (H23: 초과 시 경고 로깅)
_MAX_CANDIDATES = 10_000
# strict_ids 를 Qdrant HasIdCondition 으로 넘길 때 청크 크기 (H6: 페이지네이션)
_ID_CHUNK = 2_000


def _fts_field(source: str) -> str:
    return {"ocr": "ocr_text", "wd14": "tags", "ram": "ram_tags", "stt": "audio_text"}[source]


def _like_match_ids(field: str, q: str) -> list[int]:
    """FTS5 미사용 시 LIKE 기반 폴백. 짧은 쿼리는 행 상한으로 풀스캔 비용 완화 (MEDIUM-2)."""
    conn = get_connection()
    try:
        lim = LIKE_FALLBACK_MAX_ROWS if len(q) < 3 else None
        sql = f"SELECT id FROM media WHERE {field} LIKE ?"
        params: list = [f"%{q}%"]
        if lim is not None:
            sql += " LIMIT ?"
            params.append(lim)
        rows = conn.execute(sql, params).fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


def _strict_filter_ids(active: dict[str, str], media_type: Optional[str]) -> Optional[list[int]]:
    """활성 소스별 FTS5 MATCH 로 AND 교집합 id 목록을 구한다 (H7)."""
    sets: list[set[int]] = []
    for source, q in active.items():
        field = _fts_field(source)
        fts = fts_match_ids(field, q)
        ids = set(fts) if fts is not None else set(_like_match_ids(field, q))
        if not ids:
            return []
        sets.append(ids)

    if not sets and media_type is None:
        return None

    strict: Optional[set[int]] = None
    if sets:
        strict = set.intersection(*sets)
        if not strict:
            return []

    if media_type in ("image", "gif", "video"):
        conn = get_connection()
        try:
            if strict is not None and len(strict) <= 5000:
                placeholders = ",".join("?" * len(strict))
                rows = conn.execute(
                    f"SELECT id FROM media WHERE media_type = ? AND id IN ({placeholders})",
                    [media_type, *strict],
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM media WHERE media_type = ?", [media_type],
                ).fetchall()
                type_ids = {r["id"] for r in rows}
                if strict is None:
                    return sorted(type_ids) if type_ids else []
                strict &= type_ids
                return sorted(strict) if strict else []
            return [r["id"] for r in rows]
        finally:
            conn.close()

    return sorted(strict) if strict else []


def _qdrant_search_with_ids(
    client, vector: list[float], strict_ids: Optional[list[int]],
) -> dict[int, tuple[float, dict]]:
    """Qdrant 검색을 수행하되 strict_ids 가 매우 길면 청크로 나눠 호출한다 (H6).

    Returns: {id: (score, payload)}
    """
    hits_map: dict[int, tuple[float, dict]] = {}

    def run(filter_ids: Optional[list[int]], limit: int) -> None:
        qf = Filter(must=[HasIdCondition(has_id=filter_ids)]) if filter_ids is not None else None
        response = client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=vector,
            query_filter=qf,
            limit=limit,
            with_payload=True,
        )
        for hit in response.points:
            prev = hits_map.get(hit.id)
            if prev is None or hit.score > prev[0]:
                hits_map[hit.id] = (hit.score, hit.payload or {})

    if strict_ids is None:
        if _MAX_CANDIDATES <= 0:
            return hits_map
        # H23: 결과가 상한에 닿으면 경고 — 사일런트 캡 방지
        run(None, _MAX_CANDIDATES)
        if len(hits_map) >= _MAX_CANDIDATES:
            logger.warning(
                "[query] Qdrant 후보 상한 도달 (%d) — 결과가 잘릴 수 있음",
                _MAX_CANDIDATES,
            )
        return hits_map

    # H6: strict_ids 가 _ID_CHUNK 초과면 청크로 분할 호출
    for start in range(0, len(strict_ids), _ID_CHUNK):
        chunk = strict_ids[start : start + _ID_CHUNK]
        run(chunk, len(chunk))
    return hits_map


def search(
    ocr_q: Optional[str] = None,
    wd14_q: Optional[str] = None,
    ram_q: Optional[str] = None,
    stt_q: Optional[str] = None,
    media_type: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int, float]:
    """소스별 쿼리로 관련 미디어를 반환한다."""
    t_start = time.perf_counter()

    active: dict[str, str] = {}
    if ocr_q and ocr_q.strip():
        active["ocr"] = ocr_q.strip()
    if wd14_q and wd14_q.strip():
        active["wd14"] = wd14_q.strip()
    if ram_q and ram_q.strip():
        active["ram"] = ram_q.strip()
    if stt_q and stt_q.strip():
        active["stt"] = stt_q.strip()

    if not active:
        raise ValueError("최소 1개 이상의 검색어가 필요합니다")

    # 1. strict 필터 (H7: FTS5 MATCH) + media_type 교집합
    strict_ids = _strict_filter_ids(active, media_type)
    if strict_ids is not None and not strict_ids:
        elapsed_ms = (time.perf_counter() - t_start) * 1000
        return [], 0, elapsed_ms

    if not collection_exists():
        elapsed_ms = (time.perf_counter() - t_start) * 1000
        return [], 0, elapsed_ms

    client = get_client()

    # 2. H9: 각 소스별로 개별 임베딩 후 검색 → 소스별 점수 최대값 융합
    fused: dict[int, tuple[float, dict]] = {}
    for q in active.values():
        vector = get_embedding(q)
        hits = _qdrant_search_with_ids(client, vector, strict_ids)
        for mid, (score, payload) in hits.items():
            prev = fused.get(mid)
            if prev is None or score > prev[0]:
                fused[mid] = (score, payload)

    if not fused:
        elapsed_ms = (time.perf_counter() - t_start) * 1000
        return [], 0, elapsed_ms

    # 3. ram_tags 표시용 조회
    media_ids = list(fused.keys())
    ram_tags_map: dict[int, str] = {}
    conn = get_connection()
    try:
        for start in range(0, len(media_ids), 900):
            chunk = media_ids[start : start + 900]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT id, ram_tags FROM media WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                ram_tags_map[row["id"]] = row["ram_tags"] or ""
    finally:
        conn.close()

    # 4. M13: 전체 dict sort 회피 — 필요한 상위 (offset+per_page) 개만 heap 으로 추출
    total = len(fused)
    offset = (page - 1) * per_page
    top_n = offset + per_page
    # heapq.nlargest(k, data): len(data)=n 일 때 대략 O(n log k) (Python 문서)
    top_items = heapq.nlargest(top_n, fused.items(), key=lambda kv: kv[1][0])
    page_slice = top_items[offset : offset + per_page]
    page_results = [
        {
            "media_id":   mid,
            "filepath":   payload.get("filepath", ""),
            "media_type": payload.get("media_type", ""),
            "thumb_path": payload.get("thumb_path", ""),
            "ram_tags":   ram_tags_map.get(mid, ""),
            "score":      round(score, 4),
        }
        for mid, (score, payload) in page_slice
    ]

    elapsed_ms = (time.perf_counter() - t_start) * 1000
    return page_results, total, elapsed_ms
