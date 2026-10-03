"""Finds the game's window on the current virtual desktop.

A window counts as the game when its process is conquer.exe, or its title is either the
character + level ("[BigW] LVL 133") or just "Conquer". Crash dialogs (WerFault.exe) and frozen
("Not Responding") windows don't count, so a crashed account reads as "no game here"."""
import ctypes
import ctypes.wintypes
import os
import re
import time

GAME_EXE = 'conquer.exe'
TITLE_RE = re.compile(r'\bLVL\s*\d+', re.IGNORECASE)
GAME_TITLE = 'conquer'  # exact title (any case), shown before a character is picked

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _title(hwnd):
    n = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _exe_name(hwnd):
    """Lower-case file name of the process that owns `hwnd`, e.g. 'conquer.exe' ('' if unknown)."""
    pid = ctypes.wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ''
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.wintypes.DWORD(len(buf))
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ''
        return os.path.basename(buf.value).lower()
    finally:
        _kernel32.CloseHandle(handle)


def _on_current_desktop(hwnd):
    try:
        from pyvda import AppView
        return AppView(hwnd=hwnd).is_on_current_desktop()
    except Exception:
        return False


def is_game_window(hwnd):
    exe = _exe_name(hwnd)
    if exe == 'werfault.exe':  # "conquer.exe has stopped working" dialog — not the game
        return False
    title = _title(hwnd)
    return exe == GAME_EXE or title.strip().lower() == GAME_TITLE or bool(TITLE_RE.search(title))


def find_game_window():
    """hwnd of a live (not frozen) game window on the current virtual desktop, or None."""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd, _):
        if (_user32.IsWindowVisible(hwnd) and is_game_window(hwnd)
                and not _user32.IsHungAppWindow(hwnd) and _on_current_desktop(hwnd)):
            found.append(hwnd)
            return False
        return True

    _user32.EnumWindows(_cb, 0)
    return found[0] if found else None


def focus_game_window():
    """Brings this desktop's game window to the front. The game ignores mouse moves and clicks
    while it isn't the active window, so call this before clicking in it. Only this desktop's
    window is used — focusing one on another desktop makes Windows switch to that desktop."""
    hwnd = find_game_window()
    if not hwnd:
        return False
    if _user32.GetForegroundWindow() != hwnd:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, 9)  # SW_RESTORE — only when minimized
        _user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
    return _user32.GetForegroundWindow() == hwnd
