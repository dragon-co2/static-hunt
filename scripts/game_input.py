"""Mouse + keyboard input sent to the game.

The game ignores injected input (pyautogui / SendInput / PostMessage clicks) since its update —
it only reacts to a real device. With the Interception driver installed, input is sent through
it and arrives as if from the physical mouse/keyboard. Without the driver this falls back to
pyautogui, so nothing breaks (but the game will ignore the clicks).

Driver: https://github.com/oblitum/Interception — run `install-interception.exe /install`
as Administrator, then restart Windows."""
import ctypes
import ctypes.wintypes
import os
import time

import pyautogui

from _paths import app_root

INSTALLER = os.path.join(app_root(), 'apps', 'interception', 'install-interception.exe')
INSTALL_BAT = os.path.join(app_root(), 'install_driver.bat')  # runs INSTALLER, then offers a restart
_DRIVER_FILES = [os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'drivers', f)
                 for f in ('keyboard.sys', 'mouse.sys')]

DRIVER = False
try:
    import interception
    if interception.inputs._g_context.valid:
        interception.auto_capture_devices(keyboard=True, mouse=True)  # find the working device numbers
        DRIVER = True
except Exception as e:  # library missing / failed: fall back to pyautogui
    print(f'[INPUT] Interception library failed ({type(e).__name__}: {e})')

print('[INPUT] using the Interception driver' if DRIVER
      else '[INPUT] Interception driver not active — using pyautogui (the game will ignore clicks)')


def _driver_files_present():
    return all(os.path.exists(f) for f in _DRIVER_FILES)


def _message_box(text, title, flags):
    MB_TOPMOST, MB_SETFOREGROUND = 0x40000, 0x10000
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags | MB_TOPMOST | MB_SETFOREGROUND)


def _run_installer_as_admin():
    """Runs install_driver.bat as administrator (one UAC prompt) and waits for it; the bat
    installs the driver and asks whether to restart now. Falls back to running the installer
    exe directly if the bat is missing. Returns True if it finished with exit code 0."""
    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.wintypes.DWORD), ('fMask', ctypes.c_ulong),
                    ('hwnd', ctypes.wintypes.HWND), ('lpVerb', ctypes.wintypes.LPCWSTR),
                    ('lpFile', ctypes.wintypes.LPCWSTR), ('lpParameters', ctypes.wintypes.LPCWSTR),
                    ('lpDirectory', ctypes.wintypes.LPCWSTR), ('nShow', ctypes.c_int),
                    ('hInstApp', ctypes.wintypes.HINSTANCE), ('lpIDList', ctypes.c_void_p),
                    ('lpClass', ctypes.wintypes.LPCWSTR), ('hkeyClass', ctypes.wintypes.HKEY),
                    ('dwHotKey', ctypes.wintypes.DWORD), ('hIcon', ctypes.wintypes.HANDLE),
                    ('hProcess', ctypes.wintypes.HANDLE)]

    SEE_MASK_NOCLOSEPROCESS, SW_SHOW = 0x40, 5
    info = SHELLEXECUTEINFO(cbSize=ctypes.sizeof(SHELLEXECUTEINFO), fMask=SEE_MASK_NOCLOSEPROCESS,
                            lpVerb='runas',
                            lpFile=INSTALL_BAT if os.path.exists(INSTALL_BAT) else INSTALLER,
                            lpParameters=None if os.path.exists(INSTALL_BAT) else '/install',
                            lpDirectory=app_root(), nShow=SW_SHOW)
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return False  # UAC prompt declined, or the installer couldn't start
    ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = ctypes.wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    return code.value == 0


PROMPT_TIMEOUT = 30  # seconds before the install question closes itself (treated as "No")


def _ask_yes_no(text, title):
    """Yes/No box that closes itself after PROMPT_TIMEOUT seconds, so an unattended start never
    hangs on it. Returns True only for an explicit Yes."""
    MB_YESNO, MB_ICONQUESTION, MB_TOPMOST, MB_SETFOREGROUND, IDYES = 0x4, 0x20, 0x40000, 0x10000, 6
    flags = MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND
    try:  # MessageBoxTimeoutW: same as MessageBoxW plus a timeout in ms (returns 32000 on timeout)
        answer = ctypes.windll.user32.MessageBoxTimeoutW(None, text, title, flags, 0, PROMPT_TIMEOUT * 1000)
    except AttributeError:
        answer = _message_box(text, title, MB_YESNO | MB_ICONQUESTION)
    return answer == IDYES


def ensure_driver():
    """Called once when static.py starts. If the Interception driver isn't active, offers to
    install it (UAC prompt; the install bat then offers a restart). Never stops the script:
    whatever happens, it carries on — without the driver the game just ignores the clicks."""
    if DRIVER:
        return True
    if _driver_files_present():
        print('[INPUT] driver is installed but not running yet — restart Windows to activate it')
        return True
    if not os.path.exists(INSTALLER):
        print(f'[INPUT] installer not found: {INSTALLER}')
        return True
    if not _ask_yes_no('The game only accepts clicks from a real mouse. The Interception '
                       'driver makes the script\'s clicks count as real ones.\n\n'
                       'Install it now? (needs administrator rights and a restart)\n\n'
                       f'This closes by itself in {PROMPT_TIMEOUT} seconds.',
                       'Dragon CO2 — input driver'):
        print('[INPUT] driver install skipped — continuing without it (the game will ignore clicks)')
        return True
    if not _run_installer_as_admin():
        print('[INPUT] driver install did not complete (UAC declined or installer failed)')
        return True
    print('[INPUT] driver installed — restart Windows to activate it; continuing for now')
    return True


def move(x, y):
    """Moves the cursor to (x, y) on screen."""
    if DRIVER:
        interception.move_to(int(x), int(y))
    else:
        pyautogui.moveTo(x, y, duration=0.05)


def down(button='left'):
    if DRIVER:
        interception.mouse_down(button)
    else:
        pyautogui.mouseDown(button=button)


def up(button='left'):
    if DRIVER:
        interception.mouse_up(button)
    else:
        pyautogui.mouseUp(button=button)


def click_here(button='left', hold=0.1):
    """Press + hold + release at the current cursor position."""
    down(button)
    time.sleep(hold)
    up(button)


def click(x, y, button='left', hold=0.1):
    """Moves to (x, y), then clicks with `hold` seconds between press and release."""
    move(x, y)
    time.sleep(0.05)
    click_here(button, hold)


# Keyboard: the game reads keys by hardware scan code. pyautogui sends only virtual-key codes
# (ignored), and keys sent through the Interception driver were ignored too — but SendInput with
# KEYEVENTF_SCANCODE works (tested: Alt+P opens the bank). So keys always go this way.
_NAMED_VK = {'alt': 0x12, 'shift': 0x10, 'ctrl': 0x11, 'esc': 0x1B, 'enter': 0x0D, 'backspace': 0x08,
             'tab': 0x09, 'space': 0x20}
_KEYEVENTF_KEYUP, _KEYEVENTF_SCANCODE, _INPUT_KEYBOARD = 0x0002, 0x0008, 1


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', ctypes.wintypes.WORD), ('wScan', ctypes.wintypes.WORD),
                ('dwFlags', ctypes.wintypes.DWORD), ('time', ctypes.wintypes.DWORD),
                ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]


class _MOUSEINPUT(ctypes.Structure):  # only here so the union has the full INPUT size
    _fields_ = [('dx', ctypes.wintypes.LONG), ('dy', ctypes.wintypes.LONG),
                ('mouseData', ctypes.wintypes.DWORD), ('dwFlags', ctypes.wintypes.DWORD),
                ('time', ctypes.wintypes.DWORD), ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('ki', _KEYBDINPUT), ('mi', _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.wintypes.DWORD), ('u', _INPUTUNION)]


def _scan_code(key):
    vk = _NAMED_VK.get(key.lower())
    if vk is None and len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())  # A-Z / 0-9 virtual-key codes are their ASCII codes, whatever the layout
    if vk is None:
        raise ValueError(f'unknown key: {key!r}')
    return ctypes.windll.user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC


def _send_scan(scan, up):
    flags = _KEYEVENTF_SCANCODE | (_KEYEVENTF_KEYUP if up else 0)
    inp = _INPUT(type=_INPUT_KEYBOARD, u=_INPUTUNION(ki=_KEYBDINPUT(0, scan, flags, 0, None)))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _send_key(key, up):
    _send_scan(_scan_code(key), up)


_US_LAYOUT = None


def _us_layout():
    """HKL of the US English layout (loaded, not activated) — maps characters to physical keys
    the same way whatever layout the PC is set to (e.g. Arabic)."""
    global _US_LAYOUT
    if _US_LAYOUT is None:
        _US_LAYOUT = ctypes.windll.user32.LoadKeyboardLayoutW('00000409', 0)
    return _US_LAYOUT


def use_english_layout(hwnd):
    """Asks the window to switch its input language to US English, so the scan codes typed into
    it come out as Latin letters, not as the keys' Arabic (or other) characters."""
    WM_INPUTLANGCHANGEREQUEST = 0x0050
    ctypes.windll.user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, _us_layout())


def type_text(text, delay=0.04, secret=False):
    """Types `text` key by key as hardware scan codes (US layout; Shift for capitals and symbols).
    Characters a US keyboard can't type are skipped with a warning — for a secret (password) the
    character itself is never printed, only its position. Returns how many characters were typed."""
    hkl = _us_layout()
    typed = 0
    for pos, ch in enumerate(text, 1):
        res = ctypes.windll.user32.VkKeyScanExW(ord(ch), hkl)
        if res == -1 or (res & 0xFFFF) == 0xFFFF:
            what = f'character #{pos}' if secret else f'character {ch!r}'
            print(f'[INPUT] cannot type {what} on a US keyboard — skipped')
            continue
        vk, shift = res & 0xFF, bool(res & 0x100)
        scan = ctypes.windll.user32.MapVirtualKeyExW(vk, 0, hkl)  # MAPVK_VK_TO_VSC
        if shift:
            key_down('shift')
        _send_scan(scan, up=False)
        time.sleep(0.02)
        _send_scan(scan, up=True)
        if shift:
            key_up('shift')
        typed += 1
        time.sleep(delay)
    return typed


def press(key, times=1, delay=0.03):
    """Taps a named key (e.g. 'backspace', 'enter') `times` times."""
    for _ in range(times):
        key_down(key)
        time.sleep(0.02)
        key_up(key)
        time.sleep(delay)


def key_down(key):
    _send_key(key, up=False)


def key_up(key):
    _send_key(key, up=True)
