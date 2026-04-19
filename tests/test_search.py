"""검색 모듈 smoke test — L12: server/search/ 에서 tests/ 로 이관.

현재 `search()` 시그니처(`ocr_q/wd14_q/ram_q/stt_q`)에 맞춘 최소 회귀 방어용.
외부 시드 데이터를 요구하지 않는다 — 현재 DB/Qdrant 상태에서 API 호환성만 확인.

실행:
    .venv\\Scripts\\python.exe -m unittest tests.test_search
    또는 pytest tests/test_search.py
"""

from __future__ import annotations

import os
import tempfile
import unittest

# CRITICAL-2: import server 전에 DB 격리 (test_api / test_qa_regressions 와 동일)
_TMP_ROOT = tempfile.mkdtemp(prefix="imgsearch_test_search_")
os.environ["SQLITE_PATH"] = os.path.join(_TMP_ROOT, "t.db")
os.environ.setdefault("QDRANT_PATH", os.path.join(_TMP_ROOT, "qdrant"))
os.environ.setdefault("GALLERY_ROOT", os.path.join(_TMP_ROOT, "gallery"))
os.makedirs(os.environ["GALLERY_ROOT"], exist_ok=True)

from server.db.sqlite import init_db
from server.search.query import search


class SearchApiShapeTests(unittest.TestCase):
    """search() 의 응답 shape 과 에러 처리를 검증한다 (데이터 의존 없음)."""

    @classmethod
    def setUpClass(cls) -> None:
        init_db()

    def test_empty_query_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            search()
        with self.assertRaises(ValueError):
            search(ocr_q="", wd14_q="", ram_q="", stt_q="")
        with self.assertRaises(ValueError):
            search(ocr_q="   ")

    def test_returns_tuple_of_three(self) -> None:
        try:
            result = search(ocr_q="존재하지않는_쿼리_12345")
        except ValueError:
            self.fail("non-empty query should not raise ValueError")
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)
        results, total, elapsed_ms = result
        self.assertIsInstance(results, list)
        self.assertIsInstance(total, int)
        self.assertIsInstance(elapsed_ms, float)
        self.assertGreaterEqual(total, 0)

    def test_media_type_filter_accepts_known_values(self) -> None:
        for mt in ("image", "gif", "video"):
            results, total, _ = search(ocr_q="x", media_type=mt)
            self.assertIsInstance(results, list)
            self.assertGreaterEqual(total, 0)


if __name__ == "__main__":
    unittest.main()
