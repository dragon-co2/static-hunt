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


def ensure_driver():
    """Called once when static.py starts. If the Interception driver isn't active, offers to
    install it (UAC prompt) and asks for a restart. Returns True to keep running, False if the
    script should exit so the user can restart Windows first."""
    if DRIVER:
        return True
    MB_OK, MB_YESNO, MB_ICONQUESTION, MB_ICONINFO, IDYES = 0x0, 0x4, 0x20, 0x40, 6
    title = 'Dragon CO2 — input driver'
    if _driver_files_present():
        _message_box('The Interception driver is installed but not running yet.\n\n'
                     'Restart Windows, then run the script again.', title, MB_OK | MB_ICONINFO)
        return False
    if not os.path.exists(INSTALLER):
        print(f'[INPUT] installer not found: {INSTALLER}')
        return True
    answer = _message_box('The game only accepts clicks from a real mouse. The Interception '
                          'driver makes the script\'s clicks count as real ones.\n\n'
                          'Install it now? (needs administrator rights and a restart)',
                          title, MB_YESNO | MB_ICONQUESTION)
    if answer != IDYES:
        print('[INPUT] driver install declined — continuing without it (the game will ignore clicks)')
        return True
    if not _run_installer_as_admin():
        print('[INPUT] driver install did not complete (UAC declined or installer failed)')
        _message_box('The driver was not installed.\n\nThe script will keep running, but the game '
                     'will ignore its clicks.', title, MB_OK | MB_ICONINFO)
        return True
    # the bat already offered to restart; if the user said no, just stop here
    print('[INPUT] driver installed — restart Windows, then run the script again')
    return False


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


def key_down(key):
    if DRIVER:
        interception.key_down(key, 0)
    else:
        pyautogui.keyDown(key)


def key_up(key):
    if DRIVER:
        interception.key_up(key, 0)
    else:
        pyautogui.keyUp(key)
