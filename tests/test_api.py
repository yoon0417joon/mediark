"""API smoke test (unittest 호환) — L12: server/ 에서 tests/ 로 이관.

fastapi TestClient 를 사용해 주요 엔드포인트 응답 형태만 검증한다.

- import 시 top-level 부작용 없음 → `unittest discover` 안전
- Qdrant 로컬 파일 모드는 단일 프로세스 전용 — uvicorn 서버 구동 중이면 skip

실행:
    .venv\\Scripts\\python.exe -m unittest tests.test_api
    또는 pytest tests/test_api.py
"""

from __future__ import annotations

import os
import tempfile
import unittest

# unittest 직접 실행 시 conftest 미로드 — API_KEY·헤더 정합 (CRITICAL-1)
os.environ.setdefault("API_KEY", "pytest-test-api-key")


class ApiSmokeTests(unittest.TestCase):
    """서버 프로세스 없이 TestClient 로 응답 shape 만 확인."""

    @classmethod
    def setUpClass(cls) -> None:
        # Qdrant 락 충돌을 피하기 위해 임시 경로로 오버라이드
        cls._tmp_root = tempfile.mkdtemp(prefix="imgsearch_test_")
        os.environ.setdefault("SQLITE_PATH", os.path.join(cls._tmp_root, "t.db"))
        os.environ.setdefault("QDRANT_PATH", os.path.join(cls._tmp_root, "qdrant"))
        os.environ.setdefault("GALLERY_ROOT", os.path.join(cls._tmp_root, "gallery"))
        os.makedirs(os.environ["GALLERY_ROOT"], exist_ok=True)

        try:
            from fastapi.testclient import TestClient
            from server.db.sqlite import init_db
            from server.main import app
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"TestClient 초기화 실패: {e}")

        # startup event 가 lifespan 컨텍스트 없이는 호출되지 않으므로 DB는 명시 초기화
        init_db()
        _key = os.environ.get("API_KEY", "")
        cls.client = TestClient(
            app,
            headers={"X-API-Key": _key} if _key else {},
            raise_server_exceptions=False,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.client.close()
        except Exception:
            pass

    def test_search_requires_query(self) -> None:
        resp = self.client.get("/search")
        self.assertEqual(resp.status_code, 400)

    def test_search_returns_envelope(self) -> None:
        resp = self.client.get("/search", params={"ocr_q": "테스트"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ("results", "count", "total", "page", "per_page", "total_pages", "elapsed_ms"):
            self.assertIn(key, body)
        self.assertIsInstance(body["results"], list)

    def test_media_not_found(self) -> None:
        resp = self.client.get("/media/9999999")
        self.assertEqual(resp.status_code, 404)

    def test_thumb_not_found(self) -> None:
        resp = self.client.get("/thumb/9999999")
        self.assertEqual(resp.status_code, 404)

    def test_status_shape(self) -> None:
        resp = self.client.get("/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ("running", "total", "completed", "pending"):
            self.assertIn(key, body)

    def test_upload_rejects_path_traversal(self) -> None:
        payload = (
            "../escape.jpg",
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00",
            "image/jpeg",
        )
        resp = self.client.post("/upload", files={"file": payload})
        # basename 으로 강제 저장되어야 하며, 어떤 경우에도 500 이 아니어야 한다
        self.assertNotEqual(resp.status_code, 500)
        if resp.status_code == 202:
            body = resp.json()
            # 저장된 파일명에 경로 구분자 포함 금지
            self.assertNotIn("/", body["filename"])
            self.assertNotIn("\\", body["filename"])
            self.assertNotIn("..", body["filename"])

    def test_upload_rejects_empty_file(self) -> None:
        resp = self.client.post(
            "/upload",
            files={"file": ("empty.jpg", b"", "image/jpeg")},
        )
        self.assertEqual(resp.status_code, 400)

    def test_upload_rejects_unknown_extension(self) -> None:
        resp = self.client.post(
            "/upload",
            files={"file": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")},
        )
        self.assertEqual(resp.status_code, 400)

    def test_upload_status_not_found(self) -> None:
        resp = self.client.get("/upload/status/9999999")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
