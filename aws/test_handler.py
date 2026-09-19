"""Offline tests for the Lambda handler (fake S3, no AWS needed). Run: python aws/test_handler.py

Packages the handler the same way the deploy does (handler + index.html + cards.json in one
folder), so it also catches a broken/missing page or card list before anything is deployed.
"""
import base64
import importlib
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
pkg = Path(tempfile.mkdtemp())
for src in (REPO / 'aws' / 'lambda_function.py', REPO / 'index.html', REPO / 'cards.json'):
    shutil.copy(src, pkg)

os.environ.update(BUCKET='test-bucket', PASSCODE='test-passcode')
store = {}


class ClientError(Exception):
    pass


class FakeBody:
    def __init__(self, d):
        self.d = d

    def read(self):
        return self.d


class FakeS3:
    def get_object(self, Bucket, Key):
        if Key not in store:
            raise ClientError()
        return {'Body': FakeBody(store[Key])}

    def head_object(self, Bucket, Key):
        if Key not in store:
            raise ClientError()
        return {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        store[Key] = Body


botocore, exc, boto3 = types.ModuleType('botocore'), types.ModuleType('botocore.exceptions'), types.ModuleType('boto3')
exc.ClientError = ClientError
boto3.client = lambda *a, **k: FakeS3()
sys.modules.update({'botocore': botocore, 'botocore.exceptions': exc, 'boto3': boto3})
sys.path.insert(0, str(pkg))
L = importlib.import_module('lambda_function')
L.time.sleep = lambda s: None

fails = []


def check(name, cond):
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        fails.append(name)


def ev(method, path, body=None, cookies=None, b64=False):
    return {'requestContext': {'http': {'method': method}}, 'rawPath': path, 'body': body,
            'cookies': cookies or [], 'isBase64Encoded': b64}


h = L.handler
check('anonymous / shows the login page', 'Passcode' in h(ev('GET', '/'), None)['body'])
check('anonymous api is 401', h(ev('GET', '/api/collection'), None)['statusCode'] == 401)
check('anonymous image is 401', h(ev('GET', '/card_images/OP01-026.jpg'), None)['statusCode'] == 401)
check('wrong passcode is 401', h(ev('POST', '/login', json.dumps({'passcode': 'nope'})), None)['statusCode'] == 401)
check('forged cookie is rejected', 'Passcode' in h(ev('GET', '/', cookies=['evento_session=forged']), None)['body'])

r = h(ev('POST', '/login', json.dumps({'passcode': 'test-passcode'})), None)
check('right passcode logs in with a secure cookie',
      r['statusCode'] == 200 and 'Secure' in r['cookies'][0] and 'HttpOnly' in r['cookies'][0])
ck = [r['cookies'][0].split(';')[0]]

page = h(ev('GET', '/', cookies=ck), None)
check('logged-in / serves the app', page['statusCode'] == 200 and 'JP Event Binder' in page['body'])
cards = json.loads(h(ev('GET', '/cards.json', cookies=ck), None)['body'])
check('login page has a favicon', 'rel="icon"' in h(ev('GET', '/'), None)['body'])
check('the app page has a favicon', 'rel="icon"' in page['body'])
check('cards.json has 404 cards', len(cards) == 404)
check('cards have the fields the UI needs', all({'slot', 'name', 'num', 'rar', 'set', 'foil'} <= set(c) for c in cards))

check('empty collection is {}', h(ev('GET', '/api/collection', cookies=ck), None)['body'] == '{}')
good = {'OP01-026': {'jp': True}, 'ST01-014': {'jp': True, 'foil': True, 'en': True, 'kr': True}}
check('valid save is accepted', h(ev('PUT', '/api/collection', json.dumps(good), cookies=ck), None)['statusCode'] == 200)
check('save reads back', json.loads(h(ev('GET', '/api/collection', cookies=ck), None)['body']) == good)
enc = base64.b64encode(json.dumps(good).encode()).decode()
check('base64 body is accepted', h(ev('PUT', '/api/collection', enc, cookies=ck, b64=True), None)['statusCode'] == 200)
for label, bad in (('unknown field', {'a': {'x': True}}), ('legacy true', {'a': True}), ('false value', {'a': {'jp': False}}),
                   ('empty entry', {'a': {}}), ('not an object', [1])):
    check('rejects ' + label, h(ev('PUT', '/api/collection', json.dumps(bad), cookies=ck), None)['statusCode'] == 400)
check('rejects oversized body', h(ev('PUT', '/api/collection', 'x' * 300_000, cookies=ck), None)['statusCode'] == 413)

store['images/OP01-026.jpg'] = b'\xff\xd8jpegdata'
img = h(ev('GET', '/card_images/OP01-026.jpg', cookies=ck), None)
check('image is served base64 and cacheable',
      img['statusCode'] == 200 and img['isBase64Encoded'] and base64.b64decode(img['body']).startswith(b'\xff\xd8')
      and 'immutable' in img['headers']['Cache-Control'])
check('missing image is 404', h(ev('GET', '/card_images/ZZZ99-999.jpg', cookies=ck), None)['statusCode'] == 404)
check('path traversal is 404', h(ev('GET', '/card_images/../data/collection.json', cookies=ck), None)['statusCode'] == 404)
check('unknown route is 404', h(ev('GET', '/nope', cookies=ck), None)['statusCode'] == 404)

# Japanese art lives under images_jp/ and is served as PNG; English art is unaffected
png = b'\x89PNG\r\n\x1a\nfakepngdata'
store['images_jp/OP01-026.png'] = png
store['images_jp/manifest.json'] = b'["OP01-026"]'
check('JP art needs a login', h(ev('GET', '/card_images_jp/OP01-026.png'), None)['statusCode'] == 401)
check('JP manifest needs a login', h(ev('GET', '/card_images_jp/manifest.json'), None)['statusCode'] == 401)
jp = h(ev('GET', '/card_images_jp/OP01-026.png', cookies=ck), None)
check('JP art is served as a cacheable base64 PNG',
      jp['statusCode'] == 200 and jp['isBase64Encoded'] and jp['headers']['Content-Type'] == 'image/png'
      and base64.b64decode(jp['body']) == png and 'immutable' in jp['headers']['Cache-Control'])
mf = h(ev('GET', '/card_images_jp/manifest.json', cookies=ck), None)
check('JP manifest is served and never cached',
      mf['statusCode'] == 200 and json.loads(mf['body']) == ['OP01-026'] and mf['headers']['Cache-Control'] == 'no-store')
check('missing JP art is 404', h(ev('GET', '/card_images_jp/ZZZ99-999.png', cookies=ck), None)['statusCode'] == 404)
check('JP route only serves PNG card codes',
      all(h(ev('GET', '/card_images_jp/' + n, cookies=ck), None)['statusCode'] == 404
          for n in ('OP01-026.jpg', '../data/collection.json', '../images/OP01-026.jpg', 'op01-026.png', 'manifest.jsonx')))
check('English art still works alongside', h(ev('GET', '/card_images/OP01-026.jpg', cookies=ck), None)['statusCode'] == 200)
del store['images_jp/manifest.json']
check('a missing JP manifest is a clean 404 (the app then falls back to English)',
      h(ev('GET', '/card_images_jp/manifest.json', cookies=ck), None)['statusCode'] == 404)

# ----------------------------------------------------------------------------------------------
# Passkeys: a software authenticator (real EC keys, real CBOR/COSE) runs the full ceremonies.
# ----------------------------------------------------------------------------------------------
import hashlib  # noqa: E402

import cbor2  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

ORIGIN, RP_ID = L.ORIGIN, L.RP_ID
b64u, b64ud = L.b64u, L.b64ud


class Authenticator:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(32)
        self.count = 0
        n = self.key.public_key().public_numbers()
        self.cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, 'big'), -3: n.y.to_bytes(32, 'big')})

    def create(self, options, origin=ORIGIN, uv=True):
        challenge = options['challenge']
        client = json.dumps({'type': 'webauthn.create', 'challenge': challenge, 'origin': origin,
                             'crossOrigin': False}).encode()
        flags = 0x01 | (0x04 if uv else 0) | 0x40  # UP | UV | AT
        auth = (hashlib.sha256(options['rp']['id'].encode()).digest() + bytes([flags]) + (0).to_bytes(4, 'big')
                + bytes(16) + len(self.cred_id).to_bytes(2, 'big') + self.cred_id + self.cose)
        att = cbor2.dumps({'fmt': 'none', 'attStmt': {}, 'authData': auth})
        return {'id': b64u(self.cred_id), 'rawId': b64u(self.cred_id), 'type': 'public-key',
                'authenticatorAttachment': 'platform', 'clientExtensionResults': {},
                'response': {'clientDataJSON': b64u(client), 'attestationObject': b64u(att), 'transports': ['internal']}}

    def get(self, options, origin=ORIGIN, uv=True, key=None, bump=True):
        if bump:
            self.count += 1
        client = json.dumps({'type': 'webauthn.get', 'challenge': options['challenge'], 'origin': origin,
                             'crossOrigin': False}).encode()
        flags = 0x01 | (0x04 if uv else 0)
        auth = hashlib.sha256(options['rpId'].encode()).digest() + bytes([flags]) + self.count.to_bytes(4, 'big')
        sig = (key or self.key).sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {'id': b64u(self.cred_id), 'rawId': b64u(self.cred_id), 'type': 'public-key',
                'clientExtensionResults': {},
                'response': {'clientDataJSON': b64u(client), 'authenticatorData': b64u(auth),
                             'signature': b64u(sig), 'userHandle': b64u(L.USER_ID)}}


def post(path, obj=None, cookies=None):
    return h(ev('POST', path, json.dumps(obj) if obj is not None else None, cookies=cookies), None)


def j(r):
    return json.loads(r['body'])


check('passkey login options are public and well-formed', (lambda o: o['options']['rpId'] == RP_ID
      and o['options']['userVerification'] == 'required' and '.' in o['token'])(j(post('/webauthn/login/options'))))
check('adding a passkey needs a session', post('/webauthn/register/options')['statusCode'] == 401)
check('listing passkeys needs a session', h(ev('GET', '/webauthn/passkeys'), None)['statusCode'] == 401)
check('no passkeys yet', j(h(ev('GET', '/webauthn/passkeys', cookies=ck), None)) == [])

phone = Authenticator()
reg = j(post('/webauthn/register/options', cookies=ck))
check('registration asks for a discoverable, user-verified passkey',
      reg['options']['authenticatorSelection']['residentKey'] == 'required'
      and reg['options']['authenticatorSelection']['userVerification'] == 'required'
      and reg['options']['rp']['id'] == RP_ID)
check('registration is rejected for a wrong origin',
      post('/webauthn/register/verify', {'token': reg['token'], 'credential': phone.create(reg['options'], origin='https://evil.example')}, ck)['statusCode'] == 400)
check('registration is rejected without user verification',
      post('/webauthn/register/verify', {'token': reg['token'], 'credential': phone.create(reg['options'], uv=False)}, ck)['statusCode'] == 400)
check('registration is rejected with a login-purpose token',
      post('/webauthn/register/verify', {'token': j(post('/webauthn/login/options'))['token'], 'credential': phone.create(reg['options'])}, ck)['statusCode'] == 400)
check('registration is rejected with a tampered token',
      post('/webauthn/register/verify', {'token': reg['token'][:-4] + 'AAAA', 'credential': phone.create(reg['options'])}, ck)['statusCode'] == 400)
good_reg = post('/webauthn/register/verify', {'token': reg['token'], 'credential': phone.create(reg['options']), 'name': '  My   phone  '}, ck)
check('a valid passkey registers', good_reg['statusCode'] == 200 and j(good_reg)['passkeys'][0]['name'] == 'My phone')
check('registration cannot be replayed',
      post('/webauthn/register/verify', {'token': reg['token'], 'credential': phone.create(reg['options'])}, ck)['statusCode'] == 400)
check('the same device is not registered twice', post('/webauthn/register/verify', {'token': (lambda r: r['token'])(j(post('/webauthn/register/options', cookies=ck))), 'credential': phone.create(j(post('/webauthn/register/options', cookies=ck))['options'])}, ck)['statusCode'] == 400)

# --- sign in
opts = j(post('/webauthn/login/options'))
login = post('/webauthn/login/verify', {'token': opts['token'], 'credential': phone.get(opts['options'])})
check('passkey sign-in works and sets the session cookie',
      login['statusCode'] == 200 and login['cookies'][0].startswith('evento_session=') and 'Secure' in login['cookies'][0])
check('the cookie from a passkey sign-in is accepted', h(ev('GET', '/api/collection', cookies=[login['cookies'][0].split(';')[0]]), None)['statusCode'] == 200)
check('sign-in cannot be replayed',
      post('/webauthn/login/verify', {'token': opts['token'], 'credential': phone.get(opts['options'], bump=False)})['statusCode'] == 400)
check('sign-count and last-used are recorded', (lambda k: k['n'] == 1 and k['used'])(json.loads(store[L.KEYS_KEY])[0]))

o2 = j(post('/webauthn/login/options'))
check('sign-in with the wrong key is rejected',
      post('/webauthn/login/verify', {'token': o2['token'], 'credential': phone.get(o2['options'], key=ec.generate_private_key(ec.SECP256R1()))})['statusCode'] == 401)
o3 = j(post('/webauthn/login/options'))
check('sign-in from the wrong origin is rejected',
      post('/webauthn/login/verify', {'token': o3['token'], 'credential': phone.get(o3['options'], origin='https://evil.example')})['statusCode'] == 401)
o4 = j(post('/webauthn/login/options'))
check('sign-in without user verification is rejected',
      post('/webauthn/login/verify', {'token': o4['token'], 'credential': phone.get(o4['options'], uv=False)})['statusCode'] == 401)
o5, o6 = j(post('/webauthn/login/options')), j(post('/webauthn/login/options'))
check('an assertion for a different challenge is rejected',
      post('/webauthn/login/verify', {'token': o5['token'], 'credential': phone.get(o6['options'])})['statusCode'] == 401)
check('an unknown passkey is rejected',
      post('/webauthn/login/verify', {'token': o6['token'], 'credential': Authenticator().get(o6['options'])})['statusCode'] == 401)
o7 = j(post('/webauthn/login/options'))
real_time = L.time.time
L.time.time = lambda: real_time() + 1000
expired = post('/webauthn/login/verify', {'token': o7['token'], 'credential': phone.get(o7['options'])})['statusCode']
L.time.time = real_time
check('an expired challenge is rejected', expired == 400)
check('a rolled-back sign counter is rejected (cloned key)',
      (lambda o: (setattr(phone, 'count', 0), post('/webauthn/login/verify', {'token': o['token'], 'credential': phone.get(o['options'], bump=False)}))[1]['statusCode'])(j(post('/webauthn/login/options'))) == 401)
phone.count = 5

# --- a second device, then revoke the first
laptop = Authenticator()
r2 = j(post('/webauthn/register/options', cookies=ck))
check('an already-registered passkey is excluded from new registrations', len(r2['options']['excludeCredentials']) == 1)
check('a second passkey registers', post('/webauthn/register/verify', {'token': r2['token'], 'credential': laptop.create(r2['options']), 'name': 'Laptop'}, ck)['statusCode'] == 200)
check('both passkeys are listed', [p['name'] for p in j(h(ev('GET', '/webauthn/passkeys', cookies=ck), None))] == ['My phone', 'Laptop'])
check('the list never exposes key material', all(set(p) == {'id', 'name', 'created', 'used', 'synced'} for p in j(h(ev('GET', '/webauthn/passkeys', cookies=ck), None))))
check('removing a passkey needs a session', h(ev('DELETE', '/webauthn/passkeys/' + b64u(phone.cred_id)), None)['statusCode'] == 401)
check('removing an unknown passkey is 404', h(ev('DELETE', '/webauthn/passkeys/nope', cookies=ck), None)['statusCode'] == 404)
check('a passkey can be removed', h(ev('DELETE', '/webauthn/passkeys/' + b64u(phone.cred_id), cookies=ck), None)['statusCode'] == 200)
o8 = j(post('/webauthn/login/options'))
check('a removed passkey can no longer sign in', post('/webauthn/login/verify', {'token': o8['token'], 'credential': phone.get(o8['options'])})['statusCode'] == 401)
o9 = j(post('/webauthn/login/options'))
check('the remaining passkey still signs in', post('/webauthn/login/verify', {'token': o9['token'], 'credential': laptop.get(o9['options'])})['statusCode'] == 200)
check('the passcode still works as a fallback', h(ev('POST', '/login', json.dumps({'passcode': 'test-passcode'})), None)['statusCode'] == 200)

# --- limit
for i in range(L.MAX_PASSKEYS):
    ro = j(post('/webauthn/register/options', cookies=ck))
    a = Authenticator()
    post('/webauthn/register/verify', {'token': ro['token'], 'credential': a.create(ro['options']), 'name': f'k{i}'}, ck)
ro = j(post('/webauthn/register/options', cookies=ck))
check('the number of passkeys is capped',
      len(json.loads(store[L.KEYS_KEY])) == L.MAX_PASSKEYS
      and post('/webauthn/register/verify', {'token': ro['token'], 'credential': Authenticator().create(ro['options'])}, ck)['statusCode'] == 400)

shutil.rmtree(pkg, ignore_errors=True)
if fails:
    print(f'\n{len(fails)} FAILED')
    sys.exit(1)
print('\nall passed')
