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

    def put_object(self, Bucket, Key, Body, ContentType):
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

shutil.rmtree(pkg, ignore_errors=True)
if fails:
    print(f'\n{len(fails)} FAILED')
    sys.exit(1)
print('\nall passed')
