import time
import threading
import ctypes
import pyautogui
from pynput import keyboard as pynput_kb

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0

CLICK_INTERVAL = 0.1  # seconds between each left click while held

BACKTICK_VK = 0xC0  # VK_OEM_3 — the ` / ~ key, physically above Tab and left of 1

_backtick_held = False
_shift_held    = False
_alt_held      = False


def _is_backtick(key):
    if getattr(key, 'vk', None) == BACKTICK_VK:
        return True
    return getattr(key, 'char', None) in ('`', '~')


def _alt_active():
    """Alt+` — plain left-click held Alt takes priority if both modifiers are down."""
    return _alt_held and _backtick_held


def _shift_active():
    """Shift+` — plain left-click, no Alt."""
    return _shift_held and _backtick_held and not _alt_held


_alt_key_sent = False  # whether we've sent a synthetic Alt keyDown that still needs a matching keyUp


def _click_loop():
    global _alt_key_sent
    was_active = False
    while True:
        alt_mode = _alt_active()
        active   = alt_mode or _shift_active()

        try:
            if alt_mode and not _alt_key_sent:
                pyautogui.keyDown('alt')
                _alt_key_sent = True
            elif not alt_mode and _alt_key_sent:
                pyautogui.keyUp('alt')
                _alt_key_sent = False

            if active:
                pyautogui.click(button='left')
        except pyautogui.FailSafeException:
            print('[AUTO-CLICK] Emergency stop!')
            if _alt_key_sent:
                pyautogui.keyUp('alt')
                _alt_key_sent = False

        if active:
            time.sleep(CLICK_INTERVAL)
        else:
            if was_active:
                print('[AUTO-CLICK] Stopped')
            time.sleep(0.02)
        was_active = active


def _announce_if_started():
    if _alt_active():
        print(f'[AUTO-CLICK] Started (Alt+Click) — every {CLICK_INTERVAL}s')
    elif _shift_active():
        print(f'[AUTO-CLICK] Started (left-click) — every {CLICK_INTERVAL}s')


def _on_key_press(key):
    global _backtick_held, _shift_held, _alt_held
    if key in (pynput_kb.Key.alt, pynput_kb.Key.alt_l, pynput_kb.Key.alt_r):
        if not _alt_held:
            _alt_held = True
            _announce_if_started()
        return
    if key in (pynput_kb.Key.shift, pynput_kb.Key.shift_l, pynput_kb.Key.shift_r):
        if not _shift_held:
            _shift_held = True
            _announce_if_started()
        return
    if _is_backtick(key) and not _backtick_held:
        _backtick_held = True
        _announce_if_started()


def _on_key_release(key):
    global _backtick_held, _shift_held, _alt_held
    if key in (pynput_kb.Key.alt, pynput_kb.Key.alt_l, pynput_kb.Key.alt_r):
        _alt_held = False
        return
    if key in (pynput_kb.Key.shift, pynput_kb.Key.shift_l, pynput_kb.Key.shift_r):
        _shift_held = False
        return
    if _is_backtick(key):
        _backtick_held = False


if __name__ == '__main__':
    print('=== Auto Left Click ===')
    print('Hold Shift+` to left-click repeatedly.')
    print('Hold Alt+` to Alt+left-click repeatedly.')
    print('Release either key in the combo to stop.')
    print('Move mouse to TOP-LEFT to emergency stop.')
    threading.Thread(target=_click_loop, daemon=True).start()
    listener = pynput_kb.Listener(on_press=_on_key_press, on_release=_on_key_release)
    listener.start()
    listener.join()
