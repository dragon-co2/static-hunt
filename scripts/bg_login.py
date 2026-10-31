"""Logs an account in, in the background - no window in front, no keys, no mouse - through the
login screen's own auto-login (version.dll, the server's login overlay):

  1. write %LOCALAPPDATA%\\DragonConquer\\AutoLogin_<n>.pending:
         Username=<user>
         Password=DP:<hex>      the password DPAPI-encrypted the way the overlay reads it (its own
                                16-byte entropy, this Windows user only) - never written in plain
         Server=0
  2. start the launcher (GAME_PATH, Play.exe): it pulls any update, then PLAY opens the game.
     (Starting data2\\Conquer.exe directly doesn't work: the client closes without the launcher's
     set-up, e.g. its CL_LP environment variable.)
  3. the new client claims the file (renames it, reads it, deletes it) and signs in on its own

Run on its own to test:   python bg_login.py [account number]   (default: the first filled-in one)"""
import ctypes
import ctypes.wintypes
import os
import struct
import subprocess
import sys
import time

import cv2
import numpy as np

import accounts
import game_memory
from _paths import app_root

_PLAY = cv2.imread(os.path.join(app_root(), 'reference_images', 'play.jpg'), cv2.IMREAD_GRAYSCALE)
CONF_PLAY      = 0.8
PLAY_SCALES    = (1, .95, .9, .85, .8, .75, .7, 1.1, 1.25, 1.5, 1.75, 2)   # PLAY's sizes tried: the
                                   # launcher shrinks on a small screen, grows with display scale
WAIT_PLAY      = 60                # seconds for the launcher's PLAY (it checks for updates first)
CLIENT_ENTROPY = b'\x8f,Q\xa6\r\xb3t\xe9"\x9bF\xc1X\xea7\xd0'   # version.dll 0x100C672C
SERVER         = 0                 # Server= (every account saved by the overlay uses 0)
FOLDER         = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'DragonConquer')
WAIT_IN_GAME   = 120               # seconds for the new client to be in the game
LOGIN_TRIES    = 3                 # login_retry: attempts per account
ORPHAN_AGE     = 60                # close_left_behind: a game this old with no character is left over
LOGIN_ERROR    = os.path.join('CLAuto', 'LoginError.txt')   # next to the launcher: why the server
                                                            # refused a sign-in (e.g. a 5th account)


class _BLOB(ctypes.Structure):
    _fields_ = [('cbData', ctypes.wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _client_password(password):
    """'DP:<hex>' - the password as the overlay's auto-login reads it (CryptProtectData with its
    entropy, CRYPTPROTECT_UI_FORBIDDEN)."""
    src, _keep = _blob(password.encode('utf-8'))
    entropy, _keep2 = _blob(CLIENT_ENTROPY)
    dst = _BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), None, ctypes.byref(entropy),
                                                   None, None, 1, ctypes.byref(dst)):
        raise OSError('CryptProtectData failed')
    try:
        return 'DP:' + ctypes.string_at(dst.pbData, dst.cbData).hex().upper()
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def _pending_file():
    """A free AutoLogin_<n>.pending path (the client looks from 1 up)."""
    os.makedirs(FOLDER, exist_ok=True)
    n = 1
    while os.path.exists(os.path.join(FOLDER, f'AutoLogin_{n}.pending')):
        n += 1
    return os.path.join(FOLDER, f'AutoLogin_{n}.pending')


def _exe_of(pid):
    """The lower-case file name of process `pid`'s exe, or ''."""
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ''
    try:
        buf, size = ctypes.create_unicode_buffer(520), ctypes.wintypes.DWORD(520)
        ok = k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value).lower() if ok else ''
    finally:
        k32.CloseHandle(handle)


def _windows_of(pid, exe=''):
    """Visible top-level windows of process `pid` - or of any process run from `exe` (a launcher
    that starts itself again, e.g. as administrator, has another pid) - biggest first."""
    user32 = ctypes.windll.user32
    found = []
    cb = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def _each(hwnd, _):
        owner = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if (owner.value == pid or exe and _exe_of(owner.value) == exe) and user32.IsWindowVisible(hwnd):
            r = ctypes.wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(r))
            found.append((r.right * r.bottom, hwnd))
        return True

    user32.EnumWindows(cb(_each), 0)
    return [h for _, h in sorted(found, reverse=True)]


def _capture(hwnd):
    """The window's client area as a gray image (PrintWindow: works behind other windows and on
    other desktops), or None."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    r = ctypes.wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right, r.bottom
    if w <= 0 or h <= 0:
        return None
    wdc = user32.GetDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(wdc)
    bmp = gdi32.CreateCompatibleBitmap(wdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    try:
        if not user32.PrintWindow(hwnd, mdc, 3):         # PW_CLIENTONLY | PW_RENDERFULLCONTENT
            return None
        header = struct.pack('<IiiHHIIiiII', 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.create_string_buffer(header, 44), 0)
        return cv2.cvtColor(np.frombuffer(buf, np.uint8).reshape(h, w, 4), cv2.COLOR_BGRA2GRAY)
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, wdc)


def _find_play(gray):
    """(score, x, y) of the best PLAY match in `gray` (its centre), at the sizes PLAY_SCALES -
    the launcher is drawn bigger with Windows' display scale above 100%."""
    best = (0.0, 0, 0)
    for scale in PLAY_SCALES:
        tpl = _PLAY if scale == 1 else cv2.resize(_PLAY, None, fx=scale, fy=scale)
        if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
            continue
        _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED))
        if score > best[0]:
            best = (score, x + tpl.shape[1] // 2, y + tpl.shape[0] // 2)
            if score >= CONF_PLAY:                      # good enough - the other sizes skipped
                break
    return best


def press_play(launcher_pid, timeout=WAIT_PLAY):
    """Clicks the launcher's PLAY in the background: finds it in a PrintWindow capture of the
    launcher (reference_images/play.jpg) and posts the click to that window - no real mouse, no
    window in front. True once clicked."""
    exe = _exe_of(launcher_pid)
    end = time.time() + timeout
    told = time.time()
    best, windows = 0.0, 0
    while time.time() < end:
        hwnds = _windows_of(launcher_pid, exe)
        windows = len(hwnds)
        for hwnd in hwnds:
            gray = _capture(hwnd)
            if gray is None:
                continue
            score, cx, cy = _find_play(gray)
            best = max(best, score)
            if score >= CONF_PLAY:
                at = cy << 16 | cx
                post = ctypes.windll.user32.PostMessageW
                post(hwnd, 0x200, 0, at)                    # WM_MOUSEMOVE (hover first)
                time.sleep(0.1)
                down = post(hwnd, 0x201, 1, at)             # WM_LBUTTONDOWN
                time.sleep(0.05)
                post(hwnd, 0x202, 0, at)                    # WM_LBUTTONUP
                if not down:                                # 5: an administrator's window
                    print(f'[BG LOGIN] PLAY found but the click was refused (error '
                          f'{ctypes.GetLastError()}) - the launcher runs as administrator: run '
                          f'this as administrator too, or untick "Run as administrator" on it')
                    return False
                print(f'[BG LOGIN] PLAY clicked (match {score:.2f})')
                return True
        if time.time() - told >= 15:
            told = time.time()
            print(f'[BG LOGIN] waiting for PLAY: {windows} launcher window(s), best match {best:.2f}'
                  f' (needs {CONF_PLAY})')
        time.sleep(1)
    print(f'[BG LOGIN] the launcher\'s PLAY button did not show up within {timeout}s ('
          + (f'best match {best:.2f}, needs {CONF_PLAY})' if windows else
             f'no window of the launcher{" " + exe if exe else ""} found)'))
    return False


def prepare(account):
    """Writes `account`'s auto-login file: the next client to open signs it in. Returns its path."""
    path = _pending_file()
    text = f'Username={account.username}\nPassword={_client_password(account.password)}\nServer={SERVER}\n'
    with open(path + '.tmp', 'w', encoding='ascii') as f:     # whole file at once: the client
        f.write(text)                                          # never sees half of it
    os.replace(path + '.tmp', path)
    print(f'[BG LOGIN] {account.username}: auto-login file {os.path.basename(path)} written')
    return path


def login_retry(account, tries=LOGIN_TRIES, others=()):
    """login(), again when it fails - the client sometimes crashes in version.dll as it starts."""
    for n in range(1, tries + 1):
        close_left_behind()
        pid = login(account, others=others)
        if pid:
            close_left_behind(keep=[pid])
            return pid
        print(f'[BG LOGIN] {account.username}: attempt {n}/{tries} failed' + (' - trying again' if n < tries else ''))
    return None


def _login_error(since):
    """' - <the game's reason>' if the client wrote CLAuto\\LoginError.txt after `since`, else ''."""
    launcher = accounts.game_path()
    path = os.path.join(os.path.dirname(launcher), LOGIN_ERROR) if launcher else ''
    try:
        if path and os.path.getmtime(path) >= since - 1:
            with open(path, encoding='utf-8', errors='replace') as f:
                reason = f.read().strip()
            return f' - the game says: {reason}' if reason else ''
    except OSError:
        pass
    return ' (crashed while loading?)'


def close_left_behind(keep=()):
    """Closes the games left on the login screen (no character, over ORPHAN_AGE seconds old) - one
    whose character dropped out reopens itself there, and stays there once the account is logged in
    from a new launcher: only RAM. Called between logins (one at a time), so none of these is a
    login in progress."""
    import travel
    left = [p for p in set(game_memory.game_pids()) - set(keep)
            if not game_memory._name_pid(p) and travel._age(p) > ORPHAN_AGE]
    if not left:
        return
    time.sleep(5)                         # still no character: not just a map change
    for pid in left:
        if pid in game_memory.game_pids() and not game_memory._name_pid(pid):
            subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True)
            print(f'[BG LOGIN] closed game {pid} - left on the login screen')


def _discard(path):
    """Deletes the auto-login file - if the client hasn't claimed (and deleted) it meanwhile."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def login(account, start=True, others=()):
    """Gets a client to sign `account` in on its own: writes its auto-login file, starts the
    launcher (start=True; it checks for updates, then PLAY opens the game) and waits for a new
    client's character to be in the game. A new game whose character is in `others` (the other
    accounts' characters) is another account's, opened meanwhile - never taken for this one.
    Returns that game's pid, or None."""
    others = {o.lower() for o in others if o}
    path = prepare(account)
    before = set(game_memory.game_pids())
    if start:
        launcher = accounts.game_path()
        if not launcher:
            print('[BG LOGIN] GAME_PATH is not set - cannot start the launcher')
            _discard(path)
            return None
        proc = subprocess.Popen([launcher], cwd=os.path.dirname(launcher))
        print(f'[BG LOGIN] {account.username}: launcher started - pressing PLAY when it shows')
        if not press_play(proc.pid):
            _discard(path)
            return None
    started = time.time()
    end = started + WAIT_IN_GAME
    seen = set()                          # the new clients (and their crash handlers) seen so far
    try:
        while time.time() < end:
            new = set(game_memory.game_pids()) - before
            for pid in sorted(new):
                name = game_memory._name_pid(pid)
                if name and name.lower() in others:
                    print(f'[BG LOGIN] {account.username}: the new game {pid} is {name} - '
                          f"another account's, not this one")
                    before.add(pid)
                elif name:
                    print(f'[BG LOGIN] {account.username}: in the game as {name} (pid {pid})')
                    return pid
            new -= before
            seen |= new
            if seen and not new:          # its client opened and is gone: crashed or refused -
                time.sleep(1)             # no point waiting the rest of WAIT_IN_GAME
                if not set(game_memory.game_pids()) - before:
                    print(f'[BG LOGIN] {account.username}: its game closed after '
                          f'{time.time() - started:.0f}s' + _login_error(started))
                    return None
            time.sleep(1)
        print(f'[BG LOGIN] {account.username}: not in the game after {WAIT_IN_GAME}s'
              + (' - the client never took the auto-login file' if os.path.exists(path) else ''))
        for pid in set(game_memory.game_pids()) - before:  # its client, stuck before the world:
            if not game_memory._name_pid(pid):              # closed, or it would take the next
                subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True)   # try's file
                print(f'[BG LOGIN] {account.username}: closed its stuck game {pid}')
        return None
    finally:
        _discard(path)                    # not claimed: don't leave the password lying around


if __name__ == '__main__':
    accts = accounts.load()
    if not accts:
        sys.exit('[BG LOGIN] no accounts - turn on Accounts in the settings and fill one in')
    pick = accts[0]
    if len(sys.argv) > 1:
        pick = next((a for a in accts if a.slot == int(sys.argv[1])), None)
        if not pick:
            sys.exit(f'[BG LOGIN] account {sys.argv[1]} is not filled in / enabled')
    sys.exit(0 if login_retry(pick) else 1)
