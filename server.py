"""Local server for the One Piece Event Binder app.

Serves the page and card images, saves your collection to collection.json, and
(because the app has an in-page editor) lets you edit the card / product / share
data and upload card art. Listens on 127.0.0.1 only (this computer, nobody else).
"""
import base64
import json
import re
import os
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SAVE = os.path.join(HERE, 'collection.json')
CARDS = os.path.join(HERE, 'cards.json')
PRODUCTS = os.path.join(HERE, 'products.json')
SHARE = os.path.join(HERE, 'aws', 'share_data.json')
BINDERS = os.path.join(HERE, 'binders.json')
JP_DIR = os.path.join(HERE, 'card_images_jp')
MANIFEST = os.path.join(JP_DIR, 'manifest.json')
PORT = int(os.environ.get('BINDER_PORT', '8765'))
LOCK = threading.Lock()


# Per card: {"jp": true, "foil": true, "en": true, "kr": true} - only true values are stored.
#   jp   = you own the Japanese card (this is what counts as "complete")
#   foil = you own the Japanese foil/parallel
#   en / kr = you are holding an English / Korean copy as a placeholder
#   manga = you own the optional Manga (special-art) version - never needed for a card to count as complete
#   ordered = bought (e.g. through a proxy) but not received yet; note = free text about that order
FIELDS = {'jp', 'foil', 'en', 'kr', 'manga', 'ordered'}
NOTE_MAX = 200
ALT_STATES = {'want', 'ordered', 'have'}      # per alt-art / Manga version: collecting it, bought and on the way, in hand

# Data-editing constraints (mirrored by the editor in index.html)
COLOURS = {'Red', 'Green', 'Blue', 'Purple', 'Black', 'Yellow'}
ALT_TYPES = {'Comic', 'Original', 'Animation', 'Other'}
NUM_RE = re.compile(r'^(?:[A-Z]{2,3}\d{2}|P)-\d{3}$')
ALT_RE = re.compile(r'^p\d{1,2}$')
SET_RE = re.compile(r'^(?:[A-Z]{2,3}-\d{2}|P-\d{3,4})$')
IMG_PATH_RE = re.compile(r'(card_images|card_images_jp)/[A-Za-z0-9_-]+\.(jpg|png)')


def valid(owned):
    if not isinstance(owned, dict):
        return False
    for k, v in owned.items():
        if not isinstance(k, str) or not isinstance(v, dict) or not v:
            return False
        for f, x in v.items():
            if f == 'note':                      # free text for an order: where it was bought, order id ...
                if not isinstance(x, str) or not 0 < len(x) <= NOTE_MAX:
                    return False
            elif f == 'show':                    # which version sits in the binder slot: 'std' (the regular card) or an alt id
                if not (isinstance(x, str) and re.fullmatch(r'std|p\d{1,2}', x)):
                    return False
            elif f == 'alt':                     # per alt-art / Manga version: {"p2": "want" | "ordered" | "have"}
                if not isinstance(x, dict) or not x or not all(re.fullmatch(r'p\d{1,2}', str(a)) and st in ALT_STATES for a, st in x.items()):
                    return False
            elif f not in FIELDS or x is not True:
                return False
    return True


def valid_card(c):
    if not isinstance(c, dict):
        return False
    if not isinstance(c.get('name'), str) or not c['name'].strip():
        return False
    if not isinstance(c.get('num'), str) or not NUM_RE.match(c['num']):
        return False
    if not isinstance(c.get('rar'), str):
        return False
    if not isinstance(c.get('set'), str) or not SET_RE.match(c['set']):
        return False
    if c.get('col') not in COLOURS:
        return False
    if not isinstance(c.get('foil'), bool):
        return False
    if isinstance(c.get('slot'), int) is False and 'slot' in c:
        return False
    if 'binder' in c and not isinstance(c['binder'], str):
        return False
    if 'noen' in c and not isinstance(c['noen'], bool):
        return False
    if 'manga' in c and not (isinstance(c['manga'], str) and ALT_RE.match(c['manga'])):
        return False
    for key in ('alt', 'altsame', 'altstar'):
        if key in c and (not isinstance(c[key], list) or not all(isinstance(a, str) and ALT_RE.match(a) for a in c[key])):
            return False
    for key in ('altsrc', 'alttype', 'altdup'):
        if key in c and (not isinstance(c[key], dict) or not all(isinstance(a, str) and isinstance(v, str) for a, v in c[key].items())):
            return False
    return True


def valid_cards(cards):
    if not isinstance(cards, list):
        return False
    nums = set()
    for c in cards:
        if not valid_card(c):
            return False
        if c['num'] in nums:
            return False
        nums.add(c['num'])
    return True


def valid_products(products):
    if not isinstance(products, dict):
        return False
    if not isinstance(products.get('products'), list):
        return False
    codes = set()
    for p in products['products']:
        if not isinstance(p, dict) or not isinstance(p.get('code'), str) or p['code'] in codes:
            return False
        codes.add(p['code'])
    src = products.get('sources')
    if not isinstance(src, dict):
        return False
    for num, codes2 in src.items():
        if not isinstance(num, str) or not isinstance(codes2, list) or not all(isinstance(s, str) for s in codes2):
            return False
    return True


def valid_share(share):
    if not isinstance(share, dict):
        return False
    for num, v in share.items():
        if not isinstance(num, str) or not isinstance(v, dict):
            return False
        if not isinstance(v.get('jp'), str):
            return False
        if not isinstance(v.get('sid'), int):
            return False
        if not isinstance(v.get('alt'), list) or not all(isinstance(a, str) and ALT_RE.match(a) for a in v['alt']):
            return False
    return True


def valid_binders(binders):
    if not isinstance(binders, list):
        return False
    ids = set()
    for b in binders:
        if not isinstance(b, dict) or not isinstance(b.get('id'), str) or not b['id'] or not isinstance(b.get('name'), str):
            return False
        if b['id'] in ids:
            return False
        ids.add(b['id'])
    return True


def load():
    try:
        with open(SAVE, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(owned):
    tmp = SAVE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(owned, f, indent=1, sort_keys=True)
    if os.path.exists(SAVE):
        try:
            os.replace(SAVE, SAVE + '.bak')
        except OSError:
            pass
    os.replace(tmp, SAVE)


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)


def update_manifest(num):
    manifest = load_json(MANIFEST, [])
    if not isinstance(manifest, list):
        manifest = []
    if num not in manifest:
        manifest.append(num)
        save_json(MANIFEST, manifest)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self, limit):
        n = int(self.headers.get('Content-Length', '0'))
        if n > limit:
            return None, 'too large'
        try:
            return json.loads(self.rfile.read(n)), None
        except ValueError:
            return None, 'bad json'

    def do_GET(self):
        if self.path.split('?')[0] == '/api/collection':
            with LOCK:
                return self._json(200, load())
        if self.path.split('?')[0] in ('/collection.json', '/collection.json.bak', '/server.py'):
            return self.send_error(404)
        return super().do_GET()

    def do_PUT(self):
        path = self.path.split('?')[0]
        # /api/collection is the odd one out (validated with valid(), saved via save()).
        if path == '/api/collection':
            body, err = self._read_body(200_000)
            if err == 'too large':
                return self._json(413, {'error': 'too large'})
            if err or body is None or not valid(body):
                return self._json(400, {'error': 'bad shape'})
            with LOCK:
                try:
                    save(body)
                except OSError as e:
                    return self._json(500, {'error': str(e)})
            return self._json(200, {'saved': len(body)})

        if path not in ('/api/cards', '/api/products', '/api/share-data', '/api/binders'):
            return self.send_error(404)
        body, err = self._read_body(10_000_000)
        if err == 'too large':
            return self._json(413, {'error': 'too large'})
        if err or body is None:
            return self._json(400, {'error': 'bad json'})
        checker = {'/api/cards': valid_cards, '/api/products': valid_products, '/api/share-data': valid_share, '/api/binders': valid_binders}[path]
        dest = {'/api/cards': CARDS, '/api/products': PRODUCTS, '/api/share-data': SHARE, '/api/binders': BINDERS}[path]
        if not checker(body):
            return self._json(400, {'error': 'bad shape'})
        with LOCK:
            try:
                save_json(dest, body)
            except OSError as e:
                return self._json(500, {'error': str(e)})
        return self._json(200, {'saved': True})

    def do_POST(self):
        if self.path.split('?')[0] != '/api/image':
            return self.send_error(404)
        body, err = self._read_body(10_000_000)
        if err == 'too large':
            return self._json(413, {'error': 'too large'})
        if err or body is None:
            return self._json(400, {'error': 'bad json'})
        rel = str(body.get('path') or '')
        data = body.get('data')
        if not IMG_PATH_RE.fullmatch(rel) or not isinstance(data, str) or not data:
            return self._json(400, {'error': 'bad path'})
        try:
            raw = base64.b64decode(data)
        except ValueError:
            return self._json(400, {'error': 'bad image'})
        dest = os.path.join(HERE, rel.replace('/', os.sep))
        with LOCK:
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, 'wb') as f:
                    f.write(raw)
                if rel.startswith('card_images_jp/'):
                    m = re.fullmatch(r'((?:[A-Z]{2,3}\d{2}|P)-\d{3})\.png', os.path.basename(rel))
                    if m:
                        update_manifest(m.group(1))
            except OSError as e:
                return self._json(500, {'error': str(e)})
        return self._json(200, {'saved': rel})

    def end_headers(self):
        # Pages and data must always be re-checked so edits show up on refresh;
        # card images never change, so let the browser keep them.
        if not self.path.split('?')[0].lower().endswith(('.jpg', '.png')):
            self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


def main():
    try:
        srv = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    except OSError:
        print(f'Port {PORT} is busy - the binder may already be running. Opening it.')
        webbrowser.open(f'http://127.0.0.1:{PORT}/')
        return
    url = f'http://127.0.0.1:{PORT}/'
    print(f'One Piece Event Binder running at {url}')
    print(f'Your collection is saved in: {SAVE}')
    print('Close this window to stop it.')
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    sys.exit(main())
