"""Loopback Bambuddy -> DingTalk adapter with a persistent delivery queue."""
import base64
from contextlib import contextmanager
from datetime import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from notifications_zh import localized, EVENT_FIELDS

ROOT = Path(os.environ.get('DINGTALK_BRIDGE_DIR', Path(__file__).parent))
LOG = logging.getLogger('dingtalk-bridge')
MAX_BODY = 131072


def config():
    return json.loads((ROOT / 'config.json').read_text())


def ready(c):
    if c.get('mode') == 'app':
        return all(c.get(k) for k in ('app_key', 'app_secret', 'robot_code', 'open_conversation_id'))
    return bool(c.get('webhook_url'))


def photo_url(event, c):
    """Accept only Bambuddy's existing expiring, single-photo URLs."""
    try:
        base = urllib.parse.urlsplit(c.get('external_url', ''))
    except ValueError:
        return None
    for field in ('image_url', 'finish_photo_url'):
        value = event.get(field)
        if not isinstance(value, str) or len(value) > 800:
            continue
        try:
            url = urllib.parse.urlsplit(value)
        except ValueError:
            continue
        if (url.scheme == base.scheme == 'https' and url.netloc == base.netloc
                and not url.username and not url.password and not url.query and not url.fragment
                and re.fullmatch(r'/api/v1/notifications/photos/[a-z0-9_]+_\d{8}_\d{6}_[A-Za-z0-9_-]{32}\.jpg', url.path)):
            return value
    return None


def markdown(event, c=None):
    title, message = localized(event)
    title = str(title)[:160]
    title = title.replace('\n', ' ').replace('\r', ' ')
    text = f'### Bambuddy · {title}\n\n{message}'.replace('\n', '\n\n')
    printer = event.get('printer') or event.get('printer_name')
    if printer and str(printer) not in message:
        text += f'\n\n打印机：{printer}'
    if event.get('timestamp'):
        stamp = str(event['timestamp'])
        try:
            date = datetime.fromisoformat(stamp)
            if date.tzinfo:
                date = date.astimezone(ZoneInfo('Asia/Shanghai'))
            stamp = date.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            pass
        text += f'\n\n时间：{stamp}'
    # Leave room for DingTalk's UTF-8 message-size limit.
    text = text.encode('utf-8')[:12000].decode('utf-8', errors='ignore')
    url = photo_url(event, c or {})
    if url:
        label = '打印完成照片' if event.get('event') == 'print_complete' else '打印现场截图'
        text += f'\n\n![{label}]({url})'
    return title, text


def signed_url(url, secret, timestamp=None):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'oapi.dingtalk.com'
            or parsed.path != '/robot/send' or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('webhook must be the DingTalk HTTPS robot/send endpoint')
    query = dict(urllib.parse.parse_qsl(parsed.query))
    if not query.get('access_token'):
        raise ValueError('webhook is missing access_token')
    for k in ('timestamp', 'sign'):
        query.pop(k, None)
    if secret:
        stamp = str(timestamp if timestamp is not None else int(time.time() * 1000))
        signature = base64.b64encode(hmac.new(secret.encode(), f'{stamp}\n{secret}'.encode(), hashlib.sha256).digest()).decode()
        query.update(timestamp=stamp, sign=signature)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), ''))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class Sender:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.access_token = None
        self.token_expiry = 0
        self.token_identity = None

    def post(self, url, body, headers=None):
        req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(),
                                     headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with self.opener.open(req, timeout=12) as response:
                data = json.loads(response.read(262144))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'dingtalk_http_{exc.code}') from None
        except Exception as exc:
            raise RuntimeError(type(exc).__name__) from None
        if not isinstance(data, dict):
            raise RuntimeError('invalid_dingtalk_response')
        return data

    def send(self, event, c):
        title, text = markdown(event, c)
        if c.get('mode') != 'app':
            result = self.post(signed_url(c['webhook_url'], c.get('sign_secret', '')),
                               {'msgtype': 'markdown', 'markdown': {'title': 'Bambuddy · ' + title, 'text': text}})
            if result.get('errcode') != 0:
                raise RuntimeError(f'dingtalk_errcode_{result.get("errcode", "missing")}')
            return
        identity = (c['app_key'], c['app_secret'])
        if self.token_identity != identity or time.monotonic() >= self.token_expiry:
            token = self.post('https://api.dingtalk.com/v1.0/oauth2/accessToken',
                              {'appKey': c['app_key'], 'appSecret': c['app_secret']})
            if not token.get('accessToken'):
                raise RuntimeError('app_access_token_missing')
            self.access_token = token['accessToken']
            self.token_expiry = time.monotonic() + max(30, int(token.get('expireIn', 7200)) - 120)
            self.token_identity = identity
        try:
            result = self.post('https://api.dingtalk.com/v1.0/robot/groupMessages/send', {
                'robotCode': c['robot_code'], 'openConversationId': c['open_conversation_id'],
                'msgKey': 'sampleMarkdown', 'msgParam': json.dumps({'title': 'Bambuddy · ' + title, 'text': text}, ensure_ascii=False),
            }, {'x-acs-dingtalk-access-token': self.access_token})
            if not result.get('processQueryKey'):
                raise RuntimeError('group_message_not_accepted')
        except Exception:
            self.token_expiry = 0
            raise


@contextmanager
def database():
    db = sqlite3.connect(ROOT / 'queue.db', timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db():
    with database() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT "pending", attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0, error TEXT, created REAL NOT NULL, sent REAL)')


def deliver_one(sender, c):
    with database() as db:
        row = db.execute('SELECT * FROM messages WHERE status=? AND next_try<=? ORDER BY id LIMIT 1',
                         ('pending', time.time())).fetchone()
    if row is None:
        return False
    try:
        sender.send(json.loads(row['payload']), c)
    except Exception as exc:
        attempts = row['attempts'] + 1
        error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        with database() as db:
            db.execute('UPDATE messages SET attempts=?,next_try=?,status=?,error=? WHERE id=?',
                       (attempts, time.time() + min(30 * 2 ** (attempts - 1), 600),
                        'failed' if attempts >= 10 else 'pending', error[:100], row['id']))
        LOG.warning('delivery_failed queue_id=%s attempt=%s code=%s', row['id'], attempts, error[:100])
    else:
        with database() as db:
            db.execute('UPDATE messages SET status=?,sent=?,error=NULL WHERE id=?',
                       ('sent', time.time(), row['id']))
        LOG.info('delivered queue_id=%s', row['id'])
    return True


def worker():
    sender = Sender()
    while True:
        try:
            c = config()
            if not ready(c):
                time.sleep(2)
                continue
            time.sleep(3.2 if deliver_one(sender, c) else 1)
        except Exception as exc:
            LOG.error('worker_error class=%s', type(exc).__name__)
            time.sleep(5)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def response(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path != '/health':
            return self.response(404, {'error': 'not_found'})
        try:
            with database() as db:
                counts = dict(db.execute('SELECT status,count(*) FROM messages GROUP BY status').fetchall())
            self.response(200, {'status': 'healthy', 'configured': ready(config()), 'queue': counts})
        except Exception:
            self.response(503, {'status': 'unavailable'})

    def do_POST(self):
        self.connection.settimeout(10)
        if self.path != '/notify':
            return self.response(404, {'error': 'not_found'})
        try:
            c = config()
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + c['adapter_token']):
                return self.response(401, {'error': 'unauthorized'})
            if not ready(c):
                return self.response(503, {'error': 'dingtalk_not_configured'})
            size = int(self.headers.get('Content-Length', '0'))
            if size < 2 or size > MAX_BODY:
                return self.response(413, {'error': 'invalid_size'})
            event = json.loads(self.rfile.read(size))
            if not isinstance(event, dict) or not isinstance(event.get('message'), str):
                return self.response(400, {'error': 'invalid_payload'})
            # Retain structured template variables and scoped photo URLs, never image bytes or verdict capabilities.
            allowed = EVENT_FIELDS | {'title', 'message', 'printer', 'printer_name', 'timestamp', 'event', 'image_url', 'finish_photo_url'}
            event = {k: v for k, v in event.items() if k in allowed and isinstance(v, (str, int, float, bool, type(None)))}
            with database() as db:
                cursor = db.execute('INSERT INTO messages(payload,created) VALUES(?,?)', (json.dumps(event, ensure_ascii=False), time.time()))
                queue_id = cursor.lastrowid
            self.response(202, {'accepted': True, 'queue_id': queue_id})
        except (ValueError, TypeError):
            self.response(400, {'error': 'invalid_payload'})
        except Exception:
            self.response(500, {'error': 'adapter_error'})


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    init_db()
    threading.Thread(target=worker, name='dingtalk-delivery', daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', 11112), Handler)
    server.daemon_threads = True
    LOG.info('listening on loopback:11112')
    server.serve_forever()


if __name__ == '__main__':
    main()
