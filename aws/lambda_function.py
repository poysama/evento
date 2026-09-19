"""AWS Lambda (API Gateway HTTP API) version of the Evento binder server.

Serves the page, card art (from a private S3 bucket) and saves the collection to S3.
Everything sits behind a login. Two ways in:
  * a passkey (WebAuthn: Face ID / Windows Hello / Touch ID / security key), or
  * the passcode - always available as a fallback, and the gate for adding a passkey from a new device.
A cookie remembers the browser after either.

Env vars: BUCKET (private S3 bucket), PASSCODE (fallback passcode),
          WEBAUTHN_ORIGIN (optional, default https://evento.peonbox.xyz - passkeys are bound to this host).
Bucket layout: images/<CODE>.jpg, data/collection.json, data/passkeys.json, used/<challenge-id> (1-day lifecycle).
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

s3 = boto3.client('s3')
BUCKET = os.environ['BUCKET']
PASSCODE = os.environ['PASSCODE']
ORIGIN = os.environ.get('WEBAUTHN_ORIGIN', 'https://evento.peonbox.xyz')
RP_ID = urlparse(ORIGIN).hostname
RP_NAME = 'Evento binder'
USER_ID = b'evento-owner'  # single-user app: every passkey belongs to the one owner
HERE = Path(__file__).parent
COOKIE = 'evento_session'
TOKEN = hmac.new(PASSCODE.encode(), b'evento-session-v1', hashlib.sha256).hexdigest()
CH_KEY = hmac.new(PASSCODE.encode(), b'evento-webauthn-challenge-v1', hashlib.sha256).digest()
CH_TTL = 180
MAX_PASSKEYS = 10
FIELDS = {'jp', 'foil', 'en', 'kr'}
IMG_RE = re.compile(r'^[A-Z]{2,3}\d{2}-\d{3}\.jpg$')
KEYS_KEY = 'data/passkeys.json'

LOGIN_HTML = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Event Binder</title>
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


def resp(status, body='', ctype='text/plain; charset=utf-8', extra=None, b64=False, cookies=None):
    out = {'statusCode': status, 'headers': {'Content-Type': ctype, 'Cache-Control': 'no-store'},
           'body': body, 'isBase64Encoded': b64}
    if extra:
        out['headers'].update(extra)
    if cookies:
        out['cookies'] = cookies
    return out


def js(status, obj):
    return resp(status, json.dumps(obj), 'application/json')


def valid(owned):
    if not isinstance(owned, dict):
        return False
    for k, v in owned.items():
        if not isinstance(k, str) or not isinstance(v, dict) or not v:
            return False
        if not set(v) <= FIELDS or not all(x is True for x in v.values()):
            return False
    return True


def authed(event):
    for c in event.get('cookies') or []:
        name, _, val = c.partition('=')
        if name.strip() == COOKIE and hmac.compare_digest(val.strip(), TOKEN):
            return True
    return False


def session_cookie():
    return f'{COOKIE}={TOKEN}; Max-Age=31536000; Path=/; Secure; HttpOnly; SameSite=Strict'


def body_bytes(event):
    b = event.get('body') or ''
    return base64.b64decode(b) if event.get('isBase64Encoded') else b.encode()


def body_json(event):
    try:
        d = json.loads(body_bytes(event) or b'{}')
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


# ---------------------------------------------------------------- passkeys (WebAuthn)
def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def b64ud(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def new_challenge(purpose):
    """A random challenge wrapped in a short-lived signed token, so no server-side state is needed."""
    ch = os.urandom(32)
    payload = json.dumps({'c': b64u(ch), 'i': b64u(os.urandom(12)), 'e': int(time.time()) + CH_TTL, 'p': purpose},
                         separators=(',', ':')).encode()
    return ch, b64u(payload) + '.' + b64u(hmac.new(CH_KEY, payload, hashlib.sha256).digest())


def open_challenge(token, purpose):
    """Returns (challenge_bytes, challenge_id) for a genuine, unexpired token of this purpose, else None."""
    try:
        p, sig = str(token).split('.')
        payload = b64ud(p)
        if not hmac.compare_digest(b64u(hmac.new(CH_KEY, payload, hashlib.sha256).digest()), sig):
            return None
        d = json.loads(payload)
        if d['p'] != purpose or d['e'] < time.time():
            return None
        return b64ud(d['c']), d['i']
    except (ValueError, KeyError, TypeError):
        return None


def challenge_used(cid):
    try:
        s3.head_object(Bucket=BUCKET, Key='used/' + cid)
        return True
    except ClientError:
        return False


def burn_challenge(cid):
    s3.put_object(Bucket=BUCKET, Key='used/' + cid, Body=b'1')


def load_keys():
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=KEYS_KEY)['Body'].read())
    except ClientError:
        return []


def save_keys(keys):
    s3.put_object(Bucket=BUCKET, Key=KEYS_KEY, Body=json.dumps(keys).encode(), ContentType='application/json')


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def public_key_list(keys):
    return [{'id': k['id'], 'name': k['name'], 'created': k['created'], 'used': k.get('used'),
             'synced': bool(k.get('backed_up'))} for k in keys]


def webauthn_route(method, path, event):
    # imported here so ordinary page/image requests never pay for loading the crypto libraries
    from webauthn import (generate_authentication_options, generate_registration_options, options_to_json,
                          verify_authentication_response, verify_registration_response)
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                          ResidentKeyRequirement, UserVerificationRequirement)

    sub = path[len('/webauthn/'):]
    logged_in = authed(event)

    # --- sign in with a passkey (public) ---
    if sub == 'login/options' and method == 'POST':
        ch, token = new_challenge('auth')
        opts = generate_authentication_options(rp_id=RP_ID, challenge=ch,
                                               user_verification=UserVerificationRequirement.REQUIRED)
        return js(200, {'options': json.loads(options_to_json(opts)), 'token': token})

    if sub == 'login/verify' and method == 'POST':
        d = body_json(event) or {}
        opened = open_challenge(d.get('token'), 'auth')
        cred = d.get('credential')
        if not opened or not isinstance(cred, dict) or challenge_used(opened[1]):
            return js(400, {'error': 'bad or expired request'})
        keys = load_keys()
        key = next((k for k in keys if k['id'] == cred.get('id')), None)
        if not key:
            time.sleep(1)
            return js(401, {'error': 'unknown passkey'})
        try:
            ok = verify_authentication_response(
                credential=cred, expected_challenge=opened[0], expected_rp_id=RP_ID, expected_origin=ORIGIN,
                credential_public_key=b64ud(key['pk']), credential_current_sign_count=key['n'],
                require_user_verification=True)
        except Exception:
            time.sleep(1)
            return js(401, {'error': 'passkey not accepted'})
        key['n'] = ok.new_sign_count
        key['used'] = now_iso()
        save_keys(keys)
        burn_challenge(opened[1])
        return resp(200, 'ok', cookies=[session_cookie()])

    # everything below needs a session
    if not logged_in:
        return js(401, {'error': 'login required'})

    if sub == 'passkeys' and method == 'GET':
        return js(200, public_key_list(load_keys()))

    if sub.startswith('passkeys/') and method == 'DELETE':
        kid = sub[len('passkeys/'):]
        keys = load_keys()
        rest = [k for k in keys if k['id'] != kid]
        if len(rest) == len(keys):
            return js(404, {'error': 'not found'})
        save_keys(rest)
        return js(200, {'removed': kid})

    # --- add a passkey to this account (needs a session, i.e. passcode or an existing passkey) ---
    if sub == 'register/options' and method == 'POST':
        keys = load_keys()
        ch, token = new_challenge('reg')
        opts = generate_registration_options(
            rp_id=RP_ID, rp_name=RP_NAME, user_name='owner', user_display_name='Owner', user_id=USER_ID,
            challenge=ch,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=b64ud(k['id'])) for k in keys])
        return js(200, {'options': json.loads(options_to_json(opts)), 'token': token})

    if sub == 'register/verify' and method == 'POST':
        d = body_json(event) or {}
        opened = open_challenge(d.get('token'), 'reg')
        cred = d.get('credential')
        if not opened or not isinstance(cred, dict) or challenge_used(opened[1]):
            return js(400, {'error': 'bad or expired request'})
        keys = load_keys()
        if len(keys) >= MAX_PASSKEYS:
            return js(400, {'error': f'at most {MAX_PASSKEYS} passkeys - remove one first'})
        try:
            v = verify_registration_response(
                credential=cred, expected_challenge=opened[0], expected_rp_id=RP_ID, expected_origin=ORIGIN,
                require_user_verification=True)
        except Exception:
            return js(400, {'error': 'passkey not accepted'})
        kid = b64u(v.credential_id)
        if any(k['id'] == kid for k in keys):
            return js(400, {'error': 'that passkey is already registered'})
        name = re.sub(r'\s+', ' ', str(d.get('name') or '')).strip()[:40] or 'Passkey'
        keys.append({'id': kid, 'pk': b64u(v.credential_public_key), 'n': v.sign_count, 'name': name,
                     'created': now_iso(), 'used': None, 'backed_up': bool(v.credential_backed_up)})
        save_keys(keys)
        burn_challenge(opened[1])
        return js(200, {'passkeys': public_key_list(keys)})

    return js(404, {'error': 'not found'})


# ---------------------------------------------------------------- handler
def handler(event, context):
    http = event['requestContext']['http']
    method, path = http['method'], event.get('rawPath', '/')

    if path == '/login' and method == 'POST':
        d = body_json(event)
        if d is None:
            return js(400, {'error': 'bad json'})
        if hmac.compare_digest(str(d.get('passcode', '')).encode(), PASSCODE.encode()):
            return resp(200, 'ok', cookies=[session_cookie()])
        time.sleep(1)  # slow down guessing
        return js(401, {'error': 'wrong passcode'})

    if path.startswith('/webauthn/'):
        return webauthn_route(method, path, event)

    if not authed(event):
        if method == 'GET' and path in ('/', '/index.html'):
            return resp(200, LOGIN_HTML, 'text/html; charset=utf-8')
        return js(401, {'error': 'login required'})

    if method == 'GET' and path in ('/', '/index.html'):
        return resp(200, (HERE / 'index.html').read_text(encoding='utf-8'), 'text/html; charset=utf-8')
    if method == 'GET' and path == '/cards.json':
        return resp(200, (HERE / 'cards.json').read_text(encoding='utf-8'), 'application/json')

    if method == 'GET' and path.startswith('/card_images/'):
        name = path[len('/card_images/'):]
        if not IMG_RE.match(name):
            return js(404, {'error': 'not found'})
        try:
            data = s3.get_object(Bucket=BUCKET, Key='images/' + name)['Body'].read()
        except ClientError:
            return js(404, {'error': 'not found'})
        return resp(200, base64.b64encode(data).decode(), 'image/jpeg', b64=True,
                    extra={'Cache-Control': 'private, max-age=31536000, immutable'})

    if path == '/api/collection':
        if method == 'GET':
            try:
                data = s3.get_object(Bucket=BUCKET, Key='data/collection.json')['Body'].read()
                return resp(200, data.decode(), 'application/json')
            except ClientError:
                return js(200, {})
        if method == 'PUT':
            raw = body_bytes(event)
            if len(raw) > 200_000:
                return js(413, {'error': 'too large'})
            try:
                owned = json.loads(raw)
            except ValueError:
                return js(400, {'error': 'bad json'})
            if not valid(owned):
                return js(400, {'error': 'bad shape'})
            s3.put_object(Bucket=BUCKET, Key='data/collection.json',
                          Body=json.dumps(owned, sort_keys=True).encode(), ContentType='application/json')
            return js(200, {'saved': len(owned)})

    return js(404, {'error': 'not found'})
