"""Local server for the One Piece Event Binder app.

Serves the page and card images, saves your collection to collection.json, and
(because the app has an in-page editor) lets you edit the card / product / share
data and upload card art. Listens on 127.0.0.1 only (this computer, nobody else).
"""

import hashlib
import hmac
from http.cookies import SimpleCookie
from urllib.parse import urlsplit, unquote
import public_access as public
import base64
import json
import re
import os
import sys
import threading
import webbrowser
import workflow as wf
import recognition
import recognition_budget
import shutil
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SAVE = os.path.join(HERE, "collection.json")
CARDS = os.path.join(HERE, "cards.json")
PRODUCTS = os.path.join(HERE, "products.json")
SHARE = os.path.join(HERE, "aws", "share_data.json")
BINDERS = os.path.join(HERE, "binders.json")
JP_DIR = os.path.join(HERE, "card_images_jp")
MANIFEST = os.path.join(JP_DIR, "manifest.json")
PORT = int(os.environ.get("BINDER_PORT", "8765"))
LOCK = threading.Lock()
EDIT_PASSCODE = os.environ.get("BINDER_EDIT_PASSCODE", "")
COOKIE = "evento_session"


# Per card: {"jp": true, "foil": true, "en": true, "kr": true} - only true values are stored.
#   jp   = you own the Japanese card (this is what counts as "complete")
#   foil = you own the Japanese foil/parallel
#   en / kr = you are holding an English / Korean copy as a placeholder
#   manga = you own the optional Manga (special-art) version - never needed for a card to count as complete
#   ordered = bought (e.g. through a proxy) but not received yet; note = free text about that order
FIELDS = {"jp", "foil", "en", "kr", "manga", "ordered"}
NOTE_MAX = 200
ALT_STATES = {
    "want",
    "ordered",
    "have",
}  # per alt-art / Manga version: collecting it, bought and on the way, in hand

# Data-editing constraints (mirrored by the editor in index.html)
COLOURS = {"Red", "Green", "Blue", "Purple", "Black", "Yellow"}
ALT_TYPES = {"Comic", "Original", "Animation", "Other"}
NUM_RE = re.compile(r"^(?:[A-Z]{2,3}\d{2}|P)-\d{3}$")
ALT_RE = re.compile(r"^p\d{1,2}$")
SET_RE = re.compile(r"^(?:[A-Z]{2,3}-\d{2}|P-\d{3,4})$")
IMG_PATH_RE = re.compile(r"(card_images|card_images_jp)/[A-Za-z0-9_-]+\.(jpg|png)")


def valid(owned):
    return wf.valid_collection(owned)


def valid_card(c):
    if not isinstance(c, dict):
        return False
    if not isinstance(c.get("name"), str) or not c["name"].strip():
        return False
    if not isinstance(c.get("num"), str) or not NUM_RE.match(c["num"]):
        return False
    if not isinstance(c.get("rar"), str):
        return False
    if not isinstance(c.get("set"), str) or not SET_RE.match(c["set"]):
        return False
    if c.get("col") not in COLOURS:
        return False
    if not isinstance(c.get("foil"), bool):
        return False
    if isinstance(c.get("slot"), int) is False and "slot" in c:
        return False
    if "binder" in c and not isinstance(c["binder"], str):
        return False
    if "noen" in c and not isinstance(c["noen"], bool):
        return False
    if "manga" in c and not (isinstance(c["manga"], str) and ALT_RE.match(c["manga"])):
        return False
    for key in ("alt", "altsame", "altstar"):
        if key in c and (
            not isinstance(c[key], list)
            or not all(isinstance(a, str) and ALT_RE.match(a) for a in c[key])
        ):
            return False
    for key in ("altsrc", "alttype", "altdup"):
        if key in c and (
            not isinstance(c[key], dict)
            or not all(
                isinstance(a, str) and isinstance(v, str) for a, v in c[key].items()
            )
        ):
            return False
    return True


def valid_cards(cards):
    if not isinstance(cards, list):
        return False
    nums = set()
    for c in cards:
        if not valid_card(c):
            return False
        if c["num"] in nums:
            return False
        nums.add(c["num"])
    return True


def valid_products(products):
    if not isinstance(products, dict):
        return False
    if not isinstance(products.get("products"), list):
        return False
    codes = set()
    for p in products["products"]:
        if (
            not isinstance(p, dict)
            or not isinstance(p.get("code"), str)
            or p["code"] in codes
        ):
            return False
        codes.add(p["code"])
    src = products.get("sources")
    if not isinstance(src, dict):
        return False
    for num, codes2 in src.items():
        if (
            not isinstance(num, str)
            or not isinstance(codes2, list)
            or not all(isinstance(s, str) for s in codes2)
        ):
            return False
    return True


def valid_share(share):
    if not isinstance(share, dict):
        return False
    for num, v in share.items():
        if not isinstance(num, str) or not isinstance(v, dict):
            return False
        if not isinstance(v.get("jp"), str):
            return False
        if not isinstance(v.get("sid"), int):
            return False
        if not isinstance(v.get("alt"), list) or not all(
            isinstance(a, str) and ALT_RE.match(a) for a in v["alt"]
        ):
            return False
    return True


def valid_binders(binders):
    if not isinstance(binders, list):
        return False
    ids = set()
    for b in binders:
        if (
            not isinstance(b, dict)
            or not isinstance(b.get("id"), str)
            or not b["id"]
            or not isinstance(b.get("name"), str)
        ):
            return False
        if b["id"] in ids:
            return False
        ids.add(b["id"])
    return True


def load():
    try:
        with open(SAVE, encoding="utf-8") as f:
            data = json.load(f)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError("bad collection shape")
        return data
    except FileNotFoundError:
        return {}


def save(owned):
    tmp = SAVE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(owned, f, indent=1, sort_keys=True)
    if os.path.exists(SAVE):
        try:
            shutil.copyfile(SAVE, SAVE + ".bak")
        except OSError:
            pass
    os.replace(tmp, SAVE)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def update_manifest(num):
    manifest = load_json(MANIFEST, [])
    if not isinstance(manifest, list):
        manifest = []
    if num not in manifest:
        manifest.append(num)
        save_json(MANIFEST, manifest)


CATALOG = os.path.join(HERE, "catalog.json")


def load_catalog():
    value = load_json(CATALOG, None)
    return wf.normalize_catalog(
        value
        if value is not None
        else {
            "cards": load_json(CARDS, []),
            "products": load_json(PRODUCTS, {"products": [], "sources": {}}),
            "share": load_json(SHARE, {}),
            "binders": load_json(BINDERS, []),
        }
    )


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def _authed(self):
        if not EDIT_PASSCODE:
            return False
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            value = cookie[COOKIE].value if COOKIE in cookie else ""
            token = hmac.new(
                EDIT_PASSCODE.encode(), b"evento-session-v1", hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(value, token)
        except (ValueError, KeyError):
            return False

    def _require_editor(self):
        allowed = self._authed()
        print(
            json.dumps(
                {
                    "event": "edit_access",
                    "action": "protected_request",
                    "allowed": allowed,
                    "method": self.command,
                }
            )
        )
        if not allowed:
            self._json(401, {"error": "editing access required"})
        return allowed

    def _json(self, code, obj, etag=None, cookie=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if etag:
            self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self, limit):
        n = int(self.headers.get("Content-Length", "0"))
        if n > limit:
            return None, "too large"
        try:
            return json.loads(self.rfile.read(n)), None
        except ValueError:
            return None, "bad json"

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/api/access":
            editor = self._authed()
            return self._json(
                200,
                {
                    "canEdit": editor,
                    "recognitionAvailable": editor and recognition.configured(),
                },
            )
        editor = self._authed()
        legacy = {
            "/cards.json": "cards",
            "/products.json": "products",
            "/aws/share_data.json": "share",
            "/binders.json": "binders",
        }
        if path in ("/api/collection", "/api/catalog") or path in legacy:
            try:
                with LOCK:
                    value = load() if path == "/api/collection" else load_catalog()
                    if not editor:
                        if path in legacy:
                            return self._json(401, {"error": "editing access required"})
                        visible = public.catalog(load_catalog())
                        projection = (
                            public.collection(value, visible)
                            if path == "/api/collection"
                            else visible
                        )
                        return self._json(200, projection, public.revision(projection))
                    return self._json(
                        200,
                        value[legacy[path]] if path in legacy else value,
                        wf.revision(value),
                    )
            except (OSError, ValueError):
                return self._json(503, {"error": "storage unavailable"})
        return super().do_GET()

    def send_head(self):
        path = unquote(urlsplit(self.path).path)
        static = path in ("/", "/index.html", "/workflow.js", "/save.js")
        image = bool(
            re.fullmatch(r"/card_images(?:_jp)?/[A-Za-z0-9_-]+\.(?:jpg|png)", path)
        )
        if path == "/card_images_jp/manifest.json":
            manifest = load_json(MANIFEST, [])
            if not self._authed():
                nums = {c["num"] for c in public.catalog(load_catalog())["cards"]}
                manifest = [n for n in manifest if n in nums]
            self._json(200, manifest)
            return None
        if image and not self._authed():
            image = path.lstrip("/") in public.image_paths(
                public.catalog(load_catalog())
            )
        if not (static or image):
            self.send_error(404)
            return None
        return super().send_head()

    def do_PUT(self):
        if not self._require_editor():
            return
        path = self.path.split("?")[0]
        if path in ("/api/cards", "/api/products", "/api/share-data", "/api/binders"):
            return self._json(
                409, {"error": "Save the canonical /api/catalog document with If-Match"}
            )
        if path not in ("/api/collection", "/api/catalog"):
            return self.send_error(404)
        expected = self.headers.get("If-Match")
        if not expected:
            return self._json(428, {"error": "If-Match required"})
        body, err = self._read_body(
            200_000 if path == "/api/collection" else 10_000_000
        )
        if err:
            return self._json(413 if err == "too large" else 400, {"error": err})
        checker = wf.valid_collection if path == "/api/collection" else wf.valid_catalog
        if not checker(body):
            return self._json(400, {"error": "bad shape"})
        try:
            with LOCK:
                current = load() if path == "/api/collection" else load_catalog()
                if wf.revision(current) != expected:
                    return self._json(409, {"error": "revision conflict"})
                if path == "/api/collection":
                    save(body)
                else:
                    body = wf.normalize_catalog(body)
                    save_json(CATALOG, body)
                return self._json(200, body, wf.revision(body))
        except (OSError, ValueError):
            return self._json(503, {"error": "storage unavailable"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/logout":
            return self._json(
                200,
                {"ok": True},
                cookie=f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict",
            )
        if path == "/login":
            body, error = self._read_body(4096)
            if error or not isinstance(body, dict):
                return self._json(400, {"error": "bad json"})
            allowed = bool(EDIT_PASSCODE) and hmac.compare_digest(
                str(body.get("passcode", "")).encode(), EDIT_PASSCODE.encode()
            )
            print(
                json.dumps(
                    {"event": "edit_access", "action": "login", "allowed": allowed}
                )
            )
            if not allowed:
                return self._json(401, {"error": "wrong passcode"})
            token = hmac.new(
                EDIT_PASSCODE.encode(), b"evento-session-v1", hashlib.sha256
            ).hexdigest()
            return self._json(
                200,
                {"ok": True},
                cookie=f"{COOKIE}={token}; Max-Age=31536000; Path=/; HttpOnly; SameSite=Strict",
            )
        if not self._require_editor():
            return
        if path == "/api/recognize":
            body, error = self._read_body(recognition.MAX_BODY_BYTES)
            if error:
                return self._json(
                    413 if error == "too large" else 400,
                    {
                        "error": (
                            "Card image is too large"
                            if error == "too large"
                            else "Invalid recognition request"
                        )
                    },
                )
            try:
                return self._json(
                    200,
                    recognition.recognize(
                        body,
                        load_catalog(),
                        reserve=lambda: recognition_budget.reserve_local(
                            os.path.join(HERE, recognition_budget.LOCAL_FILE)
                        ),
                    ),
                )
            except recognition.RecognitionError as exc:
                return self._json(exc.status, {"error": str(exc)})
            except OSError:
                return self._json(503, {"error": "Catalog unavailable"})
        if self.path.split("?")[0] != "/api/image":
            return self.send_error(404)
        body, err = self._read_body(10_000_000)
        if err == "too large":
            return self._json(413, {"error": "too large"})
        if err or body is None:
            return self._json(400, {"error": "bad json"})
        rel = str(body.get("path") or "")
        data = body.get("data")
        if not IMG_PATH_RE.fullmatch(rel) or not isinstance(data, str) or not data:
            return self._json(400, {"error": "bad path"})
        try:
            raw = wf.decode_image(rel, data)
        except (ValueError, TypeError):
            return self._json(400, {"error": "bad image"})
        dest = os.path.join(HERE, rel.replace("/", os.sep))
        with LOCK:
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(raw)
                if rel.startswith("card_images_jp/"):
                    m = re.fullmatch(
                        r"((?:[A-Z]{2,3}\d{2}|P)-\d{3})\.png", os.path.basename(rel)
                    )
                    if m:
                        update_manifest(m.group(1))
            except OSError as e:
                return self._json(500, {"error": str(e)})
        return self._json(
            200,
            {
                "saved": rel,
                "path": rel,
                "image": rel + "?v=" + wf.hashlib.sha256(raw).hexdigest()[:16],
            },
        )

    def do_DELETE(self):
        if self._require_editor():
            self._json(404, {"error": "not found"})

    def do_PATCH(self):
        if self._require_editor():
            self._json(404, {"error": "not found"})

    def end_headers(self):
        # Pages and data must always be re-checked so edits show up on refresh;
        # card images never change, so let the browser keep them.
        if not self.path.split("?")[0].lower().endswith((".jpg", ".png")):
            self.send_header("Cache-Control", "private, no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


def main():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Port {PORT} is busy - the binder may already be running. Opening it.")
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return
    url = f"http://127.0.0.1:{PORT}/"
    print(f"One Piece Event Binder running at {url}")
    print(f"Your collection is saved in: {SAVE}")
    print("Close this window to stop it.")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
