"""Sprint 15 — 로그인 레이트리밋 429, 초대 코드(회수·다회·무제한), 공개 가입, 모더레이션 라우트."""

from __future__ import annotations

import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="imgsearch_s15_")
os.environ["SQLITE_PATH"] = os.path.join(_TMP, "t.db")
os.environ["QDRANT_PATH"] = os.path.join(_TMP, "qdrant")
os.environ["GALLERY_ROOT"] = os.path.join(_TMP, "gallery")
os.environ.setdefault("API_KEY", "pytest-test-api-key")
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = "admin@sprint15.test"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "bootstrap-admin-pass-123"
os.environ["JWT_SECRET"] = "sprint15-test-jwt-secret-32bytes!!"
os.makedirs(os.environ["GALLERY_ROOT"], exist_ok=True)


class Sprint15AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient
        from server.auth.bootstrap import ensure_bootstrap_admin
        from server.auth.schema import init_auth_schema
        from server.db.sqlite import init_db
        from server.main import app

        init_db()
        init_auth_schema()
        ensure_bootstrap_admin()

        _key = os.environ.get("API_KEY", "")
        cls.client = TestClient(
            app,
            headers={"X-API-Key": _key} if _key else {},
            raise_server_exceptions=False,
        )

    def tearDown(self) -> None:
        from server.rate_limit import clear_rate_limits_for_tests

        clear_rate_limits_for_tests()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.client.close()
        except Exception:
            pass

    def _login_admin(self) -> None:
        r = self.client.post(
            "/auth/login",
            json={
                "email":    "admin@sprint15.test",
                "password": "bootstrap-admin-pass-123",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)

    def test_login_rate_limit_returns_429(self) -> None:
        import server.routes_auth as routes_auth
        from server.rate_limit import clear_rate_limits_for_tests

        prev = routes_auth.LOGIN_RATE_LIMIT
        routes_auth.LOGIN_RATE_LIMIT = 4
        clear_rate_limits_for_tests()
        try:
            for _ in range(4):
                r = self.client.post(
                    "/auth/login",
                    json={"email": "nope@example.com", "password": "wrong"},
                )
                self.assertIn(r.status_code, (401, 429), r.text)
            r5 = self.client.post(
                "/auth/login",
                json={"email": "nope@example.com", "password": "wrong"},
            )
            self.assertEqual(r5.status_code, 429, r5.text)
        finally:
            routes_auth.LOGIN_RATE_LIMIT = prev

    def test_admin_delete_invite_code(self) -> None:
        """QA: admin 초대 코드 발급(POST /admin/invite-codes) → 회수(DELETE …/invite-codes/{code})."""
        self._login_admin()
        cr = self.client.post(
            "/admin/invite-codes",
            json={"role": "viewer", "max_uses": 1},
        )
        self.assertEqual(cr.status_code, 201, cr.text)
        self.assertIn("code", cr.json())
        code = cr.json()["code"]
        de = self.client.delete(f"/admin/invite-codes/{code}")
        self.assertEqual(de.status_code, 200, de.text)
        self.assertEqual(de.json().get("status"), "revoked")

    def test_invite_max_uses_two_registrations(self) -> None:
        self._login_admin()
        cr = self.client.post(
            "/admin/invite-codes",
            json={"role": "viewer", "max_uses": 2},
        )
        self.assertEqual(cr.status_code, 201, cr.text)
        code = cr.json()["code"]

        r1 = self.client.post(
            "/auth/register",
            json={
                "email":       "u1@sprint15.test",
                "password":    "password-12345",
                "invite_code": code,
            },
        )
        self.assertEqual(r1.status_code, 201, r1.text)
        r2 = self.client.post(
            "/auth/register",
            json={
                "email":       "u2@sprint15.test",
                "password":    "password-12345",
                "invite_code": code,
            },
        )
        self.assertEqual(r2.status_code, 201, r2.text)
        r3 = self.client.post(
            "/auth/register",
            json={
                "email":       "u3@sprint15.test",
                "password":    "password-12345",
                "invite_code": code,
            },
        )
        self.assertEqual(r3.status_code, 400, r3.text)

    def test_open_registration_without_invite(self) -> None:
        self._login_admin()
        try:
            pr = self.client.put(
                "/admin/registration-settings",
                json={
                    "open_registration":      True,
                    "open_registration_role": "viewer",
                },
            )
            self.assertEqual(pr.status_code, 200, pr.text)
            r = self.client.post(
                "/auth/register",
                json={
                    "email":    "open@sprint15.test",
                    "password": "password-12345",
                },
            )
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(r.json()["user"]["role"], "viewer")
        finally:
            self._login_admin()
            self.client.put(
                "/admin/registration-settings",
                json={
                    "open_registration":      False,
                    "open_registration_role": "viewer",
                },
            )

    def test_moderation_reports_list_as_admin(self) -> None:
        self._login_admin()
        r = self.client.get("/moderation/reports")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("results", r.json())

    def test_registration_options_public_and_reflects_db(self) -> None:
        r0 = self.client.get("/auth/registration-options")
        self.assertEqual(r0.status_code, 200, r0.text)
        j0 = r0.json()
        self.assertIn("invite_required", j0)
        self.assertIn("open_registration", j0)
        self.assertEqual(j0["invite_required"], not j0["open_registration"])

        self._login_admin()
        self.client.put(
            "/admin/registration-settings",
            json={
                "open_registration":      True,
                "open_registration_role": "viewer",
            },
        )
        r1 = self.client.get("/auth/registration-options")
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertTrue(r1.json().get("open_registration"), r1.text)
        self.assertFalse(r1.json().get("invite_required"), r1.text)

        self.client.put(
            "/admin/registration-settings",
            json={
                "open_registration":      False,
                "open_registration_role": "viewer",
            },
        )

    def test_moderator_only_report_review_reports_ok_media_delete_and_hide_403(self) -> None:
        """QA: moderator 에게 report_review 만 부여 시 신고 목록·처리 가능, media_delete/media_hide/ingest 는 403."""
        from pathlib import Path

        from server.db.sqlite import insert_media

        root = Path(os.environ["GALLERY_ROOT"])
        root.mkdir(parents=True, exist_ok=True)
        media_path = root / "qa_modperm.jpg"
        media_path.write_bytes(b"\xff\xd8\xff\xd9")
        mid = insert_media(str(media_path.resolve()), "image")
        self.assertIsNotNone(mid)

        self._login_admin()
        cr = self.client.post(
            "/admin/invite-codes",
            json={"role": "viewer", "max_uses": 1},
        )
        self.assertEqual(cr.status_code, 201, cr.text)
        code = cr.json()["code"]

        reg = self.client.post(
            "/auth/register",
            json={
                "email":       "modperm@sprint15.test",
                "password":    "password-12345",
                "invite_code": code,
            },
        )
        self.assertEqual(reg.status_code, 201, reg.text)
        uid = int(reg.json()["user"]["id"])

        self._login_admin()
        r_role = self.client.put(
            f"/admin/users/{uid}/role",
            json={"role": "moderator"},
        )
        self.assertEqual(r_role.status_code, 200, r_role.text)
        r_perm = self.client.put(
            f"/admin/users/{uid}/permissions",
            json={"permissions": ["report_review"]},
        )
        self.assertEqual(r_perm.status_code, 200, r_perm.text)

        lg = self.client.post(
            "/auth/login",
            json={
                "email":    "modperm@sprint15.test",
                "password": "password-12345",
            },
        )
        self.assertEqual(lg.status_code, 200, lg.text)

        gl = self.client.get("/moderation/reports")
        self.assertEqual(gl.status_code, 200, gl.text)

        pr = self.client.post(
            "/moderation/reports",
            json={"media_id": mid, "reason": "qa"},
        )
        self.assertEqual(pr.status_code, 201, pr.text)
        rid = int(pr.json()["id"])

        rv = self.client.post(
            f"/moderation/reports/{rid}/review",
            json={"status": "dismissed", "notes": "qa"},
        )
        self.assertEqual(rv.status_code, 200, rv.text)

        d1 = self.client.delete(f"/moderation/media/{mid}")
        self.assertEqual(d1.status_code, 403, d1.text)

        h1 = self.client.post(f"/moderation/media/{mid}/hide")
        self.assertEqual(h1.status_code, 403, h1.text)

        ing = self.client.post("/ingest")
        self.assertEqual(ing.status_code, 403, ing.text)

    def test_admin_deactivate_user_blocks_login(self) -> None:
        """관리자가 PUT /admin/users/{id}/active 로 비활성화하면 해당 계정 로그인 401."""
        self._login_admin()
        cr = self.client.post(
            "/admin/invite-codes",
            json={"role": "viewer", "max_uses": 1},
        )
        self.assertEqual(cr.status_code, 201, cr.text)
        code = cr.json()["code"]
        reg = self.client.post(
            "/auth/register",
            json={
                "email":       "deact@sprint15.test",
                "password":    "password-12345",
                "invite_code": code,
            },
        )
        self.assertEqual(reg.status_code, 201, reg.text)
        uid = int(reg.json()["user"]["id"])

        self._login_admin()
        da = self.client.put(
            f"/admin/users/{uid}/active",
            json={"active": False},
        )
        self.assertEqual(da.status_code, 200, da.text)
        self.assertFalse(da.json().get("is_active"), da.text)

        bad = self.client.post(
            "/auth/login",
            json={"email": "deact@sprint15.test", "password": "password-12345"},
        )
        self.assertEqual(bad.status_code, 401, bad.text)

    def test_admin_cannot_deactivate_last_active_admin(self) -> None:
        self._login_admin()
        from server.auth.users import get_user_by_email

        row = get_user_by_email("admin@sprint15.test")
        self.assertIsNotNone(row)
        aid = int(row["id"])
        r = self.client.put(
            f"/admin/users/{aid}/active",
            json={"active": False},
        )
        self.assertEqual(r.status_code, 400, r.text)


if __name__ == "__main__":
    unittest.main()
