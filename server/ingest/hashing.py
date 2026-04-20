"""SHA-256 파일 해시 계산 유틸리티 — Sprint 14A."""

from __future__ import annotations

import hashlib
from pathlib import Path


def compute_sha256(filepath: str | Path) -> str:
    """파일 전체를 스트리밍 읽기로 SHA-256 해시를 계산한다."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
