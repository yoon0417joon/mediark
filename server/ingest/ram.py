"""RAM++ (Recognize Anything Model++) 태거 래퍼.

Sprint 8B: 일반 객체·장면·속성 태그 추출
xinyu1205/recognize-anything-plus-model 사용
"""

from __future__ import annotations

import logging
import threading

from server.config import RAM_MODEL, RAM_IMAGE_SIZE

logger = logging.getLogger(__name__)

_model = None
_transform = None
_device: str | None = None
_load_lock = threading.Lock()
_infer_lock = threading.Lock()


def _get_model():
    """싱글턴 RAM++ 모델을 반환한다 (H11: double-checked locking)."""
    global _model, _transform, _device
    if _model is not None:
        return _model, _transform, _device
    with _load_lock:
        if _model is not None:
            return _model, _transform, _device

        import torch
        from torchvision import transforms
        from ram.models import ram_plus
        from huggingface_hub import hf_hub_download

        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("[ram] 모델 로드: %s (device=%s)", RAM_MODEL, device)

        model_path = hf_hub_download(
            repo_id=RAM_MODEL,
            filename="ram_plus_swin_large_14m.pth",
        )

        model = ram_plus(pretrained=model_path, image_size=RAM_IMAGE_SIZE, vit="swin_l")
        model.eval()
        model = model.to(device)

        transform = transforms.Compose([
            transforms.Resize((RAM_IMAGE_SIZE, RAM_IMAGE_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ])

        _transform = transform
        _device = device
        _model = model  # 모든 보조 자료 준비 후 publish

        logger.info("[ram] 모델 로드 완료")
        return _model, _transform, _device


def unload_model() -> None:
    """H12: 모델 메모리 해제."""
    global _model, _transform, _device
    with _load_lock:
        if _model is None:
            return
        try:
            import torch
            _model = None
            _transform = None
            _device = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info("[ram] 모델 언로드 완료")
        except Exception as e:
            logger.warning("[ram] 언로드 중 경고: %s", e)


def _parse_tags(result) -> list[str]:
    """RAM++ inference 결과에서 영어 태그 목록을 파싱한다.

    L10: 상위 라이브러리 출력 포맷이 바뀌어도 조용히 깨지지 않도록 구분자 후보를
    순차 시도하고, 인식 실패 시 경고 로그를 남긴다.
    """
    if isinstance(result, (list, tuple)):
        raw = result[0] if result else ""
    else:
        raw = result or ""

    if not raw or not isinstance(raw, str):
        return []

    # RAM++ 2023~현재: " | " 구분. 호환성: ",", " , ", "|" 도 허용.
    for sep in (" | ", " ,", ", ", "|", ","):
        if sep in raw:
            parts = [t.strip() for t in raw.split(sep) if t.strip()]
            if parts:
                return parts

    # 구분자 매칭 실패: 단일 태그 혹은 알 수 없는 포맷
    stripped = raw.strip()
    if stripped:
        logger.warning("[ram] 알 수 없는 태그 포맷, 단일 토큰으로 처리: %r", stripped[:80])
        return [stripped]
    return []


def tag_image(filepath: str) -> str:
    """단일 이미지 파일에서 RAM++ 태그를 추출한다.

    Returns:
        쉼표로 구분된 태그 문자열. 태그 없으면 빈 문자열.
    """
    import torch
    from PIL import Image
    from ram import inference_ram as inference

    model, transform, device = _get_model()

    try:
        image = Image.open(filepath).convert("RGB")
        tensor = transform(image).unsqueeze(0).to(device)
        with _infer_lock, torch.no_grad():
            result = inference(tensor, model)
        tags = _parse_tags(result)
        return ", ".join(tags)
    except Exception as e:
        logger.warning("[ram] tag_image 실패 (%s): %s", filepath, e)
        return ""


def tag_frames(frame_paths: list[str]) -> str:
    """복수 프레임 이미지에서 RAM++ 태그를 추출한다 (GIF/영상용).

    프레임별 태그를 합산하여 유니크 태그 집합을 반환한다.

    Returns:
        쉼표로 구분된 태그 문자열. 태그 없으면 빈 문자열.
    """
    import torch
    from PIL import Image
    from ram import inference_ram as inference

    if not frame_paths:
        return ""

    model, transform, device = _get_model()
    all_tags: set[str] = set()

    for fpath in frame_paths:
        try:
            image = Image.open(fpath).convert("RGB")
            tensor = transform(image).unsqueeze(0).to(device)
            with _infer_lock, torch.no_grad():
                result = inference(tensor, model)
            for tag in _parse_tags(result):
                all_tags.add(tag)
        except Exception as e:
            logger.warning("[ram] tag_frames 실패 (%s): %s", fpath, e)

    return ", ".join(sorted(all_tags))
