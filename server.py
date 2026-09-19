"""Local server for the One Piece Event Binder app.

Serves the page and card images, and saves your collection to collection.json
next to this file. Listens on 127.0.0.1 only (this computer, nobody else).
"""
import json
import os
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SAVE = os.path.join(HERE, 'collection.json')
PORT = int(os.environ.get('BINDER_PORT', '8765'))
LOCK = threading.Lock()


# Per card: {"jp": true, "foil": true, "en": true, "kr": true} - only true values are stored.
#   jp   = you own the Japanese card (this is what counts as "complete")
#   foil = you own the Japanese foil/parallel
#   en / kr = you are holding an English / Korean copy as a placeholder
FIELDS = {'jp', 'foil', 'en', 'kr'}


def valid(owned):
    if not isinstance(owned, dict):
        return False
    for k, v in owned.items():
        if not isinstance(k, str) or not isinstance(v, dict) or not v:
            return False
        if not set(v) <= FIELDS or not all(x is True for x in v.values()):
            return False
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

    def do_GET(self):
        if self.path.split('?')[0] == '/api/collection':
            with LOCK:
                return self._json(200, load())
        if self.path.split('?')[0] in ('/collection.json', '/collection.json.bak', '/server.py'):
            return self.send_error(404)
        return super().do_GET()

    def do_PUT(self):
        if self.path.split('?')[0] != '/api/collection':
            return self.send_error(404)
        try:
            n = int(self.headers.get('Content-Length', '0'))
            if n > 200_000:
                return self._json(413, {'error': 'too large'})
            owned = json.loads(self.rfile.read(n))
            if not valid(owned):
                return self._json(400, {'error': 'bad shape'})
        except ValueError:
            return self._json(400, {'error': 'bad json'})
        with LOCK:
            try:
                save(owned)
            except OSError as e:
                return self._json(500, {'error': str(e)})
        return self._json(200, {'saved': len(owned)})

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
