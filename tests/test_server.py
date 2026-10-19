import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cookie = (
            "evento_session="
            + server.hmac.new(
                b"test-edit", b"evento-session-v1", server.hashlib.sha256
            ).hexdigest()
        )
        self.auth_patcher = patch.object(server, "EDIT_PASSCODE", "test-edit")
        self.auth_patcher.start()
        self.patchers = [
            patch.object(server, k, str(root / v))
            for k, v in {
                "SAVE": "collection.json",
                "CATALOG": "catalog.json",
                "CARDS": "cards.json",
                "PRODUCTS": "products.json",
                "SHARE": "share.json",
                "BINDERS": "binders.json",
            }.items()
        ]
        for p in self.patchers:
            p.start()
        self.http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        for p in self.patchers:
            p.stop()
        self.auth_patcher.stop()
        self.tmp.cleanup()

    def request(self, method, path, body=None, etag=None, anonymous=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.http.server_port)
        conn.request(
            method,
            path,
            json.dumps(body) if body is not None else None,
            dict(
                ({"If-Match": etag} if etag else {}),
                **({"Cookie": self.cookie} if not anonymous else {})
            ),
        )
        response = conn.getresponse()
        result = (
            response.status,
            response.getheader("ETag"),
            json.loads(response.read()),
        )
        conn.close()
        return result

    def test_conditional_collection(self):
        status, etag, value = self.request("GET", "/api/collection")
        self.assertEqual((status, value), (200, {}))
        self.assertEqual(self.request("PUT", "/api/collection", {})[0], 428)
        self.assertEqual(
            self.request("PUT", "/api/collection", {"OP01-001": {"jp": True}}, etag)[0],
            200,
        )
        self.assertEqual(self.request("PUT", "/api/collection", {}, etag)[0], 409)
        self.assertTrue(Path(server.SAVE).exists())

    def test_atomic_catalog(self):
        _, etag, catalog = self.request("GET", "/api/catalog")
        self.assertEqual(self.request("PUT", "/api/catalog", catalog, etag)[0], 200)
        before = Path(server.CATALOG).read_bytes()
        catalog["drafts"] = [{}]
        self.assertEqual(self.request("PUT", "/api/catalog", catalog, etag)[0], 400)
        self.assertEqual(Path(server.CATALOG).read_bytes(), before)

    def test_corruption_not_default(self):
        Path(server.SAVE).write_text("bad json")
        self.assertEqual(self.request("GET", "/api/collection")[0], 503)

    def test_public_access_and_write_boundary(self):
        self.assertEqual(
            self.request("GET", "/api/access", anonymous=True)[2], {"canEdit": False}
        )
        self.assertEqual(self.request("GET", "/api/access")[2], {"canEdit": True})
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in (
                "/api/catalog",
                "/api/collection",
                "/api/image",
                "/api/share",
                "/api/cards",
                "/api/products",
                "/api/binders",
                "/api/share-data",
            ):
                self.assertEqual(
                    self.request(method, path, {}, anonymous=True)[0],
                    401,
                    (method, path),
                )
        self.assertTrue(
            self.request("GET", "/api/catalog", anonymous=True)[1].startswith(
                '"public-'
            )
        )
        self.assertTrue(
            self.request("GET", "/api/collection", anonymous=True)[1].startswith(
                '"public-'
            )
        )
        self.assertEqual(
            self.request("POST", "/login", {"passcode": "bad"}, anonymous=True)[0], 401
        )
        self.assertEqual(
            self.request("POST", "/login", {"passcode": "test-edit"}, anonymous=True)[
                0
            ],
            200,
        )
        self.assertEqual(self.request("POST", "/logout", anonymous=True)[0], 200)

    def test_empty_passcode_denies_editing(self):
        with patch.object(server, "EDIT_PASSCODE", ""):
            self.assertEqual(self.request("GET", "/api/access")[2], {"canEdit": False})
            self.assertEqual(
                self.request("POST", "/login", {"passcode": ""}, anonymous=True)[0], 401
            )
            self.assertEqual(self.request("PUT", "/api/collection", {})[0], 401)

    def test_private_projection(self):
        value = {
            "OP01-001": {"jp": True, "note": "private", "history": [{"at": "secret"}]},
            "draft-secret": {"jp": True},
        }
        server.save(value)
        catalog = server.wf.normalize_catalog(
            {
                "cards": [
                    {
                        "num": "OP01-001",
                        "name": "Public",
                        "set": "OP-01",
                        "col": "Red",
                        "rar": "C",
                        "foil": False,
                        "variants": [],
                    }
                ]
            }
        )
        server.save_json(server.CATALOG, catalog)
        self.assertEqual(
            self.request("GET", "/api/collection", anonymous=True)[2],
            {"OP01-001": {"jp": True}},
        )
        self.assertEqual(self.request("GET", "/api/collection")[2], value)

    def test_public_revision_cannot_save_private_document(self):
        _, public_etag, _ = self.request("GET", "/api/collection", anonymous=True)
        self.assertEqual(
            self.request("PUT", "/api/collection", {}, public_etag)[0], 409
        )

    def test_static_allowlist_and_cookie_cache_headers(self):
        for method in ("GET", "HEAD"):
            for path in (
                "/collection.json.bak",
                "/catalog.json.tmp",
                "/aws/passkeys.json",
                "/aws/lambda_function.py",
                "/README.md",
                "/%2e%2e/evento/collection.json",
                "/card_images_jp/private-draft.png",
            ):
                conn = http.client.HTTPConnection("127.0.0.1", self.http.server_port)
                conn.request(method, path)
                response = conn.getresponse()
                self.assertEqual(response.status, 404, path)
                response.read()
                conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", self.http.server_port)
        conn.request("POST", "/login", json.dumps({"passcode": "test-edit"}))
        response = conn.getresponse()
        cookie = response.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Max-Age=31536000", cookie)
        self.assertIn("no-store", response.getheader("Cache-Control"))
        response.read()
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", self.http.server_port)
        conn.request("POST", "/logout", headers={"Cookie": cookie.split(";", 1)[0]})
        response = conn.getresponse()
        self.assertIn("Max-Age=0", response.getheader("Set-Cookie"))
        response.read()
        conn.close()


class AtomicSaveTests(unittest.TestCase):
    def test_replace_failure_preserves_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collection.json"
            path.write_text('{"OP01-001":{"jp":true}}')
            before = path.read_bytes()
            with patch.object(server, "SAVE", str(path)), patch.object(
                server.os, "replace", side_effect=OSError("disk failure")
            ):
                with self.assertRaises(OSError):
                    server.save({})
            self.assertEqual(path.read_bytes(), before)
