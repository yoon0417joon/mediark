"""pytest 전역: TestClient 의 client.host 가 testclient 이므로 loopback 미들웨어와 정합 (CRITICAL-1)."""

from __future__ import annotations

import os

# server.config 가 import 되기 전에 적용되도록 conftest 로드 순서에 의존한다.
os.environ.setdefault("API_KEY", "pytest-test-api-key")
