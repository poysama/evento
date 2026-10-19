"""AWS Lambda (API Gateway HTTP API) version of the Evento binder server.

Serves the page, card art (from a private S3 bucket) and saves the collection to S3.
Browsing is public. Editing requires either:
  * a passkey (WebAuthn: Face ID / Windows Hello / Touch ID / security key), or
  * the passcode - always available as a fallback, and the gate for adding a passkey from a new device.
A cookie remembers the browser after either.

Env vars: BUCKET (private S3 bucket), PASSCODE (fallback passcode),
          WEBAUTHN_ORIGIN (optional, default https://evento.peonbox.xyz - passkeys are bound to this host).
Bucket layout: data/share.json (friends' wishlist link), images/<CODE>.jpg (English art), images_jp/<CODE>.png + manifest.json (Japanese art), data/collection.json, data/passkeys.json, used/<challenge-id> (1-day lifecycle).
"""

import base64
import copy
import hashlib
import hmac
import json
import os
import html as htmllib
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import workflow as wf
import recognition
import recognition_budget
import recognition_settings
import public_access as public

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

s3 = boto3.client("s3")


def recognition_available():
    try:
        return bool(recognition_settings.load_s3(s3, BUCKET))
    except recognition_settings.SettingsError:
        return False


# signs short-lived direct links to pictures, so the bytes come from S3 and not through this (concurrency-limited) function
s3_sign = boto3.client(
    "s3",
    region_name=os.environ.get("AWS_REGION", "ap-southeast-1"),
    config=Config(signature_version="s3v4"),
)
BUCKET = os.environ["BUCKET"]
PASSCODE = os.environ.get("PASSCODE", "")
ORIGIN = os.environ.get("WEBAUTHN_ORIGIN", "https://evento.peonbox.xyz")
RP_ID = urlparse(ORIGIN).hostname
RP_NAME = "Evento binder"
USER_ID = b"evento-owner"  # single-user app: every passkey belongs to the one owner
HERE = Path(__file__).parent
COOKIE = "evento_session"
TOKEN = hmac.new(PASSCODE.encode(), b"evento-session-v1", hashlib.sha256).hexdigest()
CH_KEY = hmac.new(
    PASSCODE.encode(), b"evento-webauthn-challenge-v1", hashlib.sha256
).digest()
CH_TTL = 180
MAX_PASSKEYS = 10
FIELDS = {
    "jp",
    "foil",
    "en",
    "kr",
    "manga",
    "ordered",
}  # ordered = bought, still on its way
ALT_STATES = {
    "want",
    "ordered",
    "have",
}  # per alt-art / Manga version: collecting it, bought and on the way, in hand
NOTE_MAX = 200  # free-text note per card (where it was ordered from ...)
CARD = r"(?:[A-Z]{2,3}\d{2}|P)-\d{3}"  # OP01-026, EB02-007, ST01-014 ... and promos such as P-057
IMG_RE = re.compile(r"^[A-Za-z0-9_-]+\.(?:png|jpg)$")
JP_IMG_RE = re.compile(r"^[A-Za-z0-9_-]+\.(?:png|jpg)$")
KEYS_KEY = "data/passkeys.json"

LOGIN_HTML = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Event Binder</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%2313202E'/%3E%3Crect x='6.5' y='6' width='14' height='20' rx='2.2' fill='%235B6B7D' transform='rotate(-9 13.5 16)'/%3E%3Crect x='11.5' y='5.5' width='14' height='20' rx='2.2' fill='%23F9FBFD' transform='rotate(5 18.5 15.5)'/%3E%3Ccircle cx='18.8' cy='16' r='5' fill='%231E7F5C'/%3E%3Cpath d='M16.3 16.2l2 2 3.6-4.2' fill='none' stroke='%23fff' stroke-width='1.9' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<style>body{font:16px system-ui,sans-serif;background:#E6ECF2;color:#13202E;display:grid;place-items:center;min-height:100vh;margin:0}
@media(prefers-color-scheme:dark){body{background:#0D141C;color:#E3E9F0}input,button{background:#151E29;color:#E3E9F0;border-color:#2E3B4A}#pk{background:#E3E9F0;color:#0D141C}}
main{display:flex;flex-direction:column;gap:12px;width:min(320px,86vw)}h1{font-size:18px;letter-spacing:.06em;text-transform:uppercase;margin:0 0 4px}
input,button{font:inherit;padding:12px;border:1px solid #B7C3D0;border-radius:8px}button{cursor:pointer;font-weight:600}
#pk{background:#13202E;color:#fff;border-color:#13202E;padding:14px}#f{display:flex;flex-direction:column;gap:10px}
.or{font-size:13px;opacity:.65;text-align:center;margin:2px 0}#e{color:#B3261E;min-height:1.2em;font-size:14px}</style>
<main><h1>JP Event Binder</h1>
<button id=pk type=button hidden>Sign in with a passkey</button><div class=or id=or hidden>or use the passcode</div>
<form id=f><input id=p type=password placeholder=Passcode autocomplete=current-password required><button>Enter</button></form>
<div id=e role=alert></div></main>
<script>
var J={'Content-Type':'application/json'};
f.onsubmit=async function(ev){ev.preventDefault();e.textContent='';
var r=await fetch('/login',{method:'POST',headers:J,body:JSON.stringify({passcode:p.value})});
if(r.ok)location.reload();else e.textContent=r.status===401?'Wrong passcode.':'Something went wrong.'};
if(window.PublicKeyCredential&&PublicKeyCredential.parseRequestOptionsFromJSON){pk.hidden=false;or.hidden=false;
pk.onclick=async function(){e.textContent='';try{
var o=await(await fetch('/webauthn/login/options',{method:'POST'})).json();
var c=await navigator.credentials.get({publicKey:PublicKeyCredential.parseRequestOptionsFromJSON(o.options)});
var r=await fetch('/webauthn/login/verify',{method:'POST',headers:J,body:JSON.stringify({token:o.token,credential:c.toJSON()})});
if(r.ok)location.reload();else e.textContent='That passkey was not accepted.';
}catch(x){e.textContent=x&&x.name==='NotAllowedError'?'Passkey sign-in was cancelled.':'Passkey sign-in failed - use the passcode.'}}}
</script>"""


def resp(
    status,
    body="",
    ctype="text/plain; charset=utf-8",
    extra=None,
    b64=False,
    cookies=None,
):
    out = {
        "statusCode": status,
        "headers": {"Content-Type": ctype, "Cache-Control": "no-store"},
        "body": body,
        "isBase64Encoded": b64,
    }
    if extra:
        out["headers"].update(extra)
    if cookies:
        out["cookies"] = cookies
    return out


def js(status, obj):
    return resp(status, json.dumps(obj), "application/json")


def valid(owned):
    return wf.valid_collection(owned)


def authed(event):
    if not PASSCODE:
        return False
    for c in event.get("cookies") or []:
        name, _, val = c.partition("=")
        if name.strip() == COOKIE and hmac.compare_digest(val.strip(), TOKEN):
            return True
    return False


def session_cookie():
    return (
        f"{COOKIE}={TOKEN}; Max-Age=31536000; Path=/; Secure; HttpOnly; SameSite=Strict"
    )


def body_bytes(event):
    b = event.get("body") or ""
    return base64.b64decode(b) if event.get("isBase64Encoded") else b.encode()


def body_json(event):
    try:
        d = json.loads(body_bytes(event) or b"{}")
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


# ---------------------------------------------------------------- editable data (cards / products / share / binders)
# The UI can edit these. The packaged copies inside this Lambda are the base; edits are stored
# in S3 and take precedence, so a redeploy never wipes them.
EDITABLE = {
    "cards": ("cards.json", "data/cards.json", []),
    "products": (
        "products.json",
        "data/products.json",
        {"products": [], "sources": {}},
    ),
    "share": ("share_data.json", "data/share_data.json", {}),
    "binders": ("binders.json", "data/binders.json", [{"id": "main", "name": "Main"}]),
}
COLOUR_SET = {"Red", "Green", "Blue", "Purple", "Black", "Yellow"}
ALT_RE = re.compile(r"^p\d{1,2}$")
SET_RE = re.compile(r"^(?:[A-Z]{2,3}-\d{2}|P-\d{3,4})$")


def missing_key(exc):
    return exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound")


def read_document(key, default):
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        value = json.loads(obj["Body"].read())
        return default if value is None else value, obj.get("ETag"), True
    except ClientError as exc:
        if missing_key(exc):
            return default, None, False
        raise


def load_catalog_document():
    value, etag, exists = read_document("data/catalog.json", None)
    if exists:
        return wf.normalize_catalog(value), etag, True
    values = {}
    for name, (packaged, key, default) in EDITABLE.items():
        fallback = (
            json.loads((HERE / packaged).read_text(encoding="utf-8"))
            if (HERE / packaged).exists()
            else default
        )
        values[name] = read_document(key, fallback)[0]
    return wf.normalize_catalog(values), None, False


def load_editable(name):
    return load_catalog_document()[0][name]


def document_api(method, event, key, default, checker, catalog=False):
    try:
        value, etag, exists = (
            load_catalog_document() if catalog else read_document(key, default)
        )
        current = etag or wf.revision(value)
        if method == "GET":
            return resp(
                200,
                json.dumps(value, ensure_ascii=False),
                "application/json",
                extra={"ETag": current},
            )
        if method != "PUT":
            return js(404, {"error": "not found"})
        expected = (event.get("headers") or {}).get("if-match") or (
            event.get("headers") or {}
        ).get("If-Match")
        if not expected:
            return js(428, {"error": "If-Match required"})
        if expected != current:
            return js(409, {"error": "revision conflict"})
        raw = body_bytes(event)
        if len(raw) > (10_000_000 if catalog else 200_000):
            return js(413, {"error": "too large"})
        try:
            value = json.loads(raw)
        except ValueError:
            return js(400, {"error": "bad json"})
        if not checker(value):
            return js(400, {"error": "bad shape"})
        if catalog:
            value = wf.normalize_catalog(value)
        condition = {"IfMatch": etag} if exists else {"IfNoneMatch": "*"}
        if exists and not etag:
            raise ValueError("storage omitted revision")
        out = s3.put_object(
            Bucket=BUCKET,
            Key=key,
            Body=json.dumps(value, ensure_ascii=False).encode(),
            ContentType="application/json",
            **condition,
        )
        _CACHE["cat"] = None
        _CACHE["own"] = (0, {})
        return resp(
            200,
            json.dumps(value, ensure_ascii=False),
            "application/json",
            extra={"ETag": out.get("ETag") or wf.revision(value)},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in (
            "PreconditionFailed",
            "ConditionalRequestConflict",
            "412",
            "409",
        ):
            return js(409, {"error": "revision conflict"})
        return js(503, {"error": "storage unavailable"})
    except (OSError, ValueError):
        return js(503, {"error": "storage unavailable"})


def save_editable(name, data):
    s3.put_object(
        Bucket=BUCKET,
        Key=EDITABLE[name][1],
        Body=json.dumps(data, ensure_ascii=False).encode(),
        ContentType="application/json",
    )
    _CACHE["cat"] = (
        None  # edited cards/share_data must show on the share pages without a cold start
    )


def valid_card(c):
    if not isinstance(c, dict):
        return False
    if not isinstance(c.get("name"), str) or not c["name"].strip():
        return False
    if not isinstance(c.get("num"), str) or not re.fullmatch(CARD, c["num"]):
        return False
    if not isinstance(c.get("rar"), str):
        return False
    if not isinstance(c.get("set"), str) or not SET_RE.fullmatch(c["set"]):
        return False
    if c.get("col") not in COLOUR_SET:
        return False
    if not isinstance(c.get("foil"), bool):
        return False
    if "slot" in c and not isinstance(c["slot"], int):
        return False
    if "binder" in c and not isinstance(c["binder"], str):
        return False
    if "noen" in c and not isinstance(c["noen"], bool):
        return False
    if "manga" in c and not (
        isinstance(c["manga"], str) and ALT_RE.fullmatch(c["manga"])
    ):
        return False
    for key in ("alt", "altsame", "altstar"):
        if key in c and (
            not isinstance(c[key], list)
            or not all(isinstance(a, str) and ALT_RE.fullmatch(a) for a in c[key])
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
        if not valid_card(c) or c["num"] in nums:
            return False
        nums.add(c["num"])
    return True


def valid_products(products):
    if not isinstance(products, dict) or not isinstance(products.get("products"), list):
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
    return all(
        isinstance(n, str)
        and isinstance(v, list)
        and all(isinstance(s, str) for s in v)
        for n, v in src.items()
    )


def valid_share(share):
    if not isinstance(share, dict):
        return False
    for num, v in share.items():
        if not isinstance(num, str) or not isinstance(v, dict):
            return False
        if not isinstance(v.get("jp"), str) or not isinstance(v.get("sid"), int):
            return False
        if not isinstance(v.get("alt"), list) or not all(
            isinstance(a, str) and ALT_RE.fullmatch(a) for a in v["alt"]
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


def update_jp_manifest(num):
    try:
        mf = json.loads(
            s3.get_object(Bucket=BUCKET, Key="images_jp/manifest.json")["Body"].read()
        )
    except (ClientError, ValueError):
        mf = []
    if not isinstance(mf, list):
        mf = []
    if num not in mf:
        mf.append(num)
        s3.put_object(
            Bucket=BUCKET,
            Key="images_jp/manifest.json",
            Body=json.dumps(mf).encode(),
            ContentType="application/json",
        )


# ---------------------------------------------------------------- passkeys (WebAuthn)
def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64ud(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def new_challenge(purpose):
    """A random challenge wrapped in a short-lived signed token, so no server-side state is needed."""
    ch = os.urandom(32)
    payload = json.dumps(
        {
            "c": b64u(ch),
            "i": b64u(os.urandom(12)),
            "e": int(time.time()) + CH_TTL,
            "p": purpose,
        },
        separators=(",", ":"),
    ).encode()
    return ch, b64u(payload) + "." + b64u(
        hmac.new(CH_KEY, payload, hashlib.sha256).digest()
    )


def open_challenge(token, purpose):
    """Returns (challenge_bytes, challenge_id) for a genuine, unexpired token of this purpose, else None."""
    try:
        p, sig = str(token).split(".")
        payload = b64ud(p)
        if not hmac.compare_digest(
            b64u(hmac.new(CH_KEY, payload, hashlib.sha256).digest()), sig
        ):
            return None
        d = json.loads(payload)
        if d["p"] != purpose or d["e"] < time.time():
            return None
        return b64ud(d["c"]), d["i"]
    except (ValueError, KeyError, TypeError):
        return None


def challenge_used(cid):
    try:
        s3.head_object(Bucket=BUCKET, Key="used/" + cid)
        return True
    except ClientError:
        return False


def burn_challenge(cid):
    s3.put_object(Bucket=BUCKET, Key="used/" + cid, Body=b"1")


def load_keys():
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=KEYS_KEY)["Body"].read())
    except ClientError:
        return []


def save_keys(keys):
    s3.put_object(
        Bucket=BUCKET,
        Key=KEYS_KEY,
        Body=json.dumps(keys).encode(),
        ContentType="application/json",
    )


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def public_key_list(keys):
    return [
        {
            "id": k["id"],
            "name": k["name"],
            "created": k["created"],
            "used": k.get("used"),
            "synced": bool(k.get("backed_up")),
        }
        for k in keys
    ]


def webauthn_route(method, path, event):
    # imported here so ordinary page/image requests never pay for loading the crypto libraries
    from webauthn import (
        generate_authentication_options,
        generate_registration_options,
        options_to_json,
        verify_authentication_response,
        verify_registration_response,
    )
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    if not PASSCODE:
        return js(503, {"error": "editing unavailable"})
    sub = path[len("/webauthn/") :]
    logged_in = authed(event)

    # --- sign in with a passkey (public) ---
    if sub == "login/options" and method == "POST":
        ch, token = new_challenge("auth")
        opts = generate_authentication_options(
            rp_id=RP_ID,
            challenge=ch,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return js(200, {"options": json.loads(options_to_json(opts)), "token": token})

    if sub == "login/verify" and method == "POST":
        d = body_json(event) or {}
        opened = open_challenge(d.get("token"), "auth")
        cred = d.get("credential")
        if not opened or not isinstance(cred, dict) or challenge_used(opened[1]):
            return js(400, {"error": "bad or expired request"})
        keys = load_keys()
        key = next((k for k in keys if k["id"] == cred.get("id")), None)
        if not key:
            time.sleep(1)
            return js(401, {"error": "unknown passkey"})
        try:
            ok = verify_authentication_response(
                credential=cred,
                expected_challenge=opened[0],
                expected_rp_id=RP_ID,
                expected_origin=ORIGIN,
                credential_public_key=b64ud(key["pk"]),
                credential_current_sign_count=key["n"],
                require_user_verification=True,
            )
        except Exception:
            time.sleep(1)
            return js(401, {"error": "passkey not accepted"})
        key["n"] = ok.new_sign_count
        key["used"] = now_iso()
        save_keys(keys)
        burn_challenge(opened[1])
        return resp(200, "ok", cookies=[session_cookie()])

    # everything below needs a session
    if not logged_in:
        return js(401, {"error": "login required"})

    if sub == "passkeys" and method == "GET":
        return js(200, public_key_list(load_keys()))

    if sub.startswith("passkeys/") and method == "DELETE":
        kid = sub[len("passkeys/") :]
        keys = load_keys()
        rest = [k for k in keys if k["id"] != kid]
        if len(rest) == len(keys):
            return js(404, {"error": "not found"})
        save_keys(rest)
        return js(200, {"removed": kid})

    # --- add a passkey to this account (needs a session, i.e. passcode or an existing passkey) ---
    if sub == "register/options" and method == "POST":
        keys = load_keys()
        ch, token = new_challenge("reg")
        opts = generate_registration_options(
            rp_id=RP_ID,
            rp_name=RP_NAME,
            user_name="owner",
            user_display_name="Owner",
            user_id=USER_ID,
            challenge=ch,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=b64ud(k["id"])) for k in keys
            ],
        )
        return js(200, {"options": json.loads(options_to_json(opts)), "token": token})

    if sub == "register/verify" and method == "POST":
        d = body_json(event) or {}
        opened = open_challenge(d.get("token"), "reg")
        cred = d.get("credential")
        if not opened or not isinstance(cred, dict) or challenge_used(opened[1]):
            return js(400, {"error": "bad or expired request"})
        keys = load_keys()
        if len(keys) >= MAX_PASSKEYS:
            return js(
                400, {"error": f"at most {MAX_PASSKEYS} passkeys - remove one first"}
            )
        try:
            v = verify_registration_response(
                credential=cred,
                expected_challenge=opened[0],
                expected_rp_id=RP_ID,
                expected_origin=ORIGIN,
                require_user_verification=True,
            )
        except Exception:
            return js(400, {"error": "passkey not accepted"})
        kid = b64u(v.credential_id)
        if any(k["id"] == kid for k in keys):
            return js(400, {"error": "that passkey is already registered"})
        name = re.sub(r"\s+", " ", str(d.get("name") or "")).strip()[:40] or "Passkey"
        keys.append(
            {
                "id": kid,
                "pk": b64u(v.credential_public_key),
                "n": v.sign_count,
                "name": name,
                "created": now_iso(),
                "used": None,
                "backed_up": bool(v.credential_backed_up),
            }
        )
        save_keys(keys)
        burn_challenge(opened[1])
        return js(200, {"passkeys": public_key_list(keys)})

    return js(404, {"error": "not found"})


# ---------------------------------------------------------------- share "what I'm looking for" with friends
# A private settings API (login required) plus an unlisted, read-only page at /w/<token>. The page lists only the
# cards still missing. Bandai's servers refuse to have their pictures embedded on other sites (Cross-Origin-Resource-
# Policy), so the pictures are served from the private bucket instead - only through the secret link, and only for
# cards that are on the list. "Show pictures" can be switched off (names, codes and links to Bandai's own list remain).
SHARE_KEY = "data/share.json"
SHARE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
SHARE_IMG_RE = re.compile(r"^(" + CARD + r")(?:_(p\d{1,2}|v_[a-zA-Z0-9_-]+))?\.png$")
BANDAI_LIST = "https://www.onepiece-cardgame.com/cardlist/?search=true&series="
SHARE_KEYS = {"upcoming", "enabled", "foil", "pics", "name", "message", "rotate"}
_CACHE = {"cat": None, "own": (0.0, {})}


def load_share():
    return read_document(SHARE_KEY, None)[0]


def save_share(cfg):
    s3.put_object(
        Bucket=BUCKET,
        Key=SHARE_KEY,
        Body=json.dumps(cfg).encode(),
        ContentType="application/json",
    )


def clean_text(v, limit):
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]+", " ", str(v))).strip()[:limit]


def share_view(cfg):
    cfg = cfg or {}
    tok = cfg.get("token")
    return {
        "enabled": bool(cfg.get("enabled") and tok),
        "foil": bool(cfg.get("foil")),
        "pics": cfg.get("pics", True) is not False,
        "upcoming": bool(cfg.get("upcoming")),
        "name": cfg.get("name", ""),
        "message": cfg.get("message", ""),
        "url": f"{ORIGIN}/w/{tok}" if tok else None,
    }


def share_api(method, event):
    cfg = load_share() or {}
    if method == "GET":
        return js(200, share_view(cfg))
    if method != "PUT":
        return js(404, {"error": "not found"})
    d = body_json(event)
    if d is None or not set(d) <= SHARE_KEYS:
        return js(400, {"error": "bad request"})
    for k in ("enabled", "foil", "pics", "upcoming", "rotate"):
        if k in d and not isinstance(d[k], bool):
            return js(400, {"error": k + " must be true or false"})
    for k in ("name", "message"):
        if k in d and not isinstance(d[k], str):
            return js(400, {"error": k + " must be text"})
    if "name" in d:
        cfg["name"] = clean_text(d["name"], 40)
    if "message" in d:
        cfg["message"] = clean_text(d["message"], 300)
    for k in ("foil", "pics", "upcoming", "enabled"):
        if k in d:
            cfg[k] = d[k]
    if d.get("rotate") or (cfg.get("enabled") and not cfg.get("token")):
        cfg["token"] = secrets.token_urlsafe(16)  # 128 bits: unguessable
    cfg["updated"] = now_iso()
    save_share(cfg)
    return js(200, share_view(cfg))


def share_gate(token):
    """The share settings if this is the live, correct link - otherwise None."""
    cfg = load_share()
    if (
        SHARE_TOKEN_RE.match(token)
        and cfg
        and cfg.get("enabled")
        and cfg.get("token")
        and hmac.compare_digest(token.encode(), str(cfg["token"]).encode())
    ):
        return cfg
    return None


def _catalog():
    if not _CACHE["cat"]:
        _CACHE["cat"] = (load_editable("cards"), load_editable("share"))
    return _CACHE["cat"]


def _owned(max_age):
    return read_document("data/collection.json", {})[0]


def _alt_versions(c, info):
    """All alt-art / Manga picture ids of a card, in Bandai's order (p1, p2 ...)."""
    return list(info.get(c["num"], {}).get("alt") or [])


def _alt_label(c, alts, a):
    """'Manga', or 'Alt art' (numbered when there are several) plus where it came from and a star for the special foil finish -
    the same wording as the app."""
    if c.get("manga") == a:
        return "Manga"
    variant = next((v for v in c.get("variants", []) if v["id"] == a), None)
    if variant and variant.get("kind") == "reprint":
        return "Reprint" + (" · " + variant["source"] if variant.get("source") else "")
    if variant and variant.get("label") not in ("Alt art", "Manga"):
        return variant["label"]
    plain = [x for x in alts if x != c.get("manga")]
    base = "Alt art" + (f" {plain.index(a) + 1}" if len(plain) > 1 else "")
    src = (c.get("altsrc") or {}).get(a)
    return (
        base
        + (f" \u00b7 {src}" if src else "")
        + (" \u2605" if a in (c.get("altstar") or []) else "")
    )


def _alt_note(c, a):
    """Bandai's illustration type, and whether the drawing is the regular card's (a foil / parallel finish of the same art)."""
    kind = {
        "Comic": "Comic art",
        "Original": "Original illustration",
        "Animation": "Animation art",
        "Other": "Other art",
    }.get((c.get("alttype") or {}).get(a), "")
    same = a in (c.get("altsame") or [])
    return " \u00b7 ".join(
        x for x in (kind, "same drawing as the regular card" if same else "") if x
    )


def _wishlist(cfg, max_age=0):
    catalog = copy.deepcopy(load_catalog_document()[0])
    for card in catalog["cards"]:
        status = wf.release_status(card, catalog=catalog)
        for variant in card.get("variants", []):
            variant["status"] = wf.release_status(card, variant, catalog)
        card["status"] = status
    owned = _owned(max_age)
    items = wf.wishlist(
        catalog,
        owned,
        include_upcoming=cfg.get("upcoming") is True,
        include_parallels=bool(cfg.get("foil")),
    )
    cards = [
        c
        for c in catalog["cards"]
        if wf.release_status(c, catalog=catalog) != "draft"
        and (
            wf.release_status(c, catalog=catalog) == "released"
            or cfg.get("upcoming") is True
        )
    ]
    missing = [i["card"] for i in items if i["version"] == "std"]
    alts = [(i["card"], i["version"]) for i in items if i["version"] != "std"]
    on_way = [
        c
        for c in cards
        if owned.get(c["num"], {}).get("ordered")
        and not owned.get(c["num"], {}).get("jp")
    ]
    return cards, catalog["share"], missing, alts, on_way


# ---------------------------------------------------------------- wishlist price estimate (Yuyu-tei)
# Yuyu-tei returns 403 Forbidden to requests from AWS's IP ranges (confirmed - not something to route around), so
# this Lambda cannot fetch prices itself. The total is instead checked from elsewhere from time to time and written
# to this one cached file; a private, logged-in-only view (/api/price) just reads it. Never shown on the public
# share page - no "refresh" is offered anywhere here, since a button that cannot actually refresh would be misleading.
PRICE_KEY = "data/price_cache.json"


def _load_price_cache():
    try:
        d = json.loads(s3.get_object(Bucket=BUCKET, Key=PRICE_KEY)["Body"].read())
        return d if isinstance(d, dict) else None
    except (ClientError, ValueError):
        return None


def _all_alt_wants():
    """Every alt-art / Manga version currently marked "want", regardless of share settings."""
    catalog = load_catalog_document()[0]
    return [
        (item["card"], item["version"])
        for item in wf.wishlist(catalog, _owned(0), include_parallels=True)
        if item["version"] != "std"
    ]


def _price_stale(cache, wants):
    if not cache:
        return False
    desired = {(card["num"], version) for card, version in wants}
    cached = {
        (item.get("num"), item.get("ver"))
        for item in cache.get("items", [])
        if isinstance(item, dict)
    }
    return cache.get("wanted") != len(desired) or cached != desired


_HEAD = {"X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}


def _share_not_found():
    return resp(
        404,
        '<!doctype html><meta charset=utf-8><meta name=robots content="noindex"><title>Not found</title>'
        '<body style="font:16px system-ui;padding:2rem"><h1>This link is not active</h1>'
        "<p>Ask your friend for a fresh one.</p>",
        "text/html; charset=utf-8",
        extra=_HEAD,
    )


def _image_ref(c, alt=None):
    variant = next((v for v in c.get("variants", []) if v["id"] == alt), {})
    return (
        variant.get("image")
        or (c.get("image") if not alt else None)
        or "card_images_jp/" + c["num"] + ("_" + alt if alt else "") + ".png"
    )


def _sign_image(name, ttl):
    ref = name if "/" in name else "card_images_jp/" + name
    if not wf.safe_image(ref):
        raise ValueError("unsafe image")
    rel = ref.split("?")[0]
    key = (
        rel.replace("card_images_jp/", "images_jp/", 1)
        if rel.startswith("card_images_jp/")
        else rel.replace("card_images/", "images/", 1)
    )
    return s3_sign.generate_presigned_url(
        "get_object",
        ExpiresIn=ttl,
        Params={
            "Bucket": BUCKET,
            "Key": key,
            "ResponseContentType": (
                "image/png" if rel.endswith(".png") else "image/jpeg"
            ),
            "ResponseCacheControl": "private, max-age=300",
        },
    )


def share_image(token, name):
    cfg = share_gate(token)
    m = SHARE_IMG_RE.match(name)
    if not cfg or not m or cfg.get("pics", True) is False:
        return js(404, {"error": "not found"})
    num, alt = m.group(1), m.group(2)
    _, info, missing, alt_wants, _ = _wishlist(cfg, max_age=20)
    if alt:
        ok = (num, alt) in {(c["num"], a) for c, a in alt_wants}
    else:
        ok = num in {c["num"] for c in missing}
    if not ok:  # only what is on the list: never reveals what you already own
        return js(404, {"error": "not found"})
    # Hand the browser a 5-minute direct link to the picture in the private bucket. The picture bytes then never pass
    # through this function, so a page full of pictures cannot run into the account's concurrent-run limit.
    card = (
        next(c for c in missing if c["num"] == num)
        if not alt
        else next(c for c, a in alt_wants if c["num"] == num and a == alt)
    )
    url = _sign_image(_image_ref(card, alt), 300)
    return resp(
        302,
        "",
        "text/plain; charset=utf-8",
        extra=dict(_HEAD, **{"Location": url, "Cache-Control": "private, max-age=240"}),
    )


def _e(s):
    return htmllib.escape(str(s), quote=True)


COLOURS = [
    ("Red", "#D6443A"),
    ("Green", "#2E9E5B"),
    ("Blue", "#2F6FDE"),
    ("Purple", "#8E4EC6"),
    ("Black", "#2B2F36"),
    ("Yellow", "#E6B800"),
]


def _placeholders(owned, num):
    """Non-JP copies held for a card that is still missing in JP, e.g. ['EN', 'KR']."""
    e = owned.get(num) or {}
    return [k.upper() for k in ("en", "kr") if e.get(k)]


def _figure(c, info, alt, token, pics, ph=(), price=None):
    """One card. alt = None for the standard card, or an alt-art / Manga id such as 'p2'. token = None means the
    private "/mine" view: the fallback route is the ordinary authenticated image route instead of a share link.
    price (private view only): {'lo': yen, 'oos': bool} for this exact alt-art / Manga version, if checked.
    """
    n, alts = c["num"], list(info.get("alt") or [])
    pid = f"{n}_{alt}" if alt else n
    jp = info.get("jp", "")
    tag = f'<span class="tag">{_e(_alt_label(c, alts, alt))}</span>' if alt else ""
    variant = next((v for v in c.get("variants", []) if v["id"] == alt), None)
    if wf.release_status(c, variant) == "announced":
        tag += '<span class="tag">Upcoming</span>'
    if alt and _alt_note(c, alt):
        tag += f'<span class="jp">{_e(_alt_note(c, alt))}</span>'
    if price:
        tag += (
            f'<span class="pc{" oos" if price["oos"] else ""}">¥{price["lo"]:,}'
            + (" <small>sold out</small>" if price["oos"] else "")
            + "</span>"
        )
    pic = ""
    if pics:
        src = (
            f"/w/{token}/img/{pid}.png" if token else "/" + _image_ref(c, alt)
        )  # the link target and the retry fallback
        # the page itself carries a 1-hour direct link, so the pictures load from S3 without touching the function
        pic = (
            f'<a class="p" href="{_e(src)}" target="_blank" rel="noopener noreferrer"><img src="{_e(_sign_image(_image_ref(c,alt), 3600))}" '
            f'data-s="{_e(src)}" alt="{_e(c["name"])}" width="300" height="420" loading="lazy" decoding="async"></a>'
        )
    link = ""
    if info.get("sid"):
        link = (
            f'<a class="bl" href="{_e(BANDAI_LIST + str(info["sid"]))}#{_e(n)}" target="_blank" '
            f'rel="noopener noreferrer">Bandai card list &rarr;</a>'
        )
    if ph:
        tag += f'<span class="tag ph">Have the {_e(" + ".join(ph))} copy, still need the JP card</span>'
    colour = c.get("col", "")
    return (
        f'<figure class="c" data-c="{_e(colour)}">{pic}<figcaption><b>{_e(c["name"])}</b>'
        + (f'<span class="jp" lang="ja">{_e(jp)}</span>' if jp else "")
        + f'<code>{_e(n)} &middot; {_e(c["rar"])}'
        + (f" &middot; {_e(colour)}" if colour else "")
        + f"</code>{tag}{link}</figcaption></figure>"
    )


def _need(n, total):
    """'need 2 of 3' / 'need all 3' / 'need it' - always says what is MISSING, never reads like what is owned."""
    return (
        "need it"
        if total == 1
        else f"need all {total}" if n == total else f"need {n} of {total}"
    )


def _sections(items, info, prefix, token, pics, totals, owned=None):
    """Sections by set; each pill and heading says how many of the set's cards are still missing."""
    groups = []
    for c in items:
        if not groups or groups[-1][0] != c["set"]:
            groups.append((c["set"], []))
        groups[-1][1].append(c)
    nav = "".join(
        f'<a href="#{prefix}-{_e(s)}">{_e(s)} <i>{_need(len(cs), totals.get(s, len(cs)))}</i></a>'
        for s, cs in groups
    )
    body = "".join(
        f'<section id="{prefix}-{_e(s)}"><h2>{_e(s)} <small>{len(cs)} of {totals.get(s, len(cs))} missing</small></h2>'
        f'<div class="g{"" if pics else " t"}">'
        + "".join(
            _figure(
                c,
                info.get(c["num"], {}),
                None,
                token,
                pics,
                _placeholders(owned or {}, c["num"]),
            )
            for c in cs
        )
        + "</div></section>"
        for s, cs in groups
    )
    return nav, body


def _src_key(label):
    """Natural order for collection names: 'Best Selection Vol.2' before 'Vol.10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", label)]


def _alt_sections(pairs, info, token, pics, prices=None, by="set"):
    """The alt-art / Manga versions I want, by card set (a card can appear more than once, once per version).
    by='src' (private view): grouped by the collection the print came from instead - Best Selection, PRB-01, an
    anniversary set, a tournament prize... - each group in card-number order.
    prices (private view only): {(num, alt): {'lo': yen, 'oos': bool}}."""
    prices = prices or {}
    pre = "f" if by == "set" else "k"
    if by == "src":
        gk = lambda c, a: (c.get("altsrc") or {}).get(a) or "Other"
        pairs = sorted(pairs, key=lambda ca: (_src_key(gk(*ca)), ca[0]["num"], ca[1]))
    else:
        gk = lambda c, a: c["set"]
    groups = []
    for c, a in pairs:
        if not groups or groups[-1][0] != gk(c, a):
            groups.append((gk(c, a), []))
        groups[-1][1].append((c, a))
    nav = "".join(
        f'<a href="#{pre}-{_e(s)}">{_e(s)} <i>{len(ps)} wanted</i></a>'
        for s, ps in groups
    )
    body = "".join(
        f'<section id="{pre}-{_e(s)}"><h2>{_e(s)} <small>{len(ps)} wanted</small></h2>'
        f'<div class="g{"" if pics else " t"}">'
        + "".join(
            _figure(
                c,
                info.get(c["num"], {}),
                a,
                token,
                pics,
                price=prices.get((c["num"], a)),
            )
            for c, a in ps
        )
        + "</div></section>"
        for s, ps in groups
    )
    return nav, body


SHARE_CSS = """:root{--bg:#E6ECF2;--card:#F9FBFD;--ink:#13202E;--mute:#5B6B7D;--line:#B7C3D0;--acc:#1E7F5C;--accbg:#DDF1E7;--amber:#8A5A16;--amberbg:#F6E7C8}
@media(prefers-color-scheme:dark){:root{--bg:#0D141C;--card:#151E29;--ink:#E3E9F0;--mute:#93A2B3;--line:#2E3B4A;--acc:#5ED1A0;--accbg:#12362A;--amber:#E0B16B;--amberbg:#3A2C10}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,'Segoe UI',sans-serif;padding:16px}
main{max-width:1000px;margin:0 auto}h1{font-size:24px;line-height:1.2;margin:8px 0 4px}h2{font-size:18px;margin:26px 0 10px;letter-spacing:.04em}
h2 small{color:var(--mute);font-weight:500}.sub{color:var(--mute);margin:0 0 14px}
.msg{background:var(--accbg);color:var(--acc);border-radius:10px;padding:10px 14px;font-weight:600;margin:12px 0}
.stat{font-size:14px;color:var(--mute)}.stat b{color:var(--ink)}
nav.chips{display:flex;flex-wrap:wrap;gap:6px;margin:14px 0 0}nav.chips a{border:1.5px solid var(--line);border-radius:999px;padding:5px 12px;color:var(--ink);text-decoration:none;font-size:14px;background:var(--card)}
nav.chips i{font-style:normal;color:var(--mute);margin-left:4px}
.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px 10px}.g.t{grid-template-columns:repeat(auto-fill,minmax(230px,1fr))}
.c{margin:0}.c a.p{display:block;border-radius:10px;overflow:hidden;border:1.5px solid var(--line);background:var(--card);aspect-ratio:63/88}
.c img{display:block;width:100%;height:100%;object-fit:cover}
figcaption{display:flex;flex-direction:column;gap:1px;padding-top:6px;font-size:13.5px}figcaption b{line-height:1.25}
.t figcaption{padding:10px 12px;border:1.5px solid var(--line);border-radius:10px;background:var(--card)}
.jp{color:var(--mute);font-size:12.5px}code{font:12px ui-monospace,Consolas,monospace;color:var(--mute)}
.tag{align-self:flex-start;margin-top:3px;background:var(--accbg);color:var(--acc);border-radius:6px;padding:1px 7px;font-size:12px;font-weight:700}
a.bl{margin-top:4px;color:var(--acc);font-size:12.5px;font-weight:600;text-decoration:none}a.bl:hover{text-decoration:underline}
.tag.ph{background:var(--amberbg);color:var(--amber)}
.gb{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:14px 0 0}.gb span{color:var(--mute);font-size:14px}
.gb button{border:1.5px solid var(--line);border-radius:999px;padding:6px 12px;background:var(--card);color:var(--ink);font:inherit;font-size:14px;cursor:pointer}
.gb button[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.pc{align-self:flex-start;margin-top:3px;font-size:12.5px;font-weight:700;color:var(--ink)}
.pc.oos{color:var(--mute);text-decoration:line-through}.pc small{font-weight:500;color:var(--mute);text-decoration:none;margin-left:4px}
.cf{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:14px 0 0;padding:8px 0}
@media(min-width:700px){.cf{position:sticky;top:0;z-index:3;background:var(--bg)}}
.cf[hidden]{display:none}.cf>span:first-child{font-size:13px;color:var(--mute);font-weight:600;margin-right:2px}
.cf button{display:inline-flex;align-items:center;gap:6px;border:1.5px solid var(--line);border-radius:999px;padding:6px 12px;background:var(--card);color:var(--ink);font:inherit;font-size:14px;cursor:pointer}
.cf button i{width:12px;height:12px;border-radius:50%;border:1px solid rgba(128,128,128,.55)}.cf button b{color:var(--mute);font-weight:600}
.cf button[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}.cf button[aria-pressed="true"] b{color:var(--bg)}
.cf .fs{flex-basis:100%;font-size:13px;color:var(--mute)}
[hidden]{display:none!important}
.price{border:1.5px solid var(--line);background:var(--card);border-radius:12px;padding:14px 16px;margin:14px 0 0;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px}
.price .pv{font:700 26px system-ui,sans-serif;letter-spacing:-.01em}
.price .pd{display:flex;flex-direction:column;gap:2px;flex:1 1 200px;min-width:0;font-size:12.5px;color:var(--mute)}
.price .stale{color:var(--acc);font-weight:600}
.done{padding:28px 0;text-align:center;font-size:18px;font-weight:600}
footer{margin:34px 0 8px;color:var(--mute);font-size:12.5px;max-width:70ch}"""


# a picture that fails to load (expired direct link, flaky connection) is retried through the site's own picture route
SHARE_JS = (
    "document.addEventListener('error',function(e){var i=e.target;if(!i||i.tagName!=='IMG'||!i.dataset.s)return;"
    "var n=+i.dataset.r||0;if(n>=3)return;i.dataset.r=n+1;"
    "setTimeout(function(){i.src=i.dataset.s+'?r='+(n+1)},500*(n+1)+Math.random()*1500)},true);"
    # colour filter: toggle one or more colours; sets with nothing left in those colours are hidden
    "(function(){var bar=document.querySelector('.cf');if(!bar)return;bar.hidden=false;var on={},fs=bar.querySelector('.fs'),"
    "q=function(s){return[].slice.call(document.querySelectorAll(s))};"
    "q('nav.chips a i,section h2 small').forEach(function(x){x.dataset.t=x.textContent});"
    "function run(){var names=Object.keys(on),any=names.length>0,shown=0;"
    "q('figure.c').forEach(function(f){f.hidden=any&&!on[f.dataset.c]});"
    "q('section').forEach(function(s){var n=s.querySelectorAll('figure.c:not([hidden])').length,h=s.querySelector('h2 small'),"
    "a=document.querySelector('nav.chips a[href=\"#'+s.id+'\"]');"
    "s.hidden=!n;if(s.id.charAt(0)==='m')shown+=n;if(h)h.textContent=any?n+(s.id.charAt(0)==='m'?' missing':' wanted'):h.dataset.t;"
    "if(a){a.hidden=!n;var i=a.querySelector('i');i.textContent=any?(s.id.charAt(0)==='m'?'need '+n:n+' wanted'):i.dataset.t}});"
    "fs.textContent=any?'Showing '+shown+' missing '+names.join(' + ')+' card'+(shown===1?'':'s')+'. Tap a colour again to clear it.':''}"
    "q('.cf button').forEach(function(b){b.addEventListener('click',function(){var c=b.dataset.c;if(on[c]){delete on[c]}else{on[c]=1}"
    "b.setAttribute('aria-pressed',on[c]?'true':'false');run()})})})();"
)


GROUP_JS = (
    "<script>(function(){var bs=document.querySelectorAll('.gb button');if(!bs.length)return;"
    "function go(g){bs.forEach(function(b){b.setAttribute('aria-pressed',b.dataset.g===g?'true':'false')});"
    "document.getElementById('alt-set').hidden=g!=='set';document.getElementById('alt-src').hidden=g!=='src';"
    "try{localStorage.setItem('minegroup',g)}catch(e){}}"
    "bs.forEach(function(b){b.addEventListener('click',function(){go(b.dataset.g)})});"
    "try{var g=localStorage.getItem('minegroup');if(g==='src')go(g)}catch(e){}})();</script>"
)


def _price_html(cache, wants):
    wanted_n = len(wants)
    if not cache or cache.get("total") is None:
        return ""
    stale = _price_stale(cache, wants)
    sub = f'matched {cache["matched"]} of {cache.get("wanted", wanted_n)}'
    if cache.get("sold_out_count"):
        sub += f' &middot; {cache["sold_out_count"]} sold out (price likely higher now)'
    when = ""
    try:
        when = datetime.fromisoformat(cache["fetched"].replace("Z", "+00:00")).strftime(
            "%d %b %Y"
        )
    except Exception:
        pass
    stale_html = (
        '<span class="stale">the list has changed since this estimate</span>'
        if stale
        else ""
    )
    return (
        f'<div class="price"><div class="pv">¥{cache["total"]:,}</div>'
        f'<div class="pd"><span>Estimated cost (Yuyu-tei) &middot; {sub}</span>'
        f'<span>{"Checked " + when if when else "Not checked yet"} &mdash; not a live price, refreshed by hand from time to time.</span>{stale_html}</div></div>'
    )


def mine_page():
    """The same layout as the public share page (missing cards + alt-art / Manga wanted, with pictures), but
    private: only reachable while logged in, always shows everything regardless of the share link's settings,
    and includes the price estimate. Images use the ordinary authenticated routes, not a share token.
    """
    cards, info, missing, alt_wants, on_way = _wishlist({"foil": True})
    owned = _owned(0)
    holding = sum(1 for c in missing if _placeholders(owned, c["num"]))
    by_col = {}
    for c in missing:
        by_col[c.get("col", "")] = by_col.get(c.get("col", ""), 0) + 1
    colour_bar = (
        '<div class="cf" role="group" aria-label="Filter by colour" hidden><span>Colour</span>'
        + "".join(
            f'<button type="button" data-c="{n}" aria-pressed="false"><i style="background:{hx}"></i>{n} <b>{by_col.get(n, 0)}</b></button>'
            for n, hx in COLOURS
            if by_col.get(n)
        )
        + '<span class="fs" aria-live="polite"></span></div>'
    )

    def per_set(cs):
        t = {}
        for c in cs:
            t[c["set"]] = t.get(c["set"], 0) + 1
        return t

    price_cache = _load_price_cache()
    prices = {
        (it["num"], it["ver"]): {"lo": it["lo"], "oos": it["oos"]}
        for it in (price_cache or {}).get("items") or []
    }
    nav1, body1 = _sections(missing, info, "m", None, True, per_set(cards), owned)
    nav2, body2 = _alt_sections(alt_wants, info, None, True, prices)
    nav3, body3 = _alt_sections(
        alt_wants, info, None, True, prices, by="src"
    )  # the same versions, grouped by the collection they came from
    parts = [
        "<h1>My Japanese Event card wishlist</h1>",
        '<p class="sub">Private - only visible while you are logged in.</p>',
        f'<p class="stat"><b>{len(missing)}</b> of {len(cards)} still looking for'
        + (
            f" &middot; <b>{len(on_way)}</b> more already bought and on the way"
            if on_way
            else ""
        )
        + (
            f' &middot; <b>{len(alt_wants)}</b> alt-art / Manga version{"s" if len(alt_wants) != 1 else ""} wanted'
            if alt_wants
            else ""
        )
        + "</p>",
        '<p class="stat">Each set below says how many of its cards you still need.'
        + (
            f" For <b>{holding}</b> of them you already have the EN or KR copy, so they are tagged &mdash; only the Japanese card is missing."
            if holding
            else ""
        )
        + "</p>",
    ]
    if missing:
        parts += [
            colour_bar,
            f'<nav class="chips" aria-label="Jump to a set">{nav1}</nav>',
            body1,
        ]
    else:
        parts.append(
            '<div class="done">Nothing left to find &mdash; everything you still need is already on its way!</div>'
            if on_way
            else '<div class="done">Nothing missing right now &mdash; the collection is complete!</div>'
        )
    if alt_wants:
        parts += [
            '<h2 style="margin-top:40px">Alt-art / Manga versions you want</h2>',
            _price_html(price_cache, alt_wants),
            '<div class="gb" role="group" aria-label="Group by"><span>Group by</span>'
            '<button type="button" data-g="set" aria-pressed="true">Card set</button>'
            '<button type="button" data-g="src" aria-pressed="false">Collection</button></div>',
            f'<div id="alt-set"><nav class="chips" aria-label="Jump to a set">{nav2}</nav>{body2}</div>',
            f'<div id="alt-src" hidden><nav class="chips" aria-label="Jump to a collection">{nav3}</nav>{body3}</div>',
        ]
    parts.append(
        "<footer>Pictures are the official Japanese card images from Bandai&rsquo;s card list (with their sample watermark). "
        "Not affiliated with Bandai. &copy; Eiichiro Oda / Shueisha / Toei Animation / Bandai.</footer>"
    )
    fav = re.search(r'<link rel="icon"[^>]*>', LOGIN_HTML)
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer">'
        "<title>My wishlist</title>"
        f'{fav.group(0) if fav else ""}<style>{SHARE_CSS}</style></head><body><main>{"".join(parts)}</main>'
        f"<script>{SHARE_JS}</script>{GROUP_JS}</body></html>"
    )
    return resp(200, page, "text/html; charset=utf-8", extra=_HEAD)


def share_page(token):
    cfg = share_gate(token)
    if not cfg:
        return _share_not_found()
    cards, info, missing, alt_wants, on_way = _wishlist(cfg)
    pics = cfg.get("pics", True) is not False
    name, message = cfg.get("name", ""), cfg.get("message", "")

    def per_set(cs):
        t = {}
        for c in cs:
            t[c["set"]] = t.get(c["set"], 0) + 1
        return t

    owned = _owned(0)
    holding = sum(1 for c in missing if _placeholders(owned, c["num"]))
    by_col = {}
    for c in missing:
        by_col[c.get("col", "")] = by_col.get(c.get("col", ""), 0) + 1
    colour_bar = (
        '<div class="cf" role="group" aria-label="Filter by colour" hidden><span>Colour</span>'
        + "".join(
            f'<button type="button" data-c="{n}" aria-pressed="false"><i style="background:{hx}"></i>{n} <b>{by_col.get(n, 0)}</b></button>'
            for n, hx in COLOURS
            if by_col.get(n)
        )
        + '<span class="fs" aria-live="polite"></span></div>'
    )
    nav1, body1 = _sections(missing, info, "m", token, pics, per_set(cards), owned)
    nav2, body2 = _alt_sections(alt_wants, info, token, pics)
    who = f"from {_e(name)}" if name else ""
    title = f"{name + chr(39) + 's' if name else 'My'} Japanese One Piece Event card wishlist"
    desc = f"{len(missing)} Japanese Event cards I'm still looking for"
    parts = [
        "<h1>Japanese Event cards I&rsquo;m looking for</h1>",
        f'<p class="sub">{who}</p>' if who else "",
        f'<div class="msg">{_e(message)}</div>' if message else "",
        f'<p class="stat"><b>{len(missing)}</b> of {len(cards)} still looking for'
        + (
            f" &middot; <b>{len(on_way)}</b> more already bought and on the way"
            if on_way
            else ""
        )
        + (
            f' &middot; <b>{len(alt_wants)}</b> alt-art / Manga version{"s" if len(alt_wants) != 1 else ""} wanted'
            if alt_wants
            else ""
        )
        + "</p>",
        '<p class="stat">Each set below says how many of its cards I still need.'
        + (
            f" For <b>{holding}</b> of them I already have the EN or KR copy, so they are tagged &mdash; only the Japanese card is missing."
            if holding
            else ""
        )
        + "</p>",
    ]
    if missing:
        parts += [
            colour_bar,
            f'<nav class="chips" aria-label="Jump to a set">{nav1}</nav>',
            body1,
        ]
    else:
        parts.append(
            '<div class="done">Nothing left to find &mdash; everything I still need is already on its way!</div>'
            if on_way
            else '<div class="done">Nothing missing right now &mdash; the collection is complete!</div>'
        )
    if alt_wants:
        parts += [
            '<h2 style="margin-top:40px">Also collecting these alt-art / Manga versions</h2>',
            f'<nav class="chips" aria-label="Jump to a set">{nav2}</nav>',
            body2,
        ]
    if pics:
        note = (
            "Pictures are the official Japanese card images from Bandai&rsquo;s card list (with their sample "
            "watermark), shown here only so you can spot the right card. Tap a card for the full-size picture."
        )
    else:
        note = "Pictures are not included here: tap &ldquo;Bandai card list&rdquo; under a card to see it on Bandai&rsquo;s official site."
    parts.append(
        f"<footer>{note} Not affiliated with Bandai. &copy; Eiichiro Oda / Shueisha / Toei Animation / Bandai.</footer>"
    )
    fav = re.search(r'<link rel="icon"[^>]*>', LOGIN_HTML)
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer">'
        f'<title>{_e(title)}</title><meta property="og:title" content="{_e(title)}">'
        f'<meta property="og:description" content="{_e(desc)}"><meta property="og:type" content="website">'
        f'{fav.group(0) if fav else ""}<style>{SHARE_CSS}</style></head><body><main>{"".join(parts)}</main>'
        f"<script>{SHARE_JS}</script></body></html>"
    )
    return resp(
        200,
        page,
        "text/html; charset=utf-8",
        extra=dict(_HEAD, **{"Cache-Control": "private, max-age=60"}),
    )


# ---------------------------------------------------------------- handler
def _handler(event, context):
    http = event["requestContext"]["http"]
    method, path = http["method"], event.get("rawPath", "/")

    if path == "/api/access" and method == "GET":
        editor = authed(event)
        return js(
            200,
            {
                "canEdit": editor,
                "recognitionAvailable": editor and recognition_available(),
            },
        )
    if path == "/logout" and method == "POST":
        print(json.dumps({"event": "edit_access", "action": "logout", "allowed": True}))
        return resp(
            200,
            "ok",
            cookies=[
                f"{COOKIE}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict"
            ],
        )
    if path == "/login" and method == "POST":
        d = body_json(event)
        if not isinstance(d, dict):
            return js(400, {"error": "bad json"})
        if PASSCODE and hmac.compare_digest(
            str(d.get("passcode", "")).encode(), PASSCODE.encode()
        ):
            print(
                json.dumps({"event": "edit_access", "action": "login", "allowed": True})
            )
            return resp(200, "ok", cookies=[session_cookie()])
        print(json.dumps({"event": "edit_access", "action": "login", "allowed": False}))
        time.sleep(1)  # slow down guessing
        return js(401, {"error": "wrong passcode"})

    if path.startswith("/webauthn/"):
        result = webauthn_route(method, path, event)
        print(
            json.dumps(
                {
                    "event": "edit_access",
                    "action": "passkey_request",
                    "allowed": result["statusCode"] < 400,
                    "method": method,
                }
            )
        )
        return result

    if method == "GET" and path.startswith("/w/"):
        tok, _, sub = path[3:].partition("/")
        return (
            share_image(tok, sub[4:])
            if sub.startswith("img/")
            else (share_page(tok) if not sub else _share_not_found())
        )

    editor = authed(event)
    public_static = path in ("/", "/index.html", "/workflow.js", "/save.js")
    public_image = path.startswith(("/card_images/", "/card_images_jp/"))
    public_data = path in ("/api/catalog", "/api/collection")
    if editor and method not in ("GET", "HEAD"):
        print(
            json.dumps(
                {
                    "event": "edit_access",
                    "action": "protected_request",
                    "allowed": True,
                    "method": method,
                }
            )
        )
    if not editor:
        if method != "GET" or not (public_static or public_image or public_data):
            print(
                json.dumps(
                    {
                        "event": "edit_access",
                        "action": "protected_request",
                        "allowed": False,
                        "method": method,
                    }
                )
            )
            return js(401, {"error": "editing access required"})
        if public_data or public_image:
            visible = public.catalog(load_catalog_document()[0])
            if path == "/api/catalog":
                return resp(
                    200,
                    json.dumps(visible),
                    "application/json",
                    extra={"ETag": public.revision(visible)},
                )
            if path == "/api/collection":
                owned, _, _ = read_document("data/collection.json", {})
                projection = public.collection(owned, visible)
                return resp(
                    200,
                    json.dumps(projection),
                    "application/json",
                    extra={"ETag": public.revision(projection)},
                )
            if path == "/card_images_jp/manifest.json":
                manifest, _, _ = read_document("images_jp/manifest.json", [])
                nums = {c["num"] for c in visible["cards"]}
                return js(200, [n for n in manifest if n in nums])
            if path.lstrip("/") not in public.image_paths(visible):
                return js(404, {"error": "not found"})

    if method == "GET" and path in ("/", "/index.html"):
        return resp(
            200,
            (HERE / "index.html").read_text(encoding="utf-8"),
            "text/html; charset=utf-8",
        )
    if method == "GET" and path in ("/workflow.js", "/save.js"):
        return resp(
            200,
            (HERE / path[1:]).read_text(encoding="utf-8"),
            "text/javascript; charset=utf-8",
        )
    if method == "GET" and path == "/mine":
        return mine_page()
    if method == "GET" and path == "/cards.json":
        return resp(
            200,
            json.dumps(load_editable("cards"), ensure_ascii=False),
            "application/json",
        )
    if method == "GET" and path == "/products.json":
        return resp(
            200,
            json.dumps(load_editable("products"), ensure_ascii=False),
            "application/json",
        )
    if method == "GET" and path == "/aws/share_data.json":
        return resp(
            200,
            json.dumps(load_editable("share"), ensure_ascii=False),
            "application/json",
        )
    if method == "GET" and path == "/binders.json":
        return resp(
            200,
            json.dumps(load_editable("binders"), ensure_ascii=False),
            "application/json",
        )

    if method == "GET" and path.startswith("/card_images/"):
        name = path[len("/card_images/") :]
        if not IMG_RE.match(name):
            return js(404, {"error": "not found"})
        try:
            data = s3.get_object(Bucket=BUCKET, Key="images/" + name)["Body"].read()
        except ClientError:
            return js(404, {"error": "not found"})
        return resp(
            200,
            base64.b64encode(data).decode(),
            "image/png" if name.endswith(".png") else "image/jpeg",
            b64=True,
            extra={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    # Japanese card art (images_jp/) and the manifest listing which cards have it
    if method == "GET" and path == "/card_images_jp/manifest.json":
        try:
            return resp(
                200,
                s3.get_object(Bucket=BUCKET, Key="images_jp/manifest.json")["Body"]
                .read()
                .decode(),
                "application/json",
            )
        except ClientError:
            return js(404, {"error": "not found"})
    if method == "GET" and path.startswith("/card_images_jp/"):
        name = path[len("/card_images_jp/") :]
        if not JP_IMG_RE.match(name):
            return js(404, {"error": "not found"})
        try:
            data = s3.get_object(Bucket=BUCKET, Key="images_jp/" + name)["Body"].read()
        except ClientError:
            return js(404, {"error": "not found"})
        return resp(
            200,
            base64.b64encode(data).decode(),
            "image/png" if name.endswith(".png") else "image/jpeg",
            b64=True,
            extra={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    if path == "/api/share":
        return share_api(method, event)

    if path == "/api/price":
        if method != "GET":
            return js(404, {"error": "not found"})
        cache = _load_price_cache()
        wants = _all_alt_wants()
        wanted_now = len(wants)
        out = dict(cache) if cache else {"total": None, "fetched": None}
        out["stale"] = _price_stale(cache, wants)
        out["wanted_now"] = wanted_now
        return js(200, out)

    if path == "/api/collection":
        return document_api(
            method, event, "data/collection.json", {}, wf.valid_collection
        )
    if path == "/api/catalog":
        return document_api(
            method, event, "data/catalog.json", None, wf.valid_catalog, catalog=True
        )
    if path in ("/api/cards", "/api/products", "/api/share-data", "/api/binders"):
        if method == "PUT":
            return js(
                409, {"error": "Save the canonical /api/catalog document with If-Match"}
            )
        name = {
            "/api/cards": "cards",
            "/api/products": "products",
            "/api/share-data": "share",
            "/api/binders": "binders",
        }[path]
        return (
            js(200, load_editable(name))
            if method == "GET"
            else js(404, {"error": "not found"})
        )

    if path == "/api/recognition-settings":
        try:
            if method == "GET":
                return js(
                    200,
                    recognition_settings.public_status(
                        recognition_settings.load_s3(s3, BUCKET)
                    ),
                )
            if method == "PUT":
                if len(event.get("body") or "") > recognition_settings.MAX_BODY_BYTES:
                    return js(413, {"error": "Invalid recognition settings request"})
                token = recognition_settings.token_from_request(body_json(event))
                recognition_settings.save_s3(s3, BUCKET, token)
                return js(200, recognition_settings.public_status(token))
            if method == "DELETE":
                recognition_settings.save_s3(s3, BUCKET, "")
                return js(200, recognition_settings.public_status(""))
            return js(404, {"error": "not found"})
        except recognition_settings.SettingsError as exc:
            return js(exc.status, {"error": str(exc)})

    if path == "/api/recognize" and method == "POST":
        if len(event.get("body") or "") > recognition.MAX_BODY_BYTES:
            return js(413, {"error": "Card image is too large"})
        try:
            return js(
                200,
                recognition.recognize(
                    body_json(event),
                    load_catalog_document()[0],
                    api_key=recognition_settings.load_s3(s3, BUCKET),
                    reserve=lambda: recognition_budget.reserve_s3(s3, BUCKET),
                ),
            )
        except (
            recognition.RecognitionError,
            recognition_settings.SettingsError,
        ) as exc:
            return js(exc.status, {"error": str(exc)})

    if path == "/api/image" and method == "POST":
        d = body_json(event) or {}
        rel = str(d.get("path") or "")
        data = d.get("data")
        if (
            not re.fullmatch(
                r"(card_images|card_images_jp)/[A-Za-z0-9_-]+\.(jpg|png)", rel
            )
            or not isinstance(data, str)
            or not data
        ):
            return js(400, {"error": "bad path"})
        try:
            raw = wf.decode_image(rel, data)
        except ValueError:
            return js(400, {"error": "bad image"})
        prefix = "images_jp/" if rel.startswith("card_images_jp/") else "images/"
        key = prefix + rel.split("/", 1)[1]
        s3.put_object(
            Bucket=BUCKET,
            Key=key,
            Body=raw,
            ContentType="image/png" if rel.endswith(".png") else "image/jpeg",
        )
        m = re.fullmatch(r"((?:[A-Z]{2,3}\d{2}|P)-\d{3})\.png", rel.rsplit("/", 1)[1])
        if rel.startswith("card_images_jp/") and m:
            update_jp_manifest(m.group(1))
        return js(
            200,
            {
                "saved": rel,
                "path": rel,
                "image": rel + "?v=" + hashlib.sha256(raw).hexdigest()[:16],
            },
        )

    return js(404, {"error": "not found"})


def handler(event, context):
    try:
        return _handler(event, context)
    except Exception:
        return js(503, {"error": "storage unavailable"})
