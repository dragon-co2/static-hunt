"""
Local settings server for the dragon-co2 automation scripts.

Serves settings_ui.html and a tiny JSON API on 127.0.0.1 so the settings
page can read/write the user's override file. Uses only the Python
standard library -- no extra packages required.

Run:  python settings_server.py
(or double-click it, if .py files are associated with python.exe)

It opens your default browser automatically. Close the terminal window
(or press Ctrl+C in it) to stop the server.
"""
import json
import os
import socket
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dragon_settings as ds

_BASE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(_BASE, 'settings_ui.html')

HOST = '127.0.0.1'
PORT_RANGE = range(8765, 8775)  # try a few ports in case one is busy


def load_schema():
    return ds._load_json(ds.SCHEMA_PATH)


def load_overrides():
    return ds._load_json(ds.override_path())


def save_overrides(overrides):
    os.makedirs(ds.override_dir(), exist_ok=True)
    tmp_path = ds.override_path() + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, ds.override_path())


def compute_overrides(values, schema):
    """Given the full {script: {name: value}} map currently shown in the
    UI, returns a sparse override dict containing only the entries that
    differ from the schema default. A field left equal to its default is
    NOT stored, so it keeps tracking future default changes automatically."""
    overrides = {}
    for script, fields in values.items():
        schema_fields = schema.get(script, {})
        section = {}
        for name, value in fields.items():
            meta = schema_fields.get(name)
            default = meta.get('default') if isinstance(meta, dict) else None
            if default is not None and _values_equal(value, default):
                continue
            section[name] = value
        if section:
            overrides[script] = section
    return overrides


def _values_equal(a, b):
    if isinstance(a, list) and isinstance(b, (list, tuple)):
        return list(a) == list(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    return a == b


class Handler(BaseHTTPRequestHandler):
    server_version = 'DragonSettings/1.0'

    def log_message(self, fmt, *args):
        pass  # keep the console quiet

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text, status=200):
        body = text.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get('Content-Length', '0') or '0')
        raw = self.rfile.read(length) if length else b'{}'
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return {}

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            try:
                with open(HTML_PATH, 'r', encoding='utf-8') as f:
                    self._send_html(f.read())
            except FileNotFoundError:
                self._send_html('<h1>settings_ui.html not found</h1>', status=500)
            return

        if self.path == '/api/schema':
            self._send_json(load_schema())
            return

        if self.path == '/api/overrides':
            self._send_json(load_overrides())
            return

        if self.path == '/api/override-path':
            self._send_json({'path': ds.override_path()})
            return

        self._send_json({'error': 'not found'}, status=404)

    def do_POST(self):
        if self.path == '/api/save':
            body = self._read_json_body()
            values = body.get('values', {})
            schema = load_schema()
            overrides = compute_overrides(values, schema)
            save_overrides(overrides)
            self._send_json({'ok': True, 'overrides': overrides})
            return

        if self.path == '/api/reset-field':
            body = self._read_json_body()
            script = body.get('script')
            name = body.get('name')
            overrides = load_overrides()
            if script in overrides and name in overrides[script]:
                del overrides[script][name]
                if not overrides[script]:
                    del overrides[script]
                save_overrides(overrides)
            self._send_json({'ok': True, 'overrides': overrides})
            return

        if self.path == '/api/reset-all':
            save_overrides({})
            self._send_json({'ok': True, 'overrides': {}})
            return

        self._send_json({'error': 'not found'}, status=404)


def find_port():
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    return 0  # let the OS pick one


def main():
    port = find_port()
    server = ThreadingHTTPServer((HOST, port), Handler)
    actual_port = server.server_address[1]
    url = f'http://{HOST}:{actual_port}/'

    print('Dragon CO2 settings server')
    print(f'  Override file: {ds.override_path()}')
    print(f'  Schema file:   {ds.SCHEMA_PATH}')
    print(f'  Open:          {url}')
    print('  Press Ctrl+C to stop.')

    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping...')
        server.shutdown()


if __name__ == '__main__':
    main()
