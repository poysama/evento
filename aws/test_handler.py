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
for src in (REPO / 'aws' / 'lambda_function.py', REPO / 'aws' / 'share_data.json', REPO / 'index.html', REPO / 'cards.json', REPO / 'products.json'):
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

    def generate_presigned_url(self, op, Params, ExpiresIn):
        assert op == 'get_object' and ExpiresIn <= 3600 and Params['Bucket']
        return f"https://fake-bucket.s3.ap-southeast-1.amazonaws.com/{Params['Key']}?X-Amz-Expires={ExpiresIn}&sig=abc"


botocore, exc, boto3 = types.ModuleType('botocore'), types.ModuleType('botocore.exceptions'), types.ModuleType('boto3')
exc.ClientError = ClientError
cfg_mod = types.ModuleType('botocore.config')
cfg_mod.Config = lambda **k: None
boto3.client = lambda *a, **k: FakeS3()
sys.modules.update({'botocore': botocore, 'botocore.exceptions': exc, 'botocore.config': cfg_mod, 'boto3': boto3})
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
check('cards.json has 410 cards (404 set Events + 6 promo-only Events)', len(cards) == 410)
check('the 17 Manga Events are marked, each with a real alt-art id',
      sorted(c['num'] for c in cards if c.get('manga')) == sorted(['OP09-020', 'OP09-057', 'OP09-078', 'OP09-096', 'OP10-019', 'OP11-018', 'OP11-080', 'OP11-114',
                                                                   'OP12-037', 'OP12-060', 'OP13-076', 'OP14-096', 'OP14-118', 'OP15-077', 'OP15-116', 'OP16-116', 'OP17-037'])
      and all(c['manga'] in c['alt'] for c in cards if c.get('manga')))
check('cards have the fields the UI needs', all({'slot', 'name', 'num', 'rar', 'set', 'foil'} <= set(c) for c in cards))

# "What to buy" data: every product, and where each Event card can be found
check('the product list needs a login', h(ev('GET', '/products.json'), None)['statusCode'] == 401)
prod = json.loads(h(ev('GET', '/products.json', cookies=ck), None)['body'])
codes = [p['code'] for p in prod['products']]
check('all 62 products (59 sets + 3 promo sources) are listed once, in the binder\'s release order',
      len(codes) == 62 and len(set(codes)) == 62 and codes[0] == 'ST-01' and codes[-1] == 'OP-17'
      and [c['set'] for c in cards if c['set'] in codes][0] == 'ST-01')
check('every product says what it is', all(p['kind'] in ('Booster pack', 'Extra booster', 'Premium booster', 'Starter deck', 'Promo pack', 'Event promo', 'Cinema promo') for p in prod['products']))
check('every card can be found in at least the set it came from',
      set(prod['sources']) == {c['num'] for c in cards} and all(c['set'] in prod['sources'][c['num']] for c in cards))
check('every source is a real product', all(s in codes for v in prod['sources'].values() for s in v))
check('reprints are recorded (some cards appear in more than one product)', sum(1 for v in prod['sources'].values() if len(v) > 1) >= 50)

check('empty collection is {}', h(ev('GET', '/api/collection', cookies=ck), None)['body'] == '{}')
good = {'OP01-026': {'jp': True}, 'ST01-014': {'jp': True, 'foil': True, 'en': True, 'kr': True}}
_ord = {'OP01-027': {'ordered': True, 'note': 'Buyee order #12345 (Mercari)'}, 'OP01-028': {'ordered': True}}
check('an "on order" card with a note is accepted', h(ev('PUT', '/api/collection', json.dumps(_ord), cookies=ck), None)['statusCode'] == 200
      and json.loads(h(ev('GET', '/api/collection', cookies=ck), None)['body']) == _ord)
for _label, _bad in (('an empty note', {'OP01-027': {'ordered': True, 'note': ''}}), ('a non-text note', {'OP01-027': {'ordered': True, 'note': 5}}),
                     ('a too-long note', {'OP01-027': {'ordered': True, 'note': 'x' * 201}}), ('ordered = false', {'OP01-027': {'ordered': False}}),
                     ('an unknown field', {'OP01-027': {'ordered': True, 'shipped': True}})):
    check('saving rejects ' + _label, h(ev('PUT', '/api/collection', json.dumps(_bad), cookies=ck), None)['statusCode'] == 400)
_alt = {'OP01-029': {'alt': {'p3': 'want', 'p4': 'have'}}, 'OP09-020': {'jp': True, 'alt': {'p2': 'ordered'}}}
check('alt-art / Manga marks are accepted', h(ev('PUT', '/api/collection', json.dumps(_alt), cookies=ck), None)['statusCode'] == 200
      and json.loads(h(ev('GET', '/api/collection', cookies=ck), None)['body']) == _alt)
for _label, _bad in (('an unknown alt state', {'OP01-029': {'alt': {'p3': 'maybe'}}}), ('a bad alt id', {'OP01-029': {'alt': {'x3': 'want'}}}),
                     ('an empty alt map', {'OP01-029': {'alt': {}}}), ('a non-object alt', {'OP01-029': {'alt': 'want'}})):
    check('saving rejects ' + _label, h(ev('PUT', '/api/collection', json.dumps(_bad), cookies=ck), None)['statusCode'] == 400)
_show = {'OP01-029': {'alt': {'p3': 'have'}, 'show': 'p3'}, 'OP09-020': {'show': 'std'}}
check('the version shown in the binder can be saved', h(ev('PUT', '/api/collection', json.dumps(_show), cookies=ck), None)['statusCode'] == 200
      and json.loads(h(ev('GET', '/api/collection', cookies=ck), None)['body']) == _show)
for _label, _bad in (('a bad show value', {'OP01-029': {'show': 'p'}}), ('a non-text show value', {'OP01-029': {'show': 3}}), ('show = true', {'OP01-029': {'show': True}})):
    check('saving rejects ' + _label, h(ev('PUT', '/api/collection', json.dumps(_bad), cookies=ck), None)['statusCode'] == 400)
_cj = json.loads((L.HERE / 'cards.json').read_text(encoding='utf-8'))
_mg = [c['num'] for c in _cj if c.get('manga')]
check('all 17 Manga versions are in the data', len(_mg) == 17 and 'OP17-037' in _mg and 'OP10-019' in _mg and 'OP09-096' in _mg)
check('OP01-030 has its 2nd Anniversary alt art', next(c for c in _cj if c['num'] == 'OP01-030')['altsrc'] == {'p1': '2nd Anniversary Set'})
check('every alt print has a source and a Bandai illustration type', all(set(c['altsrc']) == set(c['alt']) == set(c['alttype']) for c in _cj if c.get('alt'))
      and all(v in ('Comic', 'Original', 'Animation', 'Other') for c in _cj for v in (c.get('alttype') or {}).values()))
check('the Manga id is one of the card\'s alt ids', all(c['manga'] in c['alt'] for c in _cj if c.get('manga')))
check('valid save is accepted', h(ev('PUT', '/api/collection', json.dumps(good), cookies=ck), None)['statusCode'] == 200)
check('the optional Manga flag is accepted and read back',
      h(ev('PUT', '/api/collection', json.dumps({'OP09-057': {'manga': True}}), cookies=ck), None)['statusCode'] == 200
      and json.loads(h(ev('GET', '/api/collection', cookies=ck), None)['body']) == {'OP09-057': {'manga': True}})
h(ev('PUT', '/api/collection', json.dumps(good), cookies=ck), None)
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
# Share "what I'm looking for": private settings API + unlisted public page (no login, no hosted art)
# ----------------------------------------------------------------------------------------------
import re as _re  # noqa: E402


def put_share(obj, cookies=ck):
    return h(ev('PUT', '/api/share', json.dumps(obj), cookies=cookies), None)


def page_of(url):
    return h(ev('GET', url.replace(L.ORIGIN, '')), None)          # anonymous: no cookies


check('share settings need a login', h(ev('GET', '/api/share'), None)['statusCode'] == 401
      and h(ev('PUT', '/api/share', json.dumps({'enabled': True})), None)['statusCode'] == 401)
check('sharing starts switched off', json.loads(h(ev('GET', '/api/share', cookies=ck), None)['body'])
      == {'enabled': False, 'foil': False, 'pics': True, 'name': '', 'message': '', 'url': None})
check('a link that was never created is 404', page_of('/w/' + 'a' * 22)['statusCode'] == 404)

# owned cards must NOT appear on the page; everything else must
store['data/collection.json'] = json.dumps({'ST01-014': {'jp': True}, 'OP01-026': {'jp': True, 'foil': True},
                                            'OP01-027': {'kr': True},
                                            'OP01-029': {'alt': {'p3': 'want', 'p4': 'have'}},       # two alt arts: one wanted, one in hand
                                            'OP09-020': {'alt': {'p2': 'want'}},                     # the Manga version, wanted
                                            'OP01-030': {'alt': {'p1': 'ordered'}}}).encode()        # an alt art already on order
sh = json.loads(put_share({'enabled': True, 'name': 'Poy', 'message': 'Any condition is fine!'})['body'])
check('turning sharing on gives an unguessable link', sh['enabled'] and _re.fullmatch(r'https://evento\.peonbox\.xyz/w/[A-Za-z0-9_-]{22}', sh['url']))
pg = page_of(sh['url'])
body = pg['body']
check('the public page needs no login', pg['statusCode'] == 200 and 'looking for' in body)
check('it lists what is missing, with name and code', 'Round Table' in body and 'OP01-027' in body and 'Punk Gibson' in body)
check('cards already owned in JP are left off', 'Guard Point' not in body and 'ST01-014' not in body and 'OP01-026' not in body)
check('a card held only as a KR/EN placeholder still counts as missing', 'OP01-027' in body)
check('it shows the header, name and message', 'Poy' in body and 'Any condition is fine!' in body and '</b> of 410 still looking for' in body)
check('pills and headings say how many are missing per set', 'ST-01 <i>need 2 of 3</i>' in body and 'ST-01 <small>2 of 3 missing</small>' in body
      and 'ST-02 <i>need all 3</i>' in body and 'P-2207 <i>need it</i>' in body and '/3<' not in body.split('<nav')[1].split('</nav>')[0])
check('missing count is right', f'<b>{410 - 2}</b> of 410 still looking for' in body)
check('Japanese names are included for finding cards in shops', 'lang="ja"' in body)
tok = sh['url'].rsplit('/', 1)[1]
imgs = _re.findall(r'data-s="([^"]+)"', body)
direct = _re.findall(r'<img src="([^"]+)"', body)
check('every picture has a fallback through this same secret link',
      len(imgs) == 408 and all(_re.fullmatch(r'/w/' + tok + r'/img/(?:[A-Z]{2,3}\d{2}|P)-\d{3}\.png', u) for u in imgs) and 'card_images' not in body)
check('pictures load directly from the private bucket (never through the function), from no other site',
      len(direct) == 408 and all(u.startswith('https://fake-bucket.s3.ap-southeast-1.amazonaws.com/images_jp/') and 'Expires=3600' in u for u in direct)
      and not _re.search(r'src="https?://(?!fake-bucket\.s3)', body))
check('each card links out to its page on Bandai\'s official list',
      body.count('href="https://www.onepiece-cardgame.com/cardlist/?search=true&amp;series=55') == 408 and 'rel="noopener noreferrer"' in body)
check('the secret link is never sent to other sites as a referrer',
      pg['headers']['Referrer-Policy'] == 'no-referrer' and 'name="referrer" content="no-referrer"' in body)
check('the page is kept out of search engines', 'noindex' in pg['headers']['X-Robots-Tag'] and 'noindex' in body)
check('no foil section unless asked for', 'foil / alt-art versions wanted' not in body and 'alt-art version' not in body)
check('the page shows nothing that could be a login or key', 'evento_session' not in body and 'passcode' not in body.lower())

# cards bought by proxy (on order) are taken off the "looking for" list, and their note is never shown
_saved = store['data/collection.json']
store['data/collection.json'] = json.dumps({'ST01-014': {'jp': True}, 'OP01-026': {'jp': True, 'foil': True}, 'OP01-027': {'kr': True},
                                            'OP01-028': {'ordered': True, 'note': 'SECRET-ORDER-NOTE-777'},
                                            'OP01-030': {'ordered': True, 'en': True}}).encode()
_ob = page_of(sh['url'])['body']
check('a card on order is not listed as looking for', 'Green Star Rafflesia' not in _ob and 'OP01-028' not in _ob and 'OP01-030' not in _ob)
check('the header counts them separately', f'<b>{410 - 2 - 2}</b> of 410 still looking for' in _ob and '<b>2</b> more already bought and on the way' in _ob)
check('cards still needed stay listed', 'Round Table' in _ob and 'OP01-027' in _ob)
check('the order note is never shown to friends', 'SECRET-ORDER-NOTE-777' not in _ob and 'Buyee' not in _ob)
check('a picture for a card on order is refused (nothing about it leaks)', h(ev('GET', f'/w/{tok}/img/OP01-028.png'), None)['statusCode'] == 404)
check('cards on order are not counted in a per-set need (20 cards, 1 owned, 2 on order)', 'OP-01 <i>need 17 of 20</i>' in _ob)
store['data/collection.json'] = json.dumps({c['num']: {'jp': True} for c in cards if c['num'] not in ('OP01-027',)} | {'OP01-027': {'ordered': True}}).encode()
_all = page_of(sh['url'])['body']
check('when everything missing is on order it says so', 'already on its way' in _all and '<img' not in _all)
store['data/collection.json'] = _saved

# colour filter + "have a non-JP copy" tag
_figs = _re.findall(r'<figure class="c" data-c="([^"]*)"', body)
check('every card carries its colour', len(_figs) == 408 and set(_figs) <= {'Red', 'Green', 'Blue', 'Purple', 'Black', 'Yellow'})
_btns = _re.findall(r'<button type="button" data-c="(\w+)" aria-pressed="false"><i [^>]*></i>\w+ <b>(\d+)</b></button>', body)
check('there is a filter button per colour, with the number still missing', [b[0] for b in _btns] == ['Red', 'Green', 'Blue', 'Purple', 'Black', 'Yellow']
      and sum(int(b[1]) for b in _btns) == 408 and all(int(b[1]) == _figs.count(b[0]) for b in _btns))
check('the filter starts hidden until the script runs (page still works without it)', '<div class="cf" role="group" aria-label="Filter by colour" hidden>' in body)
check('each caption names the colour', 'OP01-027 &middot; ' in body and _re.search(r'<code>OP01-027 &middot; \w+ &middot; (Red|Green|Blue|Purple|Black|Yellow)</code>', body))
check('a card I only hold as a KR copy says so, and nothing else is tagged', body.count('class="tag ph"') == 1
      and 'Have the KR copy, still need the JP card' in body.split('OP01-027 &middot;')[1].split('</figure>')[0])
check('the header counts the cards held as placeholders', 'For <b>1</b> of them I already have the EN or KR copy' in body)
check('cards owned in JP or with no placeholder are not tagged', 'Have the' not in body.split('OP01-028 &middot;')[1].split('</figure>')[0])

# alt-art / Manga option: only the versions I marked "want" (not those in hand or already on order)
sh2 = json.loads(put_share({'foil': True})['body'])
body2 = page_of(sh2['url'])['body']
check('turning the option on adds an alt-art / Manga section', 'Also collecting these alt-art / Manga versions' in body2 and _re.search(r'<b>2</b> alt-art / Manga versions wanted', body2))
alt_part = body2.split('Also collecting these alt-art')[1]
check('it lists the wanted versions with product, illustration type and labels (OP01-029 has 3 alt arts: p3 is "Alt art 2 \u00b7 PRB-01"; OP09-020 p2 is the Manga)',
      'OP01-029 &middot;' in alt_part and '<span class="tag">Alt art 2 \u00b7 PRB-01</span>' in alt_part and '<span class="tag">Manga</span>' in alt_part and 'OP09-020 &middot;' in alt_part
      and 'same drawing as the regular card' in alt_part)
check('versions in hand or on order are not listed', 'OP01-030 &middot;' not in alt_part and alt_part.count('<figure class="c"') == 2)
check('the alt-art pictures are used', 'OP01-029_p3' in alt_part and 'OP09-020_p2' in alt_part and 'OP01-029_p4' not in alt_part)
check('settings persist in the bucket', json.loads(store['data/share.json'])['foil'] is True and sh2['url'] == sh['url'])
put_share({'foil': False})
check('with the option off there is no alt-art section', 'Also collecting these' not in page_of(sh['url'])['body'])
put_share({'foil': True})

# wishlist pricing (Yuyu-tei): fetched on demand only, cached, cooldown on repeated refreshes
check('a bad token gets a plain 404, not a price', h(ev('GET', '/w/' + 'b' * 22 + '/price'), None)['statusCode'] == 404
      and h(ev('POST', '/w/' + 'b' * 22 + '/price'), None)['statusCode'] == 404)
check('another method on the price route is refused', h(ev('PUT', f'/w/{tok}/price'), None)['statusCode'] == 404)
check('before any fetch, the price is empty', json.loads(h(ev('GET', f'/w/{tok}/price'), None)['body']) == {'total': None, 'fetched': None, 'stale': False})

_calls = []


def _fake_search(num):
    _calls.append(num)
    if num == 'OP01-029':
        return [{'path': 'prb01', 'id': '999', 'title': 'OP01-029 P-UC ラディカルビ～～～ム！！！！(パラレル)', 'yen': 320, 'stock': '○'}]
    if num == 'OP09-020':
        return [{'path': 'prb02', 'id': '888', 'title': 'OP09-020 P-R 来い…!!!おれ達が相手をしてやる!!!(パラレル)(PRB2)', 'yen': 24800, 'stock': '×'}]
    return []


L._yuyu_search = _fake_search
p1 = json.loads(h(ev('POST', f'/w/{tok}/price'), None)['body'])
check('a refresh fetches once per distinct card and totals the cheapest match', sorted(_calls) == ['OP01-029', 'OP09-020']
      and p1['total'] == 25120 and p1['matched'] == 2 and p1['wanted'] == 2 and p1['in_stock_count'] == 1 and p1['sold_out_count'] == 1 and p1['stale'] is False)
check('the result is cached in the bucket', json.loads(store['data/price_cache.json'])['total'] == 25120)
p2 = json.loads(h(ev('POST', f'/w/{tok}/price'), None)['body'])
check('a refresh inside the cooldown reuses the cache instead of fetching again', len(_calls) == 2 and p2['cooldown'] is True and p2['total'] == 25120 and p2['retry_after'] > 0)
check('a plain GET always returns the cache without fetching', len(_calls) == 2 and json.loads(h(ev('GET', f'/w/{tok}/price'), None)['body'])['total'] == 25120)
_page_with_price = page_of(sh['url'])['body']
check('the share page bakes in the cached total and a refresh button', '¥25,120' in _page_with_price and 'id="prbtn"' in _page_with_price and 'Refresh price' in _page_with_price)

# the wishlist changed since the last fetch: the page flags the total as stale
store['data/collection.json'] = json.dumps({'OP01-029': {'alt': {'p3': 'want'}}, 'OP09-020': {'alt': {'p2': 'want'}}, 'OP01-055': {'alt': {'p2': 'want'}}}).encode()
_stale_body = page_of(sh['url'])['body']
check('a wishlist that grew since the cached price is flagged as stale', 'the list has changed since this price' in _stale_body)
store['data/collection.json'] = _saved
L._CACHE['own'] = (0.0, {})   # force the 20s owned-cache to refetch: it was just warmed with the temporary stale-test fixture

# the picture route: token-gated, only cards on the list, never a way to probe what you own
def pic(path_tok, name):
    return h(ev('GET', f'/w/{path_tok}/img/{name}'), None)


store['images_jp/OP01-027.png'] = b'\x89PNG\r\n\x1a\nmissing-card'
store['images_jp/OP01-026.png'] = b'\x89PNG\r\n\x1a\nOWNED-card'
store['images_jp/OP01-029_p3.png'] = b'\x89PNG\r\n\x1a\nalt-art'
p1 = pic(tok, 'OP01-027.png')
check('a friend gets a short-lived direct link to a wishlist card picture, with no login',
      p1['statusCode'] == 302 and p1['headers']['Location'].startswith('https://fake-bucket.s3.')
      and '/images_jp/OP01-027.png?' in p1['headers']['Location'] and 'Expires=300' in p1['headers']['Location'])
check('the picture bytes do not pass through the function', p1['body'] == '')
check('picture responses are private, uncrawlable and leak no referrer',
      'private' in p1['headers']['Cache-Control'] and p1['headers']['Referrer-Policy'] == 'no-referrer' and 'noindex' in p1['headers']['X-Robots-Tag'])
check('a card you already own cannot be fetched (nothing to learn about your collection)', pic(tok, 'OP01-026.png')['statusCode'] == 404)
check('a card you own looks exactly like a card that does not exist', pic(tok, 'OP01-026.png')['body'] == pic(tok, 'ZZZ99-999.png')['body'])
check('the page retries a picture that fails to load', "addEventListener('error'" in body and body.count('<script>') == 1)
check('a wanted alt art is served (option on); one in hand or on order is not', pic(tok, 'OP01-029_p3.png')['statusCode'] == 302 and pic(tok, 'OP09-020_p2.png')['statusCode'] == 302
      and pic(tok, 'OP01-029_p4.png')['statusCode'] == 404 and pic(tok, 'OP01-030_p1.png')['statusCode'] == 404)
check('alt-art that is not on the list is refused', pic(tok, 'OP01-029_p9.png')['statusCode'] == 404 and pic(tok, 'OP01-027_p1.png')['statusCode'] == 404)
check('pictures need the right link', pic('b' * 22, 'OP01-027.png')['statusCode'] == 404 and pic('short', 'OP01-027.png')['statusCode'] == 404)
check('picture names are strictly validated',
      all(pic(tok, n)['statusCode'] == 404 for n in ('../data/collection.json', 'OP01-027.jpg', 'op01-027.png', 'OP01-027.png/x', '%2e%2e%2fshare.json', 'OP01-027_p.png')))
check('other sub-paths of the link are 404, a trailing slash is fine', h(ev('GET', f'/w/{tok}/anything'), None)['statusCode'] == 404 and h(ev('GET', f'/w/{tok}/x/y'), None)['statusCode'] == 404 and h(ev('GET', f'/w/{tok}/'), None)['statusCode'] == 200)

# promo-only Events (P-002 ...) work like every other card: listed, pictured, and served through the link
check('promo Events are on the public list', all(n in body for n in ('P-002', 'P-024', 'P-057', 'P-060')) and 'I Smell Adventure!!!' in body)
store['images_jp/P-002.png'] = b'\x89PNG\r\n\x1a\npromo'
check('a promo picture is served with a direct link', pic(tok, 'P-002.png')['statusCode'] == 302 and '/images_jp/P-002.png?' in pic(tok, 'P-002.png')['headers']['Location'])
check('promo card numbers are validated too', pic(tok, 'P-02.png')['statusCode'] == 404 and pic(tok, 'P-0002.png')['statusCode'] == 404)
_pj = json.loads((L.HERE / 'products.json').read_text(encoding='utf-8'))
_pc = {p['code'] for p in _pj['products']}
check('promo Events have a product to be found in', all(set(_pj['sources'][n]) <= _pc for n in ('P-002', 'P-024', 'P-057')) and _pj['sources']['P-002'] == ['P-2207']
      and _pj['sources']['P-057'] == ['P-2311', 'ST-16'])

# safety: text is escaped, lengths and types are enforced
def body3_count_ok(b):      # the only <img> tags on the page are the card pictures (the injected one is inert text)
    return all(t.startswith('<img src="https://fake-bucket.s3.') for t in _re.findall(r'<img[^>]*>', b))


inj =json.loads(put_share({'name': '<script>alert(1)</script>', 'message': '"><img src=x onerror=alert(2)>'})['body'])
b3 = page_of(inj['url'])['body']
check('names and messages cannot inject HTML',
      '<script>alert' not in b3 and '<img src=x' not in b3 and '&lt;script&gt;' in b3 and '&lt;img src=x' in b3
      and body3_count_ok(b3))
check('long text is trimmed', len(json.loads(put_share({'message': 'x' * 900})['body'])['message']) == 300
      and len(json.loads(put_share({'name': 'n' * 200})['body'])['name']) == 40)
check('control characters are stripped', json.loads(put_share({'name': 'a\x00b\nc\td'})['body'])['name'] == 'a b c d')
for label, bad in (('unknown field', {'token': 'x'}), ('non-boolean enabled', {'enabled': 'yes'}), ('non-text name', {'name': 5}),
                   ('non-boolean rotate', {'rotate': 1}), ('not an object', [1])):
    check('share settings reject ' + label, put_share(bad)['statusCode'] == 400)
check('a wrong or malformed link is 404 and reveals nothing',
      all(page_of(u)['statusCode'] == 404 for u in ('/w/' + 'b' * 22, '/w/short', '/w/../api/collection', '/w/' + sh['url'][-22:-1] + 'X', '/w/')))
check('the public page is read-only', h(ev('POST', sh['url'].replace(L.ORIGIN, '')), None)['statusCode'] in (401, 404))

# new link: the old one stops working; stop sharing: the link stops working; turning back on reuses it
new = json.loads(put_share({'rotate': True})['body'])
check('a new link replaces the old one', new['url'] != sh['url'] and page_of(sh['url'])['statusCode'] == 404 and page_of(new['url'])['statusCode'] == 200)
off = json.loads(put_share({'enabled': False})['body'])
check('stopping sharing kills the link', off['enabled'] is False and off['url'] is not None and page_of(new['url'])['statusCode'] == 404)
back = json.loads(put_share({'enabled': True})['body'])
check('sharing again reuses the same link', back['url'] == new['url'] and page_of(back['url'])['statusCode'] == 200)

# pictures switched off: the page is text + links to Bandai, and the picture route serves nothing
np = json.loads(put_share({'pics': False, 'foil': False})['body'])
ntok = np['url'].rsplit('/', 1)[1]
nbody = page_of(np['url'])['body']
check('with pictures off the page has no images at all', np['pics'] is False and '<img' not in nbody and 'class="p"' not in nbody)
check('with pictures off it still lists the cards and links to Bandai',
      'Round Table' in nbody and 'OP01-027' in nbody and nbody.count('Bandai card list &rarr;') == 408 and 'Pictures are not included' in nbody)
check('with pictures off the picture route serves nothing', pic(ntok, 'OP01-027.png')['statusCode'] == 404)
check('pictures can be turned back on', json.loads(put_share({'pics': True})['body'])['pics'] is True and pic(ntok, 'OP01-027.png')['statusCode'] == 302)
check('the pictures switch must be true or false', put_share({'pics': 'no'})['statusCode'] == 400)

# nothing missing (foil option off, so no foil section either)
put_share({'foil': False})
store['data/collection.json'] = json.dumps({c['num']: {'jp': True} for c in cards}).encode()
done = page_of(back['url'])['body']
check('a complete collection says so instead of listing cards', 'the collection is complete' in done and '<img' not in done)
store['data/collection.json'] = json.dumps(good).encode()

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
