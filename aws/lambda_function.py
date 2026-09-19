"""AWS Lambda (Function URL) version of the Evento binder server.

Serves the page, card art (from a private S3 bucket) and saves the collection to S3.
Everything sits behind a single passcode: log in once per device, a cookie remembers you.

Env vars: BUCKET (private S3 bucket), PASSCODE (the login passcode).
Bucket layout: images/<CODE>.jpg  and  data/collection.json
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

s3 = boto3.client('s3')
BUCKET = os.environ['BUCKET']
PASSCODE = os.environ['PASSCODE']
HERE = Path(__file__).parent
COOKIE = 'evento_session'
TOKEN = hmac.new(PASSCODE.encode(), b'evento-session-v1', hashlib.sha256).hexdigest()
FIELDS = {'jp', 'foil', 'en', 'kr'}
IMG_RE = re.compile(r'^[A-Z]{2,3}\d{2}-\d{3}\.jpg$')

LOGIN_HTML = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Event Binder</title>
<style>body{font:16px system-ui,sans-serif;background:#E6ECF2;color:#13202E;display:grid;place-items:center;min-height:100vh;margin:0}
@media(prefers-color-scheme:dark){body{background:#0D141C;color:#E3E9F0}input,button{background:#151E29;color:#E3E9F0;border-color:#2E3B4A}}
form{display:flex;flex-direction:column;gap:10px;width:min(320px,86vw)}h1{font-size:18px;letter-spacing:.06em;text-transform:uppercase;margin:0 0 4px}
input,button{font:inherit;padding:12px;border:1px solid #B7C3D0;border-radius:8px}button{cursor:pointer;font-weight:600}#e{color:#B3261E;min-height:1.2em;font-size:14px}</style>
<form id=f><h1>JP Event Binder</h1><input id=p type=password placeholder=Passcode autocomplete=current-password autofocus required>
<button>Enter</button><div id=e role=alert></div></form>
<script>f.onsubmit=async function(ev){ev.preventDefault();e.textContent='';
var r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({passcode:p.value})});
if(r.ok)location.reload();else e.textContent=r.status===401?'Wrong passcode.':'Something went wrong.'}</script>"""


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


def body_bytes(event):
    b = event.get('body') or ''
    return base64.b64decode(b) if event.get('isBase64Encoded') else b.encode()


def handler(event, context):
    http = event['requestContext']['http']
    method, path = http['method'], event.get('rawPath', '/')

    if path == '/login' and method == 'POST':
        try:
            given = str(json.loads(body_bytes(event)).get('passcode', ''))
        except ValueError:
            return js(400, {'error': 'bad json'})
        if hmac.compare_digest(given.encode(), PASSCODE.encode()):
            ck = f'{COOKIE}={TOKEN}; Max-Age=31536000; Path=/; Secure; HttpOnly; SameSite=Strict'
            return resp(200, 'ok', cookies=[ck])
        time.sleep(1)  # slow down guessing
        return js(401, {'error': 'wrong passcode'})

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
