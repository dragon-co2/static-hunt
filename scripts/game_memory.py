"""Read-only peeks into the game's memory: OpenProcess(PROCESS_VM_READ) + ReadProcessMemory —
it never writes to, injects into or suspends the game.

Conquer.exe has no ASLR, so its globals sit at fixed addresses. Found on the current client
(re-check after a game update):
    [0x008D365C]          -> the hero (the logged-in character)
    [hero + 0x0EB0]       -> the item in the arrow slot of the equipment
    item + 0x00           == 0x007FC66C   (the item object's vtable)
    item + 0x1C           =  item type    (1050002 = SpeedArrow; arrows are 1050000..1059999)
    item + 0x190          =  amount       (arrows left in the pack)"""
import ctypes
import ctypes.wintypes

from game_window import find_game_window

HERO_PTR     = 0x008D365C
ARROW_SLOT   = 0x0EB0
ITEM_VTABLE  = 0x007FC66C
ITEM_TYPE    = 0x1C
ITEM_AMOUNT  = 0x190
ARROW_TYPES  = range(1050000, 1060000)

_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
PROCESS_VM_READ, PROCESS_QUERY_INFORMATION = 0x0010, 0x0400


def _u32(handle, address):
    buf = ctypes.c_uint32()
    read = ctypes.c_size_t()
    ok = _kernel32.ReadProcessMemory(handle, ctypes.c_void_p(address), ctypes.byref(buf), 4, ctypes.byref(read))
    if not ok or read.value != 4:
        raise OSError(f'cannot read 0x{address:08x}')
    return buf.value


def _pid_of(hwnd):
    pid = ctypes.wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def equipped_arrows(hwnd=None):
    """Arrows left in the equipped pack of the game on this desktop (`hwnd`, else the one found
    on the current desktop): 0 if the arrow slot is empty. None if it can't be read — no game,
    not logged in, or the memory layout doesn't match (e.g. after a game update)."""
    hwnd = hwnd or find_game_window()
    if not hwnd:
        return None
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, _pid_of(hwnd))
    if not handle:
        return None
    try:
        hero = _u32(handle, HERO_PTR)
        if not hero:
            return None
        item = _u32(handle, hero + ARROW_SLOT)
        if not item:
            return 0                                   # nothing in the arrow slot
        if _u32(handle, item) != ITEM_VTABLE or _u32(handle, item + ITEM_TYPE) not in ARROW_TYPES:
            return None                                # not what we expect there: don't guess
        amount = _u32(handle, item + ITEM_AMOUNT)
        return amount if amount <= 100000 else None
    except OSError:
        return None
    finally:
        _kernel32.CloseHandle(handle)


if __name__ == '__main__':
    print('equipped arrows:', equipped_arrows())
