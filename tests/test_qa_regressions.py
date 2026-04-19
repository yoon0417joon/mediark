"""QA_REPORT.md 에서 발견된 버그들의 회귀 방어 테스트.

각 테스트는 해당 QA 항목에 매핑된다.
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path

# ── 모듈 로드 전에 env 를 한 번만 설정 — server.config 는 import 시 고정된다 ──
_TMP_ROOT = tempfile.mkdtemp(prefix="imgsearch_qa_")
os.environ["SQLITE_PATH"] = os.path.join(_TMP_ROOT, "t.db")
os.environ["QDRANT_PATH"] = os.path.join(_TMP_ROOT, "qdrant")
os.environ["GALLERY_ROOT"] = os.path.join(_TMP_ROOT, "gallery")
os.environ.setdefault("API_KEY", "pytest-test-api-key")
os.makedirs(os.environ["GALLERY_ROOT"], exist_ok=True)


def _setup_env() -> str:
    return _TMP_ROOT


class QA1_PathTraversalTest(unittest.TestCase):
    """QA#1 CRITICAL: /upload 의 filename path traversal 방어."""

    @classmethod
    def setUpClass(cls) -> None:
        _setup_env()
        from server.db.sqlite import init_db
        init_db()
        from fastapi.testclient import TestClient
        from server.main import app
        _key = os.environ.get("API_KEY", "")
        cls.client = TestClient(
            app,
            headers={"X-API-Key": _key} if _key else {},
            raise_server_exceptions=False,
        )

    def _tiny_jpeg(self) -> bytes:
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (4, 4), (200, 50, 50)).save(buf, "JPEG")
        return buf.getvalue()

    def test_dotdot_filename_saved_as_basename_only(self) -> None:
        body = self._tiny_jpeg()
        resp = self.client.post(
            "/upload",
            files={"file": ("../qa1_escape.jpg", body, "image/jpeg")},
        )
        self.assertEqual(resp.status_code, 202, resp.text)
        j = resp.json()
        # basename 으로 강제되어 경로 구분자는 없어야 한다
        self.assertNotIn("/", j["filename"])
        self.assertNotIn("\\", j["filename"])
        self.assertNotIn("..", j["filename"])

        # 실제 파일이 GALLERY_ROOT 하위에만 있어야 한다
        gallery_root = Path(os.environ["GALLERY_ROOT"]).resolve()
        outside = gallery_root.parent / "qa1_escape.jpg"
        self.assertFalse(outside.exists(), "갤러리 밖으로 파일이 저장됨")
        self.assertTrue((gallery_root / j["filename"]).exists())

    def test_windows_backslash_path_rejected(self) -> None:
        body = self._tiny_jpeg()
        resp = self.client.post(
            "/upload",
            files={"file": (r"..\\..\\qa1_evil.jpg", body, "image/jpeg")},
        )
        self.assertEqual(resp.status_code, 202, resp.text)
        j = resp.json()
        self.assertNotIn("\\", j["filename"])
        self.assertNotIn("/", j["filename"])
        self.assertNotIn("..", j["filename"])


class QA4_EmptyTextTerminalStateTest(unittest.TestCase):
    """QA#4: empty_text 항목이 terminal state 로 종료되어 재시도되지 않는다."""

    @classmethod
    def setUpClass(cls) -> None:
        _setup_env()
        from server.db.sqlite import init_db
        init_db()

    def test_empty_text_marks_index_error_and_excluded_from_retry(self) -> None:
        from server.db.sqlite import (
            connect,
            get_unembedded_media,
            insert_media,
            update_thumb_path,
        )
        from server.ingest.pipeline import run_embed_pipeline

        # 빈 텍스트 행 준비 (OS 무관 절대 경로 — HIGH-9)
        fake_abs = str(Path(os.environ["GALLERY_ROOT"]) / "imgsearch_qa_fake_thumbonly.jpg")
        fake_thumb = str(Path(os.environ["GALLERY_ROOT"]) / "nonexistent_thumb.jpg")
        mid = insert_media(fake_abs, "image")
        self.assertIsNotNone(mid)
        update_thumb_path(mid, fake_thumb)

        # 1차 실행 — empty_text 로 terminal state 기록
        run_embed_pipeline()

        with connect() as conn:
            row = conn.execute(
                "SELECT index_error, indexed_at FROM media WHERE id = ?", (mid,)
            ).fetchone()
        self.assertEqual(row["index_error"], "empty_text")
        self.assertIsNone(row["indexed_at"])

        # 2차 호출 시 pending 큐에 더 이상 포함되지 않아야 한다
        pending_ids = [r["id"] for r in get_unembedded_media()]
        self.assertNotIn(mid, pending_ids)


class QA7_ConcurrentSameNameUploadTest(unittest.TestCase):
    """QA 동시성: 동일 파일명 동시 업로드 시 파일 소실/DB 불일치가 없어야 한다."""

    @classmethod
    def setUpClass(cls) -> None:
        _setup_env()
        from server.db.sqlite import init_db
        init_db()
        from fastapi.testclient import TestClient
        from server.main import app
        _key = os.environ.get("API_KEY", "")
        cls.client = TestClient(
            app,
            headers={"X-API-Key": _key} if _key else {},
            raise_server_exceptions=False,
        )

    def _tiny_jpeg(self, color: tuple) -> bytes:
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (4, 4), color).save(buf, "JPEG")
        return buf.getvalue()

    def test_two_concurrent_same_name_uploads_both_persisted(self) -> None:
        results: list[dict] = []
        lock = threading.Lock()

        def do_upload(color: tuple) -> None:
            body = self._tiny_jpeg(color)
            r = self.client.post(
                "/upload",
                files={"file": ("qa7_race.jpg", body, "image/jpeg")},
            )
            with lock:
                results.append({"status": r.status_code, "body": r.json() if r.status_code < 500 else r.text})

        t1 = threading.Thread(target=do_upload, args=((10, 200, 30),))
        t2 = threading.Thread(target=do_upload, args=((200, 30, 10),))
        t1.start(); t2.start()
        t1.join(); t2.join()

        # 두 요청 모두 202 여야 한다 — 하나가 버저닝(_v2) 되어 저장됨
        statuses = [r["status"] for r in results]
        self.assertEqual(sorted(statuses), [202, 202], f"statuses={statuses} results={results}")

        filenames = sorted([r["body"]["filename"] for r in results])
        self.assertEqual(filenames[0], "qa7_race.jpg")
        self.assertTrue(filenames[1].startswith("qa7_race_v"))

        # 두 파일 모두 디스크에 존재해야 한다 (A 삭제/B 실패 regression 방어)
        gallery_root = Path(os.environ["GALLERY_ROOT"]).resolve()
        for name in filenames:
            self.assertTrue((gallery_root / name).exists(), f"파일 소실: {name}")


if __name__ == "__main__":
    unittest.main()
