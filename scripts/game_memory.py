"""Read-only peeks into the game's memory: OpenProcess(PROCESS_VM_READ) + ReadProcessMemory —
it never writes to, injects into or suspends the game.

Conquer.exe has no ASLR, so its globals sit at fixed addresses. Found on the current client
(re-check after a game update):
    [0x008D365C]          -> the hero (the logged-in character)
    [hero + 0x0EB0]       -> the item in the arrow slot of the equipment
    item + 0x00           == 0x007FC66C   (the item object's vtable)
    item + 0x1C           =  item type    (1050002 = SpeedArrow; arrows are 1050000..1059999)
    item + 0x190          =  amount       (uint16: arrows left in the pack; the 2 bytes after it are
                                           unrelated, so never read it as a 32-bit number)
    [hero + 0x15C]        -> the character's name (16 bytes, cp1252)"""
import ctypes
import ctypes.wintypes
import struct
import time

import numpy as np

from game_window import find_game_window

HERO_PTR     = 0x008D365C
ARROW_SLOT   = 0x0EB0
ITEM_VTABLE  = 0x007FC66C
ITEM_TYPE    = 0x1C
ITEM_AMOUNT  = 0x190
ARROW_TYPES  = range(1050000, 1060000)
HERO_NAME    = 0x15C

_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
PROCESS_VM_READ, PROCESS_QUERY_INFORMATION = 0x0010, 0x0400
last_error = ''   # why the last equipped_arrows() call returned None (for the log)


class _MBI(ctypes.Structure):   # MEMORY_BASIC_INFORMATION (this 64-bit process's layout)
    _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p),
                ('AllocationProtect', ctypes.wintypes.DWORD), ('PartitionId', ctypes.wintypes.WORD),
                ('RegionSize', ctypes.c_size_t), ('State', ctypes.wintypes.DWORD),
                ('Protect', ctypes.wintypes.DWORD), ('Type', ctypes.wintypes.DWORD)]


def _chunks(handle, chunk=0x400000):
    """(address, bytes) of every committed, readable, writable (heap / data) region of the game."""
    mbi = _MBI()
    addr = 0x10000
    while addr < 0x7FFF0000:
        if not _kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        base, size = mbi.BaseAddress or 0, mbi.RegionSize
        if size == 0:
            break
        if mbi.State == 0x1000 and not mbi.Protect & 0x101 and mbi.Protect & 0x44:   # COMMIT, RW / RWX
            for off in range(0, size, chunk):
                n = min(chunk, size - off)
                buf = ctypes.create_string_buffer(n)
                read = ctypes.c_size_t()
                if _kernel32.ReadProcessMemory(handle, ctypes.c_void_p(base + off), buf, n, ctypes.byref(read)):
                    yield base + off, buf.raw[:read.value]
        addr = base + size


def imgui_window(hwnd, name_part):
    """(x, y, w, h) of the Dear ImGui window whose name contains `name_part` (e.g. '##hubLogin'),
    in the client coordinates of the window it's drawn on - read from the game's memory, or None.
    An ImGuiWindow keeps its name pointer near its start and its Pos / Size (4 floats) 0x14
    bytes after that pointer. ImGui never frees old windows, so check the screen still shows it."""
    if not hwnd:
        return None
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, _pid_of(hwnd))
    if not handle:
        return None
    try:
        needle = name_part.encode('ascii')
        names = set()
        for base, data in _chunks(handle):
            at = data.find(needle)
            while at != -1 and len(names) < 64:
                start = data.rfind(b'\0', 0, at) + 1          # the whole name string
                names.add(base + start)
                at = data.find(needle, at + 1)
        if not names:
            return None
        wanted = np.array(sorted(names), dtype=np.uint32)
        found = []
        for base, data in _chunks(handle):
            words = np.frombuffer(data[:len(data) // 4 * 4], dtype=np.uint32)
            for i in np.nonzero(np.isin(words, wanted))[0]:
                raw = data[i * 4 + 0x14: i * 4 + 0x24]
                if len(raw) == 16:
                    x, y, w, h = struct.unpack('<4f', raw)
                    if 50 <= w <= 5000 and 50 <= h <= 5000 and -100 <= x <= 8000 and -100 <= y <= 8000:
                        found.append((round(x), round(y), round(w), round(h)))
        return found[-1] if found else None
    finally:
        _kernel32.CloseHandle(handle)


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


def equipped_arrows(hwnd=None, tries=3):
    """Arrows left in the equipped pack of the game on this desktop (`hwnd`, else the one found
    on the current desktop): 0 if the arrow slot is empty. None if it can't be read — no game,
    not logged in, or the memory layout doesn't match (e.g. after a game update); then
    last_error says why. Tries a few times, half a second apart, before giving up."""
    global last_error
    for attempt in range(tries):
        if attempt:
            time.sleep(0.5)
        result, last_error = _read_equipped(hwnd or find_game_window())
        if result is not None:
            return result
    return None


def _read_equipped(hwnd):
    """(arrows, '') or (None, why)."""
    if not hwnd:
        return None, 'no game window on this desktop'
    pid = _pid_of(hwnd)
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return None, f'cannot open the game process {pid} (error {ctypes.GetLastError()})'
    try:
        hero = _u32(handle, HERO_PTR)
        if not hero:
            return None, f'no character in game process {pid} (not logged in?)'
        item = _u32(handle, hero + ARROW_SLOT)
        if not item:
            return 0, ''                               # nothing in the arrow slot
        vtable, kind = _u32(handle, item), _u32(handle, item + ITEM_TYPE)
        if vtable != ITEM_VTABLE or kind not in ARROW_TYPES:
            return None, f'the arrow slot holds something else (vtable 0x{vtable:x}, type {kind})'
        return _u32(handle, item + ITEM_AMOUNT) & 0xFFFF, ''   # a 16-bit field
    except OSError as e:
        return None, f'reading game process {pid} failed ({e})'
    finally:
        _kernel32.CloseHandle(handle)


def character_name(hwnd=None):
    """Name of the character logged in to the game on this desktop (`hwnd`, else the one found
    on the current desktop), or None (no game / not logged in / unreadable)."""
    hwnd = hwnd or find_game_window()
    if not hwnd:
        return None
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, _pid_of(hwnd))
    if not handle:
        return None
    try:
        hero = _u32(handle, HERO_PTR)
        where = _u32(handle, hero + HERO_NAME) if hero else 0
        if not where:
            return None
        buf = ctypes.create_string_buffer(16)
        read = ctypes.c_size_t()
        if not _kernel32.ReadProcessMemory(handle, ctypes.c_void_p(where), buf, 16, ctypes.byref(read)):
            return None
        name = buf.raw.split(b'\0')[0].decode('cp1252', 'replace').strip()
        return name if name.isprintable() and 1 < len(name) <= 15 else None
    except OSError:
        return None
    finally:
        _kernel32.CloseHandle(handle)


if __name__ == '__main__':
    print('character:', character_name(), '| equipped arrows:', equipped_arrows())
