"""Qdrant 로컬 모드 클라이언트 및 컬렉션 초기화."""

import logging
import os
import threading

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from server.config import QDRANT_PATH as _DEFAULT_QDRANT_PATH
from server.config import QDRANT_COLLECTION, EMBED_VECTOR_SIZE

logger = logging.getLogger(__name__)

_client: QdrantClient | None = None
_client_key: str | None = None
# M8: get_client / close 동시성 보호 — close+recreate 사이 race 차단.
_client_lock = threading.Lock()


def _qdrant_path() -> str:
    """QDRANT_PATH 를 환경변수에서 즉시 재평가 — 테스트 격리 지원."""
    return os.environ.get("QDRANT_PATH", _DEFAULT_QDRANT_PATH)


def _qdrant_url() -> str:
    """QDRANT_URL 환경변수 — 설정 시 HTTP 서비스 모드로 전환 (멀티프로세스 가능)."""
    return os.environ.get("QDRANT_URL", "").strip()


def _qdrant_api_key() -> str | None:
    val = os.environ.get("QDRANT_API_KEY", "").strip()
    return val or None


def get_client() -> QdrantClient:
    """싱글턴 QdrantClient 를 반환한다.

    QDRANT_URL 가 설정되면 HTTP 서비스 모드 (멀티프로세스·재시작 안전).
    그렇지 않으면 로컬 파일 모드 — 단일 프로세스 (--workers 1) 필수.

    M8: _client_lock 으로 close/recreate race 차단.
    """
    global _client, _client_key
    url = _qdrant_url()
    key = f"http::{url}" if url else f"file::{_qdrant_path()}"

    with _client_lock:
        if _client is not None and _client_key != key:
            try:
                _client.close()
            except Exception:
                pass
            _client = None

        if _client is None:
            if url:
                _client = QdrantClient(url=url, api_key=_qdrant_api_key())
                logger.info("[qdrant] HTTP 서비스 모드: %s", url)
            else:
                _client = QdrantClient(path=_qdrant_path())
            _client_key = key
        return _client


def init_collection() -> None:
    """gallery 컬렉션이 없으면 생성한다."""
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    if QDRANT_COLLECTION not in existing:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(
                size=EMBED_VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )
        logger.info("[qdrant] 컬렉션 생성: %s", QDRANT_COLLECTION)
    else:
        logger.info("[qdrant] 컬렉션 이미 존재: %s", QDRANT_COLLECTION)
    logger.info("[qdrant] 데이터 경로: %s", _qdrant_path())


def collection_exists() -> bool:
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    return QDRANT_COLLECTION in existing


def upsert_vector(
    media_id: int,
    vector: list[float],
    payload: dict,
) -> None:
    """벡터와 페이로드를 Qdrant에 upsert한다.

    media_id를 포인트 ID로 사용하므로 동일 ID 재삽입 시 덮어쓰기 (중복 없음).

    Args:
        media_id: SQLite media.id (Qdrant 포인트 ID로 사용)
        vector:   임베딩 벡터 (dim=768)
        payload:  {"media_id", "filepath", "media_type", "thumb_path"}
    """
    client = get_client()
    client.upsert(
        collection_name=QDRANT_COLLECTION,
        points=[
            PointStruct(
                id=media_id,
                vector=vector,
                payload=payload,
            )
        ],
    )


def get_existing_ids(ids: list[int]) -> set[int]:
    """주어진 ID 목록 중 Qdrant 컬렉션에 실제 존재하는 ID 집합을 반환한다.

    Qdrant-SQLite 정합성 검사에서 사용.
    컬렉션이 없으면 빈 집합 반환.
    """
    if not ids:
        return set()
    if not collection_exists():
        return set()
    client = get_client()
    points = client.retrieve(
        collection_name=QDRANT_COLLECTION,
        ids=ids,
        with_payload=False,
        with_vectors=False,
    )
    return {p.id for p in points}


def get_all_point_ids() -> set[int]:
    """Qdrant 컬렉션에 저장된 모든 포인트 ID를 반환한다.

    Qdrant-SQLite 역방향 정합성 검사에서 사용.
    컬렉션이 없으면 빈 집합 반환.
    """
    if not collection_exists():
        return set()
    client = get_client()
    ids: set[int] = set()
    offset = None
    while True:
        result, next_offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            limit=1000,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        for point in result:
            ids.add(point.id)
        if next_offset is None:
            break
        offset = next_offset
    return ids


def delete_points_by_media_ids(media_ids: list[int]) -> None:
    """Qdrant 에서 포인트 삭제. 컬렉션 없거나 id 비어 있으면 noop."""
    if not media_ids:
        return
    if not collection_exists():
        return
    from qdrant_client.models import PointIdsList

    client = get_client()
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=PointIdsList(points=media_ids),
    )


def upsert_vectors_batch(
    items: list[tuple[int, list[float], dict]],
) -> None:
    """복수 벡터를 배치로 upsert한다.

    Args:
        items: [(media_id, vector, payload), ...] 리스트
    """
    if not items:
        return
    client = get_client()
    points = [
        PointStruct(id=media_id, vector=vector, payload=payload)
        for media_id, vector, payload in items
    ]
    client.upsert(collection_name=QDRANT_COLLECTION, points=points)


if __name__ == "__main__":
    init_collection()
    get_client().close()
