"""IP×버킷 슬라이딩 윈도우 레이트리밋 (전역 deque 맵)."""

from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import HTTPException

from server.config import RATE_LIMIT_MAX_KEYS

_rate_lock = threading.Lock()
_rate_hits: dict[tuple[str, str], deque[float]] = {}


def rate_limit_bucket(ip: str, bucket: str, limit: int) -> None:
    now = time.monotonic()
    window = 60.0
    key = (bucket, ip)
    with _rate_lock:
        if len(_rate_hits) > RATE_LIMIT_MAX_KEYS:
            overflow = len(_rate_hits) - RATE_LIMIT_MAX_KEYS + 1000
            for k in list(_rate_hits.keys())[:overflow]:
                _rate_hits.pop(k, None)
        dq = _rate_hits.get(key)
        if dq is None:
            dq = deque()
            _rate_hits[key] = dq
        while dq and dq[0] < now - window:
            dq.popleft()
        if len(dq) >= limit:
            raise HTTPException(status_code=429, detail="요청 빈도 초과")
        dq.append(now)
