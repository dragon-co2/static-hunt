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
    [hero + 0x15C]        -> the character's name (16 bytes, cp1252)
    [hero + 0x51C]        =  max HP (as last sent by the server)
    [hero + 0x980]        -> current HP: a scrambled int array (mode +4, size +8, data +0xC);
                             HP is entry 1, stored rotated by its slot index
    hero + 0x5E4          =  current MP: RC6-encrypted (round keys +4..+0xB0, block +0xB4)
    hero + 0xE38          =  the bag: a deque (start iterator cur/first/last/node, finish iterator)
                             of 8-byte entries whose first word is the item"""
import ctypes
import ctypes.wintypes
import struct
import time

import numpy as np

from game_window import find_game_window

HERO_PTR     = 0x008D365C
ARROW_SLOT   = 0x0EB0
ITEM_VTABLE  = 0x007FC66C
ITEM_ID      = 0x0C      # the item's own id (what the server knows it by)
ITEM_TYPE    = 0x1C
ITEM_AMOUNT  = 0x190
ARROW_TYPES  = range(1050000, 1060000)
HERO_NAME    = 0x15C
HERO_CP      = 0x10      # CPs (the Shopping Mall's currency); +0x14 = CPs (B)
HERO_MAX_HP  = 0x51C
HERO_HP      = 0x980
HERO_MP      = 0x5E4
HERO_BAG     = 0xE38
POTIONS      = {725065: '7StarOintment', 725066: 'SerenityPill',
                725067: '7StarPouch', 725068: 'SerenityPouch'}

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
    return _read_equipped_pid(_pid_of(hwnd))


def _read_equipped_pid(pid):
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
    return _name_pid(_pid_of(hwnd)) if hwnd else None


def _name_pid(pid):
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
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


_M32 = 0xFFFFFFFF


def _rotl(x, n):
    n &= 31
    return ((x << n) | (x >> (32 - n))) & _M32 if n else x


def _rotr(x, n):
    n &= 31
    return ((x >> n) | (x << (32 - n))) & _M32 if n else x


def _read(handle, address, size):
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t()
    if not _kernel32.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read)) \
            or read.value != size:
        raise OSError(f'cannot read 0x{address:08x}')
    return buf.raw


def _hp(handle, hero):
    """Current HP: entry 1 of the game's scrambled int array (the game's own getter, re-done)."""
    arr = _u32(handle, hero + HERO_HP)
    mode, size, data = struct.unpack('<III', _read(handle, arr + 4, 12))
    index = 1
    if mode == 0:
        slot = index
    elif mode == 1:
        slot = size - index - 1
    elif mode == 2:
        slot = index // 2 + ((size >> 1) + 1 if size & 1 else 0)
    elif mode == 3:
        slot = index // 2 + (0 if size & 1 else size >> 1)
    else:
        raise OSError(f'unknown HP layout {mode}')
    raw = _u32(handle, data + slot * 4)
    return _rotr(raw, slot) if mode in (0, 2) else _rotl(raw, slot)


def _mp(handle, hero):
    """Current MP: an RC6 block (20 rounds) decrypted with the round keys stored next to it."""
    obj = hero + HERO_MP
    s = struct.unpack('<44I', _read(handle, obj + 4, 44 * 4))
    a, b, c, d = struct.unpack('<4I', _read(handle, obj + 0xB4, 16))
    c = (c - s[43]) & _M32
    a = (a - s[42]) & _M32
    for r in range(20, 0, -1):
        a, b, c, d = d, a, b, c
        u = _rotl((d * (2 * d + 1)) & _M32, 5)
        t = _rotl((b * (2 * b + 1)) & _M32, 5)
        c = _rotr((c - s[2 * r + 1]) & _M32, t) ^ u
        a = _rotr((a - s[2 * r]) & _M32, u) ^ t
    d = (d - s[1]) & _M32
    b = (b - s[0]) & _M32
    return ((((d & 0xFF) << 8 | (c & 0xFF)) << 8 | (b & 0xFF)) << 8) + a


def _bag(handle, hero):
    """Item pointers in the bag (walks the deque from its start to its finish iterator)."""
    cur, first, last, node, fcur, _, _, fnode = struct.unpack('<8I', _read(handle, hero + HERO_BAG, 32))
    block = last - first
    if not 0 < block <= 0x1000:
        raise OSError('the bag does not look like a deque')
    items = []
    for _ in range(64):                      # a bag never spans that many blocks
        stop = fcur if node == fnode else last
        items += [_u32(handle, a) for a in range(cur, stop, 8)]
        if node == fnode:
            return items
        node += 4
        cur = _u32(handle, node)
        last = cur + block
    raise OSError('the bag deque never reached its end')


def vitals_pid(pid):
    """{'hp', 'max_hp', 'mp', 'cp', 'bag' (items in it), 'potions': {name: count},
    'ids': {name: [item id, ...]}} of the character in game process `pid`,
    or None (not logged in / unreadable)."""
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        hero = _u32(handle, HERO_PTR)
        if not hero:
            return None
        ids = {kind: [] for kind in POTIONS.values()}
        bag = _bag(handle, hero)
        for item in bag:
            if item and _u32(handle, item) == ITEM_VTABLE:
                kind = POTIONS.get(_u32(handle, item + ITEM_TYPE))
                if kind:
                    ids[kind].append(_u32(handle, item + ITEM_ID))
        potions = {kind: len(found) for kind, found in ids.items()}
        return {'hp': _hp(handle, hero), 'max_hp': _u32(handle, hero + HERO_MAX_HP),
                'mp': _mp(handle, hero), 'cp': _u32(handle, hero + HERO_CP),
                'bag': sum(1 for item in bag if item), 'potions': potions, 'ids': ids}
    except (OSError, struct.error):
        return None
    finally:
        _kernel32.CloseHandle(handle)


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', ctypes.wintypes.DWORD), ('cntUsage', ctypes.wintypes.DWORD),
                ('th32ProcessID', ctypes.wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                ('th32ModuleID', ctypes.wintypes.DWORD), ('cntThreads', ctypes.wintypes.DWORD),
                ('th32ParentProcessID', ctypes.wintypes.DWORD), ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', ctypes.wintypes.DWORD), ('szExeFile', ctypes.c_wchar * 260)]


def game_pids():
    """Process ids of every running Conquer.exe (whatever desktop its window is on)."""
    _kernel32.CreateToolhelp32Snapshot.restype = ctypes.wintypes.HANDLE
    snap = _kernel32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.wintypes.HANDLE(-1).value:
        return []
    pids = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == 'conquer.exe':
                pids.append(entry.th32ProcessID)
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snap)
    return pids


def all_games():
    """{character name: equipped arrows (None if unreadable)} for every running, logged-in game -
    read straight from each process, so it works for games on any desktop."""
    out = {}
    for pid in game_pids():
        name = _name_pid(pid)
        if name:
            out[name] = _read_equipped_pid(pid)[0]
    return out


def all_vitals():
    """{character name: vitals_pid()} for every running, logged-in game (any desktop)."""
    out = {}
    for pid in game_pids():
        name = _name_pid(pid)
        if name:
            out[name] = vitals_pid(pid)
    return out


if __name__ == '__main__':
    print('character:', character_name(), '| equipped arrows:', equipped_arrows())
    print('all games:', all_games())
    print('vitals:', all_vitals())
