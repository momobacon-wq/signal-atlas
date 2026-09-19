# -*- coding: utf-8 -*-
"""Local test double for tools/auth/Code.gs: serves docs/ statically AND answers POST /mock-auth with the same
JSON contract, so the login gate can be tested without deploying Apps Script.
GET auth-config.json (and db/../auth-config.json) is overridden to point at /mock-auth.
Usage: py tools/auth/mock_server.py [port] [docs_dir]   (default 8766, ../docs)
Users: tools/auth/mock_users.csv (id,name) — test data only, never the real list.
Log: printed to stdout and appended to tools/auth/mock_log.jsonl
"""
import os, sys, json, time, hmac, hashlib, base64, csv
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
DOCS = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', '..', 'docs'))
SECRET = b'mock-secret'
LOG = os.path.join(HERE, 'mock_log.jsonl')
import unicodedata
FAIL_MODE = {'down': False, 'slow': 0}


def norm(s):
    return ''.join(unicodedata.normalize('NFKC', str(s or '')).split())


def canon(s):
    import re
    return re.sub(r'^0+(?=\d)', '', norm(s))


USERS = {}   # canon id -> {'name': raw display name, 'key': normalised upper-case name}
with open(os.path.join(HERE, 'mock_users.csv'), encoding='utf-8') as f:
    for row in csv.DictReader(f):
        USERS[canon(row['id'])] = {'name': row['name'].strip(), 'key': norm(row['name']).upper()}


def sign(uid, exp):
    mac = hmac.new(SECRET, f'{uid}|{exp}'.encode(), hashlib.sha256).digest()
    return f'{exp}.' + base64.urlsafe_b64encode(mac).decode().rstrip('=')


def verify(uid, token):
    try:
        exp_s, mac = token.split('.', 1)
        exp = int(exp_s)
    except Exception:
        return None
    if exp <= int(time.time() * 1000):
        return None
    return exp if sign(uid, exp) == token else None


def log(rec):
    rec['ts'] = time.strftime('%Y-%m-%d %H:%M:%S')
    print('LOG', json.dumps(rec, ensure_ascii=False), flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=DOCS, **k)

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path.split('?')[0]
        if p.endswith('auth-config.json'):
            return self._json({'endpoint': f'http://127.0.0.1:{PORT}/mock-auth', 'sessionHours': 12, 'title': '請先登入（測試端點）'})
        if p == '/mock-auth':
            return self._json({'ok': True, 'service': 'mock', 'users': len(USERS)})
        if p == '/mock-control':
            return self._json(FAIL_MODE)
        return super().do_GET()

    def do_POST(self):
        p = self.path.split('?')[0]
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b''
        if p == '/mock-control':  # test hook: {"down": true} / {"slow": 20}
            try: FAIL_MODE.update(json.loads(raw or b'{}'))
            except Exception: pass
            return self._json(FAIL_MODE)
        if p != '/mock-auth':
            return self._json({'ok': False, 'error': 'not found'}, 404)
        if FAIL_MODE['down']:  # like a real outage: HTML error page, not JSON
            data = b'<html><body>Service Unavailable</body></html>'
            self.send_response(503); self.send_header('Content-Type', 'text/html'); self.send_header('Access-Control-Allow-Origin', '*'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
        if FAIL_MODE['slow']:
            time.sleep(FAIL_MODE['slow'])
        try:
            body = json.loads(raw or b'{}')
        except Exception:
            return self._json({'ok': False, 'error': '請求格式錯誤'})
        action = body.get('action'); uid = canon(body.get('id')); name = norm(body.get('name')).upper()
        site, page, ua = str(body.get('site', ''))[:60], str(body.get('page', ''))[:200], str(body.get('ua', ''))[:200]
        if action == 'login':
            if not uid:
                return self._json({'ok': False, 'error': '請輸入員工代號。'})
            u = USERS.get(uid)
            if not u:
                log({'id': uid, 'name': name, 'action': 'LOGIN_FAIL', 'site': site, 'page': page})
                return self._json({'ok': False, 'error': '員工代號不在使用者清單中，請再試一次。'})
            exp = int(time.time() * 1000) + 12 * 3600 * 1000
            log({'id': uid, 'name': u['name'], 'action': 'LOGIN', 'site': site, 'page': page, 'ua': ua})
            return self._json({'ok': True, 'id': uid, 'name': u['name'], 'token': sign(uid, exp), 'exp': exp})
        if action == 'resume':
            exp = verify(uid, str(body.get('token', '')))
            if not exp or uid not in USERS:
                return self._json({'ok': False, 'error': '工作階段已失效，請重新登入。'})
            log({'id': uid, 'name': USERS[uid]['name'], 'action': 'VISIT', 'site': site, 'page': page})
            return self._json({'ok': True, 'id': uid, 'name': USERS[uid]['name'], 'exp': exp})
        if action == 'logout':
            log({'id': uid, 'action': 'LOGOUT', 'site': site})
            return self._json({'ok': True})
        return self._json({'ok': False, 'error': '未知的動作'})


class S(ThreadingHTTPServer):
    allow_reuse_address = False  # Windows: refuse to double-bind the port


if __name__ == '__main__':
    print(f'mock auth+static server on http://127.0.0.1:{PORT}/  docs={DOCS}  users={len(USERS)}  log={LOG}', flush=True)
    S(('127.0.0.1', PORT), H).serve_forever()
