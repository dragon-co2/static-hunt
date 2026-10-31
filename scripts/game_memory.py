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
ITEM_PLUS    = 0x61      # byte: the item's +1, +2...
ARROW_TYPES  = range(1050000, 1060000)
HERO_NAME    = 0x15C
HERO_CP      = 0x10      # CPs (the Shopping Mall's currency); +0x14 = CPs (B)
HERO_STATUS  = 0x6C      # status flags shown as icons at the top: 0x200 Stigma, 0x100 Magic Shield
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
                'status': _u32(handle, hero + HERO_STATUS),
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


def _processes():
    """[(pid, parent pid, exe lower case)] of every process running."""
    _kernel32.CreateToolhelp32Snapshot.restype = ctypes.wintypes.HANDLE
    snap = _kernel32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.wintypes.HANDLE(-1).value:
        return []
    out = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile.lower()))
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snap)
    return out


HUNG_SECONDS = 60       # a game whose window hasn't answered Windows this long has crashed
_hung_since = {}        # pid -> when its window was first seen not answering


def _windows_hung(pid):
    """True if `pid` has visible windows and every one is "Not Responding"; None if it has none."""
    user32 = ctypes.windll.user32
    found = []
    cb = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def _each(hwnd, _):
        owner = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(bool(user32.IsHungAppWindow(hwnd)))
        return True

    user32.EnumWindows(cb(_each), 0)
    return all(found) if found else None


def crashed(pid):
    """Why game `pid` has crashed though its process is still there - its "has stopped working"
    report (a WerFault.exe it started) is up, or its window has been Not Responding HUNG_SECONDS -
    or '' if it hasn't."""
    if any(parent == pid and exe == 'werfault.exe' for _, parent, exe in _processes()):
        return 'it crashed (its "stopped working" report is up)'
    if _windows_hung(pid):
        since = _hung_since.setdefault(pid, time.time())
        if time.time() - since >= HUNG_SECONDS:
            return f'its window has not responded for {int(time.time() - since)}s'
    else:
        _hung_since.pop(pid, None)
    return ''


def close_game(pid):
    """Ends game `pid` and its crash report (WerFault.exe) - so it can be logged in again."""
    for child, parent, exe in _processes():
        if parent == pid and exe == 'werfault.exe':
            _terminate(child)
    _terminate(pid)
    _hung_since.pop(pid, None)


def _terminate(pid):
    handle = _kernel32.OpenProcess(0x0001, False, pid)          # PROCESS_TERMINATE
    if handle:
        try:
            _kernel32.TerminateProcess(handle, 1)
        finally:
            _kernel32.CloseHandle(handle)


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


class _ModuleEntry(ctypes.Structure):   # MODULEENTRY32 (this 64-bit process's layout)
    _fields_ = [('dwSize', ctypes.wintypes.DWORD), ('th32ModuleID', ctypes.wintypes.DWORD),
                ('th32ProcessID', ctypes.wintypes.DWORD), ('GlblcntUsage', ctypes.wintypes.DWORD),
                ('ProccntUsage', ctypes.wintypes.DWORD), ('modBaseAddr', ctypes.c_void_p),
                ('modBaseSize', ctypes.wintypes.DWORD), ('hModule', ctypes.c_void_p),
                ('szModule', ctypes.c_char * 256), ('szExePath', ctypes.c_char * 260)]


def _module_base(pid, name):
    """Load address of the module `name` (e.g. 'Co.dll') in game process `pid`, or None."""
    _kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = _kernel32.CreateToolhelp32Snapshot(0x18, pid)   # TH32CS_SNAPMODULE | SNAPMODULE32
    if not snap or snap == ctypes.c_void_p(-1).value:
        return None
    try:
        entry = _ModuleEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _kernel32.Module32First(ctypes.c_void_p(snap), ctypes.byref(entry))
        while ok:
            if entry.szModule.decode('ascii', 'replace').lower() == name.lower():
                return entry.modBaseAddr
            ok = _kernel32.Module32Next(ctypes.c_void_p(snap), ctypes.byref(entry))
        return None
    finally:
        _kernel32.CloseHandle(ctypes.c_void_p(snap))


def bag_plus_items(pid):
    """[(item type, plus)] of the items of +1 or better in this character's bag."""
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return []
    try:
        hero = _u32(handle, HERO_PTR)
        out = []
        for item in _bag(handle, hero):
            if item and _u32(handle, item) == ITEM_VTABLE:
                plus = _read(handle, item + ITEM_PLUS, 1)[0]
                if plus:
                    out.append((_u32(handle, item + ITEM_TYPE), plus))
        return out
    except (OSError, struct.error):
        return []
    finally:
        _kernel32.CloseHandle(handle)


HERO_ID = 0xC8            # [HERO_PTR] + this: the character's id


def hero_id(pid):
    """This game's character id, or None."""
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        hero = _u32(handle, HERO_PTR)
        return hero and _u32(handle, hero + HERO_ID) or None
    except OSError:
        return None
    finally:
        _kernel32.CloseHandle(handle)


def bag_plus_ids(pid):
    """[(item id, item type, plus)] of the items of +1 or better in this character's bag. None if
    the bag can't be read."""
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        hero = _u32(handle, HERO_PTR)
        out = []
        for item in _bag(handle, hero):
            if item and _u32(handle, item) == ITEM_VTABLE:
                plus = _read(handle, item + ITEM_PLUS, 1)[0]
                if plus:
                    out.append((_u32(handle, item + ITEM_ID), _u32(handle, item + ITEM_TYPE), plus))
        return out
    except (OSError, struct.error):
        return None
    finally:
        _kernel32.CloseHandle(handle)


COMPOSE_LIST  = 0x2417D8  # Co.dll + this: vector (begin, end) of the VIP compose warehouse's items -
COMPOSE_ENTRY = 0x40      # 0x40-byte entries: item type, entry id, best plus, category, value (the
                          # items in it, a +n counting as 3^(n-1) +1s), ..., name (std::string) at +0x1C


def compose_items(pid):
    """[(item type, name, best plus, how many of that plus)] of the VIP compose warehouse's items
    (only the ones it holds) - read from Co.dll, as its window lists them. [] if unknown."""
    base = _module_base(pid, 'Co.dll')
    if not base:
        return []
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return []
    try:
        begin, end = struct.unpack('<2I', _read(handle, base + COMPOSE_LIST, 8))
        count = (end - begin) // COMPOSE_ENTRY
        if not 0 < count <= 500:
            return []
        raw = _read(handle, begin, count * COMPOSE_ENTRY)
        out = []
        for n in range(count):
            entry = raw[n * COMPOSE_ENTRY:(n + 1) * COMPOSE_ENTRY]
            item_type, _, plus, _, value = struct.unpack_from('<5I', entry)
            if not plus or not value:
                continue
            length, capacity = struct.unpack_from('<2I', entry, 0x2C)
            text = entry[0x1C:0x2C] if capacity < 16 else _read(handle, struct.unpack_from('<I', entry, 0x1C)[0], length)
            out.append((item_type, text[:length].decode('cp1252', 'replace'), plus, value // 3 ** (plus - 1)))
        return out
    except (OSError, struct.error):
        return []
    finally:
        _kernel32.CloseHandle(handle)


MAIL_RECEIVER = 0x63F98   # NoteData.dll + this: the server's answer to the mail's last Search (set with
                          # the mail window closed too): id, then name (std::string: 16-byte buffer,
                          # length, capacity). (+0x63BC0 is the Write tab's copy - only while it's open)


def mail_receiver(pid):
    """(id, name) of the character the mail Write tab's last Search found, or None."""
    base = _module_base(pid, 'NoteData.dll')
    if not base:
        return None
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        raw = _read(handle, base + MAIL_RECEIVER, 0x1C)
        receiver, = struct.unpack_from('<I', raw)
        length, capacity = struct.unpack_from('<2I', raw, 0x14)
        text = raw[4:0x14] if capacity < 16 else _read(handle, struct.unpack_from('<I', raw, 4)[0], length)
        return (receiver, text[:length].decode('cp1252', 'replace')) if receiver else None
    except (OSError, struct.error):
        return None
    finally:
        _kernel32.CloseHandle(handle)


MAILS      = 0x63F40      # NoteData.dll + this: vector (begin, end) of the mailbox's mails (as the server
MAIL_ENTRY = 0x68         # last sent them) - 0x68-byte entries: id, sender (std::string), subject
MAIL_UNREAD = 0x4D        # (std::string) ...; this byte is 1 while it's unread (the red dot)


def mails(pid):
    """[(mail id, sender, unread)] of this character's mailbox, as last loaded. [] if unknown."""
    base = _module_base(pid, 'NoteData.dll')
    if not base:
        return []
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return []
    try:
        begin, end = struct.unpack('<2I', _read(handle, base + MAILS, 8))
        count = (end - begin) // MAIL_ENTRY
        if not 0 < count <= 1000:
            return []
        raw = _read(handle, begin, count * MAIL_ENTRY)
        out = []
        for n in range(count):
            entry = raw[n * MAIL_ENTRY:(n + 1) * MAIL_ENTRY]
            length, capacity = struct.unpack_from('<2I', entry, 0x14)
            text = entry[4:0x14] if capacity < 16 else _read(handle, struct.unpack_from('<I', entry, 4)[0], length)
            out.append((struct.unpack_from('<I', entry)[0], text[:length].decode('cp1252', 'replace'),
                        bool(entry[MAIL_UNREAD])))
        return out
    except (OSError, struct.error):
        return []
    finally:
        _kernel32.CloseHandle(handle)


PACKAGES    = 0x008E8710  # the warehouse manager: an STLport map - [this] = header node, whose +4 is
                          # the root; node: +8 left, +0xC right, +0x10 key (kind, id), +0x18 the
                          # warehouse, whose +4 is a deque of its items (like the bag)
ARROW_BANK  = (4101, 10)  # the remote bank (Alt+P) tab the arrow packs are kept in


def bank_items(pid, key=ARROW_BANK):
    """[(item id, item type, amount)] in the warehouse `key`, or None if it isn't loaded in this
    client yet (game_actions.bank_open)."""
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        header = _u32(handle, PACKAGES)
        node, stack = _u32(handle, header + 4), []
        while stack or (node and node != header):      # in-order walk of the map
            while node and node != header:
                stack.append(node)
                node = _u32(handle, node + 8)
            node = stack.pop()
            kind, wh_id, package = struct.unpack('<3I', _read(handle, node + 0x10, 12))
            if (kind, wh_id) == tuple(key):
                return [(_u32(handle, i + ITEM_ID), _u32(handle, i + ITEM_TYPE),
                         _u32(handle, i + ITEM_AMOUNT) & 0xFFFF)
                        for i in _bag(handle, package + 4 - HERO_BAG)
                        if i and _u32(handle, i) == ITEM_VTABLE]
            node = _u32(handle, node + 0xC)
        return None
    except (OSError, struct.error):
        return None
    finally:
        _kernel32.CloseHandle(handle)


EQUIPMENT   = range(0xE90, 0xF00, 8)   # hero + these: the equipped items (8-byte shared pointers;
                                       # 0xEB0 = ARROW_SLOT)
DRAGONBALL, DB_SCROLL = 1088000, 720028


ITEM_DURA   = 0x190     # an item's durability x 100 (16 bits; the same field as an arrow pack's
                        # amount) - its most: itemtype.txt's amount limit


def worn_durability(pid):
    """[(position, item id, item type, durability)] of the items this character wears - position
    1, 2... (1 helmet, 2 necklace, 3 armor, 4 right hand, 5 left hand...: EQUIPMENT in order),
    durability x 100. [] if it can't be read."""
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return []
    try:
        hero = _u32(handle, HERO_PTR)
        out = []
        for position, off in enumerate(EQUIPMENT, 1):
            item = _u32(handle, hero + off)
            if item and _u32(handle, item) == ITEM_VTABLE:
                out.append((position, _u32(handle, item + ITEM_ID), _u32(handle, item + ITEM_TYPE),
                            _u32(handle, item + ITEM_DURA) & 0xFFFF))
        return out
    except (OSError, struct.error):
        return []
    finally:
        _kernel32.CloseHandle(handle)


def bag_durability(pid, item_id):
    """The durability (x 100) of the bag item with this id, or None if it isn't in the bag."""
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        for item in _bag(handle, _u32(handle, HERO_PTR)):
            if item and _u32(handle, item) == ITEM_VTABLE and _u32(handle, item + ITEM_ID) == item_id:
                return _u32(handle, item + ITEM_DURA) & 0xFFFF
        return None
    except (OSError, struct.error):
        return None
    finally:
        _kernel32.CloseHandle(handle)


def equipped_items(pid):
    """[(item id, item type)] of the items this character wears (arrows included)."""
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return []
    try:
        hero = _u32(handle, HERO_PTR)
        out = []
        for off in EQUIPMENT:
            item = _u32(handle, hero + off)
            if item and _u32(handle, item) == ITEM_VTABLE:
                out.append((_u32(handle, item + ITEM_ID), _u32(handle, item + ITEM_TYPE)))
        return out
    except (OSError, struct.error):
        return []
    finally:
        _kernel32.CloseHandle(handle)


def bag_items_of(pid, item_type):
    """Ids of the bag's items of `item_type`."""
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return []
    try:
        hero = _u32(handle, HERO_PTR)
        return [_u32(handle, i + ITEM_ID) for i in _bag(handle, hero)
                if i and _u32(handle, i) == ITEM_VTABLE and _u32(handle, i + ITEM_TYPE) == item_type]
    except (OSError, struct.error):
        return []
    finally:
        _kernel32.CloseHandle(handle)


def bag_arrow_packs(pid):
    """[(item id, arrows in it)] of the arrow packs in the bag."""
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return []
    try:
        hero = _u32(handle, HERO_PTR)
        return [(_u32(handle, i + ITEM_ID), _u32(handle, i + ITEM_AMOUNT) & 0xFFFF)
                for i in _bag(handle, hero)
                if i and _u32(handle, i) == ITEM_VTABLE and _u32(handle, i + ITEM_TYPE) in ARROW_TYPES]
    except (OSError, struct.error):
        return []
    finally:
        _kernel32.CloseHandle(handle)


GAME_MAP    = 0x008D366C  # -> the map; + MAP_ITEMS = its item manager: a vector (+4 begin, +8 end) of
MAP_ITEMS   = 0x78        # pointers to {id, type * 1000 + 990 + plus, x, y} - the items on the ground


def ground_items(pid):
    """[(item id, item type, plus, x, y)] of the items lying on the ground around this character
    (map cells; plus = the +1, +2... of the item), from the game's own item manager. [] if none."""
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return []
    try:
        game_map = _u32(handle, GAME_MAP)
        begin, end = struct.unpack('<2I', _read(handle, game_map + MAP_ITEMS + 4, 8))
        if not 0 <= end - begin <= 4 * 2000:
            return []
        out = []
        for at in range(begin, end, 4):
            item_id, kind, x, y = struct.unpack('<4I', _read(handle, _u32(handle, at), 16))
            out.append((item_id, kind // 1000, max(0, kind % 1000 - 990), x, y))
        return out
    except (OSError, struct.error):
        return []
    finally:
        _kernel32.CloseHandle(handle)


LOOT_LABELS = 0x330C48    # Co.dll + this: vector (begin, end, capacity) of the loot names drawn on
LOOT_ENTRY  = 0x2C        # the screen - 0x2C-byte entries: name (std::string: 16-byte buffer, length,
                          # capacity), screen x, y (floats), quality (5 = super), ?, item type


def loot(pid):
    """[(name, item type, quality, (screen x, screen y))] of the items lying on the ground that the
    game shows a name for (the ones on screen) - read from Co.dll, the server's add-on. [] if none."""
    base = _module_base(pid, 'Co.dll')
    if not base:
        return []
    handle = _kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return []
    try:
        begin, end = struct.unpack('<2I', _read(handle, base + LOOT_LABELS, 8))
        count = (end - begin) // LOOT_ENTRY
        if not 0 < count <= 500:
            return []
        raw = _read(handle, begin, count * LOOT_ENTRY)
        out = []
        for n in range(count):
            entry = raw[n * LOOT_ENTRY:(n + 1) * LOOT_ENTRY]
            length, capacity, x, y, quality, _, item_type = struct.unpack_from('<2I2f3I', entry, 16)
            text = entry[:16] if capacity < 16 else _read(handle, struct.unpack_from('<I', entry)[0], length)
            out.append((text[:length].decode('cp1252', 'replace'), item_type, quality, (x, y)))
        return out
    except (OSError, struct.error):
        return []
    finally:
        _kernel32.CloseHandle(handle)


if __name__ == '__main__':
    print('character:', character_name(), '| equipped arrows:', equipped_arrows())
    print('all games:', all_games())
    print('vitals:', all_vitals())
