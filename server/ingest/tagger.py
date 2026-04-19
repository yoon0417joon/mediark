"""WD 태거 래퍼 — WD 계열 모델로 전체 파일에 태그 추출을 적용한다.

모든 미디어 유형(이미지/GIF/영상)에 적용된다.
- 이미지: tag_image() — 단일 파일 직접 추론
- GIF/영상: tag_frames() — 외부에서 추출된 키프레임 경로 목록을 받아 프레임별 추론 후 합산
"""

from __future__ import annotations
import csv
import logging
import threading
from PIL import Image

from server.config import TAGGER_MODEL, TAGGER_THRESHOLD

logger = logging.getLogger(__name__)

# ── 모듈 수준 싱글턴 (H11: 스레드 세이프 로드) ──────────────────────────────────
_model = None
_transform = None
_tags: list[str] = []
_general_indexes: list[int] = []
_character_indexes: list[int] = []
_all_indexes: list[int] = []  # M5: general+character concat 캐시
_load_lock = threading.Lock()
_infer_lock = threading.Lock()  # 단일 GPU 추론 직렬화


def _load_model() -> None:
    """WD 모델과 태그 목록을 lazy 초기화한다 (H11: double-checked locking)."""
    global _model, _transform, _tags, _general_indexes, _character_indexes, _all_indexes
    if _model is not None:
        return
    with _load_lock:
        if _model is not None:
            return

        import timm
        from timm.data import create_transform, resolve_data_config
        from huggingface_hub import hf_hub_download

        logger.info("[tagger] 모델 로드: %s", TAGGER_MODEL)
        import torch
        loaded = timm.create_model(f"hf-hub:{TAGGER_MODEL}", pretrained=True)
        loaded.eval()

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        loaded = loaded.to(device)
        logger.info("[tagger] 모델을 %s로 이동", device)

        data_config = resolve_data_config(loaded.pretrained_cfg, model=loaded)
        _transform = create_transform(**data_config)

        tags_path = hf_hub_download(TAGGER_MODEL, filename="selected_tags.csv")
        with open(tags_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        def _is_category(r: dict, target: int) -> bool:
            """L8: category 필드가 int / str / whitespace 어느 형태여도 안전하게 비교."""
            raw = r.get("category")
            if raw is None:
                return False
            try:
                return int(str(raw).strip()) == target
            except (TypeError, ValueError):
                return False

        _tags = [row["name"] for row in rows]
        _general_indexes = [i for i, r in enumerate(rows) if _is_category(r, 0)]
        _character_indexes = [i for i, r in enumerate(rows) if _is_category(r, 4)]
        _all_indexes = _general_indexes + _character_indexes  # M5: 1회 concat

        _model = loaded  # 모든 보조 자료구조 준비 후 publish (다른 스레드가 부분 상태 못 봄)

        logger.info(
            "[tagger] 태그 로드 완료: %d개 (general %d, character %d)",
            len(_tags), len(_general_indexes), len(_character_indexes),
        )


def unload_model() -> None:
    """H12: 모델 메모리 해제 — 장시간 유휴 시 호출 가능."""
    global _model, _transform, _tags, _general_indexes, _character_indexes, _all_indexes
    with _load_lock:
        if _model is None:
            return
        try:
            import torch
            _model = None
            _transform = None
            _tags = []
            _general_indexes = []
            _character_indexes = []
            _all_indexes = []
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info("[tagger] 모델 언로드 완료")
        except Exception as e:
            logger.warning("[tagger] 모델 언로드 중 경고: %s", e)


def _run_inference(img: Image.Image) -> list[float]:
    """단일 PIL 이미지에 대해 WD 태거 추론을 실행하고 확률 목록을 반환한다."""
    import torch

    tensor = _transform(img).unsqueeze(0)  # (1, C, H, W)
    device = next(_model.parameters()).device
    tensor = tensor.to(device)
    # H11: 단일 GPU 세션 직렬화 — 동시 호출 시 CUDA 컨텍스트 충돌 방지
    with _infer_lock, torch.no_grad():
        logits = _model(tensor)
        probs = torch.sigmoid(logits).squeeze(0).cpu().tolist()
    return probs


def _probs_to_tags(probs: list[float]) -> dict[str, float]:
    """확률 목록에서 임계값 이상인 general/character 태그를 추출한다."""
    result: dict[str, float] = {}
    for idx in _all_indexes:
        if idx < len(probs) and probs[idx] >= TAGGER_THRESHOLD:
            result[_tags[idx]] = probs[idx]
    return result


def tag_image(filepath: str) -> str:
    """
    단일 이미지 파일에 대해 WD 태그를 추출한다.

    Returns:
        쉼표로 구분된 태그 문자열. 태그가 없거나 실패하면 빈 문자열.
    """
    _load_model()
    try:
        img = Image.open(filepath).convert("RGB")
        probs = _run_inference(img)
    except Exception as e:
        logger.warning("[tagger] 추론 실패 (%s): %s", filepath, e)
        return ""

    tags = _probs_to_tags(probs)
    return ", ".join(tags.keys())


def tag_frames(frame_paths: list[str]) -> str:
    """
    영상 키프레임 목록에 대해 WD 태그를 추출한다.
    각 프레임에서 태그를 추출하고 최대 점수로 합산(중복 제거)한다.

    Returns:
        쉼표로 구분된 태그 문자열. 태그가 없거나 실패하면 빈 문자열.
    """
    if not frame_paths:
        return ""

    _load_model()
    tag_scores: dict[str, float] = {}

    for fpath in frame_paths:
        try:
            img = Image.open(fpath).convert("RGB")
            probs = _run_inference(img)
        except Exception as e:
            logger.warning("[tagger] 프레임 추론 실패 (%s): %s", fpath, e)
            continue

        for tag, score in _probs_to_tags(probs).items():
            tag_scores[tag] = max(tag_scores.get(tag, 0.0), score)

    return ", ".join(tag_scores.keys())


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) < 2:
        logger.error("Usage: python -m server.ingest.tagger <image_path>")
        sys.exit(1)

    path = sys.argv[1]
    result = tag_image(path)
    logger.info("[tagger] 결과: %s", result if result else "(태그 없음)")
