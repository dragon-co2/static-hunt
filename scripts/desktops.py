"""Virtual desktops (pyvda) that survive Explorer restarting.

pyvda talks to the virtual-desktop service inside explorer.exe and keeps that connection for
the whole run. When Explorer restarts (crash, session switch, RDP connect/disconnect) every
call fails with COMError 0x800706BE "The remote procedure call failed" — forever, because the
old connection is dead. safe() catches that, waits, opens a fresh connection and tries again;
if the desktops still don't answer it gives up on that one step instead of crashing the script."""
import time

import _ctypes
import pyvda.pyvda as _pyvda
from pyvda import AppView, VirtualDesktop, get_virtual_desktops
from pyvda.utils import Managers

RETRIES    = 10   # attempts per step (~RETRIES * RETRY_WAIT seconds before giving up on it)
RETRY_WAIT = 5    # seconds between attempts — gives Explorer time to come back

# COM errors meaning "the Explorer at the other end is gone" (anything else is a normal error)
_DEAD = {
    -2147023170,  # 0x800706BE RPC_S_CALL_FAILED     "The remote procedure call failed"
    -2147023174,  # 0x800706BA RPC_S_SERVER_UNAVAILABLE
    -2147417848,  # 0x80010108 RPC_E_DISCONNECTED    "The object invoked has disconnected"
}


def _reconnect():
    """Replaces pyvda's cached COM objects with fresh ones from the running Explorer."""
    try:
        _pyvda.managers = Managers()
        return True
    except Exception as e:   # Explorer not back yet (pyvda raises NotImplementedError then)
        print(f'  [DESKTOP] reconnect failed ({type(e).__name__}) — Explorer not ready yet')
        return False


def safe(fn, what, default=None):
    """Runs fn(); if Explorer stopped answering, reconnects and retries. Returns `default`
    if it still fails after RETRIES attempts."""
    for attempt in range(1, RETRIES + 1):
        try:
            return fn()
        except _ctypes.COMError as e:
            if e.args[0] not in _DEAD:
                raise
            reason = e.args[1] if len(e.args) > 1 else e
            print(f'  [DESKTOP] virtual desktops not answering while {what} ({reason}) — '
                  f'reconnecting in {RETRY_WAIT}s ({attempt}/{RETRIES})')
            time.sleep(RETRY_WAIT)
            _reconnect()
    print(f'  [DESKTOP] virtual desktops still not answering — {what} skipped')
    return default


def count(default=1):
    return safe(lambda: len(get_virtual_desktops()), 'counting desktops', default)


def current(default=None):
    """1-based number of the current desktop (or `default`)."""
    return safe(lambda: VirtualDesktop.current().number, 'reading the current desktop', default)


def go(number):
    """Switches to desktop `number` (1-based). False if it couldn't."""
    return safe(lambda: VirtualDesktop(number).go() or True, f'switching to desktop {number}', False)


def on_current_desktop(hwnd):
    return safe(lambda: AppView(hwnd=hwnd).is_on_current_desktop(),
                'checking a window\'s desktop', False)


def create():
    return safe(lambda: VirtualDesktop.create() or True, 'creating a desktop', False)


def remove(number):
    """Removes desktop `number`; its windows move to desktop 1."""
    return safe(lambda: VirtualDesktop(number).remove(fallback=VirtualDesktop(1)) or True,
                f'removing desktop {number}', False)
