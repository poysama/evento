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
        self.tmp.cleanup()

    def request(self, method, path, body=None, etag=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.http.server_port)
        conn.request(
            method,
            path,
            json.dumps(body) if body is not None else None,
            {"If-Match": etag} if etag else {},
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
