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
    # Fixed buffer + GetWindowTextW only: for another process's window this reads the cached
    # caption without messaging it. GetWindowTextLengthW messages the window, and waits forever
    # if that window is busy or frozen.
    buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, buf, 512)
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
        import desktops
        return desktops.on_current_desktop(hwnd)
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
    candidates = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd, _):
        if (_user32.IsWindowVisible(hwnd) and not _user32.IsHungAppWindow(hwnd)
                and is_game_window(hwnd)):
            candidates.append(hwnd)
        return True

    _user32.EnumWindows(_cb, 0)
    # the virtual-desktop (COM) check runs after enumeration, not inside the Windows callback
    for hwnd in candidates:
        if _on_current_desktop(hwnd):
            return hwnd
    return None


def focus_game_window():
    """Brings this desktop's game window to the front. The game ignores mouse moves and clicks
    while it isn't the active window, so call this before clicking in it. Only this desktop's
    window is used — focusing one on another desktop makes Windows switch to that desktop."""
    hwnd = find_game_window()
    if not hwnd:
        return False
    if _user32.GetForegroundWindow() == hwnd:
        return True
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, 9)  # SW_RESTORE — only when minimized
    _force_foreground(hwnd)
    return _user32.GetForegroundWindow() == hwnd


def _force_foreground(hwnd):
    """Windows refuses SetForegroundWindow from a program that isn't the one in front (focus
    stealing protection), so a plain call silently fails while e.g. the console or VS Code has
    focus. Attach to the foreground window's input first, which makes the switch allowed; if that
    still fails, tap Alt (an input event of our own also unlocks it) and try again."""
    if _user32.SetForegroundWindow(hwnd) and _user32.GetForegroundWindow() == hwnd:
        return
    fg = _user32.GetForegroundWindow()
    fg_thread = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
    me = _kernel32.GetCurrentThreadId()
    attached = bool(fg_thread and fg_thread != me and _user32.AttachThreadInput(me, fg_thread, True))
    try:
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(me, fg_thread, False)
    time.sleep(0.1)
    if _user32.GetForegroundWindow() != hwnd:
        VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
        _user32.keybd_event(VK_MENU, 0, 0, 0)
        _user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        _user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
