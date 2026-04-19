"""OCR 래퍼 — 이미지·영상 프레임에서 텍스트 추출.

OCR_BACKEND 설정에 따라 PaddleOCR (Linux/Windows) 또는 EasyOCR (macOS) 을 사용한다.
GIF 는 파이프라인이 `extract_keyframes` → `run_ocr_on_frames` 경로로 처리한다.
"""

from __future__ import annotations
import logging
import os

import numpy as np
from PIL import Image

from server.config import OCR_LANG, OCR_BACKEND

logger = logging.getLogger(__name__)

_ocr_instance = None


def _get_ocr():
    """OCR 싱글턴 인스턴스를 반환한다 (lazy init).

    OCR_BACKEND == "easyocr"  → EasyOCR (macOS/Apple Silicon)
    OCR_BACKEND == "paddleocr" → PaddleOCR (Linux/Windows)
    """
    global _ocr_instance
    if _ocr_instance is None:
        if OCR_BACKEND == "easyocr":
            import easyocr  # noqa: PLC0415
            # EasyOCR 언어 코드: "ko" (한국어), "en" (영어) 등
            lang_code = "ko" if OCR_LANG == "korean" else OCR_LANG
            _ocr_instance = ("easyocr", easyocr.Reader([lang_code, "en"], gpu=False))
        else:
            from paddleocr import PaddleOCR  # noqa: PLC0415
            _ocr_instance = ("paddleocr", PaddleOCR(use_angle_cls=True, lang=OCR_LANG, show_log=False))
    return _ocr_instance


def _ocr_file(image_path: str) -> str | None:
    """단일 이미지 파일에 OCR을 적용하고 텍스트를 반환한다."""
    backend, ocr = _get_ocr()

    try:
        pil_img = Image.open(image_path).convert("RGB")
        arr: str | np.ndarray = np.array(pil_img)
    except Exception:
        arr = image_path

    if backend == "easyocr":
        result = ocr.readtext(arr)
        if not result:
            return None
        texts = [item[1] for item in result if item[1]]
    else:
        result = ocr.ocr(arr, cls=True)
        if not result or not result[0]:
            return None
        texts = []
        for line in result[0]:
            if line and len(line) >= 2 and line[1] and line[1][0]:
                texts.append(line[1][0])

    combined = " ".join(texts).strip()
    return combined if combined else None


def run_ocr_on_image(image_path: str) -> str | None:
    """이미지 파일에서 OCR 텍스트를 추출한다."""
    return _ocr_file(image_path)


def run_ocr_on_frames(frame_paths: list[str]) -> str | None:
    """영상 키프레임 여러 장에 OCR을 적용하고 결과를 합산한다."""
    all_texts: list[str] = []
    for fp in frame_paths:
        text = _ocr_file(fp)
        if text:
            all_texts.append(text)
    combined = " | ".join(all_texts).strip()
    return combined if combined else None


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) < 2:
        logger.error("Usage: python -m server.ingest.ocr <image_file>")
        sys.exit(1)

    path = sys.argv[1]
    result = run_ocr_on_image(path)
    logger.info("[ocr] 결과: %r", result)
