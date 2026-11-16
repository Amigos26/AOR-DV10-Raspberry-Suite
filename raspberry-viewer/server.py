"""Read-only gateway: never forwards arbitrary URLs, methods or request bodies."""
import http.client
import json
import logging
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from channel_names import ChannelNames

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('AOR_VIEWER_DATA_DIR', str(Path.home() / '.local/share/aor-dv10'))).expanduser()
channel_names = ChannelNames(DATA_DIR / 'channels.csv')
UPSTREAM = (os.environ.get('AOR_CONTROL_HOST', '127.0.0.1'), int(os.environ.get('UPSTREAM_PORT', '8080')))
cache_lock = threading.Lock()
cached = None
cached_at = 0.0
names = {}
names_at = 0.0
last_received = None
listener_lock = threading.Lock()
audio_listeners = 0


def listener_count():
    with listener_lock:
        return audio_listeners


def fetch_json(path):
    connection = http.client.HTTPConnection(*UPSTREAM, timeout=8)
    try:
        connection.request('GET', path)
        response = connection.getresponse()
        if response.status != 200:
            raise OSError('Upstream HTTP %d' % response.status)
        return json.loads(response.read(2 * 1024 * 1024))
    finally:
        connection.close()


def status():
    global cached, cached_at, names, names_at, last_received
    with cache_lock:
        now = time.monotonic()
        if cached is not None and now - cached_at < 1:
            return cached
        source = fetch_json('/api/status')
        if now - names_at >= 60:
            try:
                rows = fetch_json('/api/memory?limit=2000')['channels']
                grouped = {}
                for row in rows:
                    hz = round(float(row['frequency_mhz']) * 1000000)
                    name = str(row.get('name') or '').strip()
                    if name:
                        grouped.setdefault(hz, set()).add(name)
                names = {hz: ' / '.join(sorted(values)) for hz, values in grouped.items()}
            except (OSError, ValueError, KeyError, TypeError):
                names = {}
            names_at = now
        frequency = source.get('frequency_hz')
        local_names = channel_names.load()
        connected = bool(source.get('connected'))
        receiving = connected and source.get('squelch_open') is True and isinstance(frequency, int) and frequency > 0
        if receiving:
            last_received = {'frequency_hz': frequency, 'received_at': time.time()}
        held = None
        if last_received is not None:
            held = dict(last_received)
            held['channel_name'] = local_names.get(held['frequency_hz']) or names.get(held['frequency_hz'])
        cached = {
            'frequency_hz': frequency,
            'audio_listeners': listener_count(),
            'channel_name': local_names.get(frequency) or names.get(frequency),
            'last_received': held,
            'receiving': receiving,
            'connected': connected,
            'updated_at': time.time(),
        }
        cached_at = time.monotonic()
        return cached


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def headers_for(self, code, content_type, length=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        if length is not None:
            self.send_header('Content-Length', str(length))
        else:
            self.send_header('Connection', 'close')
            self.close_connection = True
        self.end_headers()

    def send_bytes(self, code, content_type, body):
        self.headers_for(code, content_type, len(body))
        self.wfile.write(body)

    def reject(self):
        self.send_bytes(405, 'text/plain; charset=utf-8', b'Read-only endpoint\n')
        self.close_connection = True

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = reject

    def do_GET(self):
        try:
            if self.headers.get('Upgrade') or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0':
                self.reject()
                return
            # Match the entire target: absolute URLs and encoded paths are not proxy targets.
            path = self.path
            if path in ('/', '/viewer.js', '/style.css'):
                name, mime = {
                    '/': ('index.html', 'text/html; charset=utf-8'),
                    '/viewer.js': ('viewer.js', 'text/javascript; charset=utf-8'),
                    '/style.css': ('style.css', 'text/css; charset=utf-8'),
                }[path]
                self.send_bytes(200, mime, (ROOT / name).read_bytes())
            elif path == '/api/status':
                try:
                    result = status()
                    self.send_bytes(200, 'application/json', json.dumps(result).encode())
                except (OSError, ValueError, http.client.HTTPException):
                    self.send_bytes(503, 'application/json', b'{"connected":false,"error":"status_unavailable"}')
            elif path == '/audio.aac':
                self.stream_audio()
            else:
                self.send_bytes(404, 'text/plain', b'Not found\n')
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass

    def stream_audio(self):
        global audio_listeners
        connection = http.client.HTTPConnection(*UPSTREAM, timeout=12)
        started = False
        counted = False
        try:
            connection.request('GET', '/api/audio/live.aac')
            response = connection.getresponse()
            if response.status != 200:
                self.send_bytes(503, 'text/plain', b'Audio unavailable\n')
                return
            self.connection.settimeout(15)
            self.headers_for(200, 'audio/aac')
            started = True
            with listener_lock:
                audio_listeners += 1
            counted = True
            while data := response.read(1024):
                self.wfile.write(data)
                self.wfile.flush()
        except (OSError, http.client.HTTPException):
            if not started:
                self.send_bytes(503, 'text/plain', b'Audio unavailable\n')
        finally:
            if counted:
                with listener_lock:
                    audio_listeners -= 1
            connection.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    server = ThreadingHTTPServer((os.environ.get('AOR_VIEWER_HOST', '127.0.0.1'), int(os.environ.get('PORT', '8081'))), Handler)
    server.daemon_threads = True
    server.serve_forever()
