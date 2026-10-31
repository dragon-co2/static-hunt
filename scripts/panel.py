"""The control panel static.py opens: one window (pywebview, like the settings app) listing the
accounts - for each, what it does (Static, + Auto hunt, Monk hunt, Auto heal), the options of
that, and the log. Its console is hidden once the window shows: the log is read here instead.

static.py hands it two functions - state() -> dict for the page, act(body) -> dict for a click -
(and get(path, query) -> (content type, bytes) or None, for any other /api/ GET) and calls run(),
which returns when the window is closed. Lines printed anywhere go to push()
(static.py's _Tee) and the page reads them from /api/log."""
import collections
import ctypes
import json
import os
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

_BASE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(_BASE, 'panel_ui.html')
HOST = '127.0.0.1'
PORT_RANGE = range(8775, 8785)          # the settings app uses 8765-8774
TITLE = 'Dragon CO2'
SIZE = (1180, 760)
MIN_SIZE = (900, 600)
LOG_LINES = 4000                        # lines the page can scroll back through
ICON = os.path.join(os.path.dirname(_BASE), 'app_icon.ico')   # the window's and the taskbar's icon
APP_ID = 'DragonCO2.Panel'              # its own taskbar button (not python's) - with ICON on it

_log = collections.deque(maxlen=LOG_LINES)   # (number, text)
_log_n = [0]
_log_lock = threading.Lock()


def push(line):
    """A printed line - for the Log tab."""
    with _log_lock:
        _log_n[0] += 1
        _log.append((_log_n[0], line))


def _log_since(n):
    with _log_lock:
        return [[i, text] for i, text in _log if i > n], _log_n[0]


def _handler(state, act, get=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, body, kind='application/json; charset=utf-8'):
            data = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, _, query = self.path.partition('?')
            if path in ('/', '/index.html'):
                with open(HTML_PATH, encoding='utf-8') as f:
                    self._send(f.read(), 'text/html; charset=utf-8')
            elif path == '/api/state':
                self._send(json.dumps(state(), ensure_ascii=False))
            elif path == '/api/log':
                since = int(dict(p.split('=', 1) for p in query.split('&') if '=' in p).get('since', 0))
                lines, last = _log_since(since)
                self._send(json.dumps({'lines': lines, 'last': last}, ensure_ascii=False))
            else:
                params = {k: unquote(v) for k, _, v in (p.partition('=') for p in query.split('&')) if k}
                try:
                    found = get and get(path, params)
                except Exception as e:
                    print(f'[PANEL] {path}: {type(e).__name__}: {e}')
                    found = ('application/json', json.dumps({'error': f'{type(e).__name__}: {e}'}))
                if found:
                    self._send(found[1], found[0])
                else:
                    self.send_error(404)

        def do_POST(self):
            if self.path != '/api/do':
                self.send_error(404)
                return
            length = int(self.headers.get('Content-Length') or 0)
            try:
                body = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
            except ValueError:
                body = {}
            try:
                result = act(body) or {'ok': True}
            except Exception as e:                       # a click never takes the panel down
                print(f'[PANEL] {body.get("action")}: {type(e).__name__}: {e}')
                result = {'ok': False, 'message': f'{type(e).__name__}: {e}'}
            self._send(json.dumps(result, ensure_ascii=False))
    return Handler


def _port():
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    return 0


def hide_console(hide=True):
    """Hides (or shows again) this process's console window - if it has one."""
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0 if hide else 5)


def _set_icon(timeout=15):
    """ICON on the panel's window (title bar, Alt+Tab, taskbar) - once it's there."""
    if not os.path.exists(ICON):
        return
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p)
    end = time.time() + timeout
    while time.time() < end:
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            for which, size in ((0, 16), (1, 32)):              # ICON_SMALL, ICON_BIG
                icon = user32.LoadImageW(None, ICON, 1, size, size, 0x10)   # IMAGE_ICON, LR_LOADFROMFILE
                if icon:
                    user32.SendMessageW(hwnd, 0x80, which, icon)           # WM_SETICON
            return
        time.sleep(0.2)


def _shown():
    """Runs once the window is up: its icon, and no console."""
    _set_icon()
    hide_console()


def run(state, act, get=None):
    """Serves the page and shows it in its own window; returns once that window is closed. No
    pywebview: the page opens in the browser and this never returns (Ctrl+Shift+C stops)."""
    server = ThreadingHTTPServer((HOST, _port()), _handler(state, act, get))
    threading.Thread(target=server.serve_forever, daemon=True, name='panel-http').start()
    url = f'http://{HOST}:{server.server_address[1]}/'
    print(f'[PANEL] {url}')
    try:
        import webview
    except ImportError:
        print('[PANEL] pywebview is not installed - the panel opens in the browser, the console stays')
        webbrowser.open(url)
        threading.Event().wait()
        return
    try:                                                # a taskbar button of its own, not python's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass
    webview.create_window(TITLE, url, width=SIZE[0], height=SIZE[1], min_size=MIN_SIZE)
    try:
        webview.start(_shown, icon=ICON if os.path.exists(ICON) else None)
    finally:
        hide_console(False)
        server.shutdown()
