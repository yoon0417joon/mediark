"""sentence-transformers 임베딩 래퍼.

paraphrase-multilingual-mpnet-base-v2 모델을 CPU에서 실행한다.
한국어 포함 다국어 쿼리를 지원한다.
"""

from __future__ import annotations
import logging
import threading
from typing import TYPE_CHECKING

from server.config import EMBED_MODEL, MAX_QUERY_LEN

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model: "SentenceTransformer | None" = None
_load_lock = threading.Lock()


def _get_model() -> "SentenceTransformer":
    """싱글턴 SentenceTransformer 모델을 반환한다 (H11: thread-safe)."""
    global _model
    if _model is not None:
        return _model
    with _load_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("[embed] 모델 로드: %s", EMBED_MODEL)
            _model = SentenceTransformer(EMBED_MODEL)
            logger.info("[embed] 모델 로드 완료")
    return _model


def unload_model() -> None:
    """H12: mpnet 모델 메모리 해제."""
    global _model
    with _load_lock:
        if _model is None:
            return
        try:
            import torch
            _model = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info("[embed] 모델 언로드 완료")
        except Exception as e:
            logger.warning("[embed] 언로드 중 경고: %s", e)


def build_combined_text(
    ocr_text: str | None,
    tags: str | None = None,
    ram_tags: str | None = None,
    audio_text: str | None = None,
) -> str:
    """OCR 텍스트, WD 태그, RAM++ 태그, STT 텍스트를 합산 텍스트로 구성한다.

    L9: prefix 는 다국어 mpnet 임베딩의 유사도 계산에 기여하지 않도록 짧은 영어
    라벨("OCR/WD/RAM/STT") 로 통일. 과거 한국어 prefix("WD태그:", "RAM태그:") 는
    임베딩 벡터에 해당 단어가 섞여 들어가는 부작용이 있어 제거.

    Returns:
        "OCR: … | WD: … | RAM: … | STT: …" 형식의 문자열.
        모든 필드가 비어 있으면 빈 문자열을 반환한다.
    """
    parts: list[str] = []
    if ocr_text and ocr_text.strip():
        parts.append(f"OCR: {ocr_text.strip()}")
    if tags and tags.strip():
        parts.append(f"WD: {tags.strip()}")
    if ram_tags and ram_tags.strip():
        parts.append(f"RAM: {ram_tags.strip()}")
    if audio_text and audio_text.strip():
        parts.append(f"STT: {audio_text.strip()}")
    return " | ".join(parts)


def get_embedding(text: str) -> list[float]:
    """단일 텍스트의 벡터 임베딩을 반환한다 (dim=768)."""
    if len(text) > MAX_QUERY_LEN:
        text = text[:MAX_QUERY_LEN]
    model = _get_model()
    vector = model.encode(text, convert_to_numpy=True)
    return vector.tolist()


def get_embeddings(texts: list[str]) -> list[list[float]]:
    """복수 텍스트의 벡터 임베딩을 배치로 반환한다."""
    if not texts:
        return []
    texts = [t[:MAX_QUERY_LEN] if len(t) > MAX_QUERY_LEN else t for t in texts]
    model = _get_model()
    vectors = model.encode(texts, convert_to_numpy=True, batch_size=32)
    return [v.tolist() for v in vectors]
