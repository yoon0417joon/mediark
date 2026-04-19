"""요청 IP 등 공통 HTTP 헬퍼."""

from __future__ import annotations

from fastapi import Request


def client_ip(request: Request) -> str:
    c = request.client
    return c.host if c else "unknown"
