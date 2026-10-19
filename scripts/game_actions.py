"""Game actions sent from inside the game, in the background - no window, no keys, no desktop
switch. Each one runs, in the game, the same calls the game's own code makes (found in
Conquer.exe - re-check after a game update):
    0x0069460F  CMsgItem::CMsgItem()                        the message (on the stack)
    0x00694675  CMsgItem::Create(id, data, action)          action 4 = use an item from the bag
                                                            (what CHero::UseItem at 0x683C54 sends)
    0x00694756  CMsgItem::Create(shop, type, 1, amount, pay)  the Shopping Mall's Pay CP button:
                                                            shop 2888, action 1 = buy, pay 1 = CPs
    0x00688F96  CNetMsg::Send()
    0x0069466A  CMsgItem::~CMsgItem()
    0x006A0850  CHero::MagicAttack(type, my id, target id, x, y) - casting a skill (it builds and
                sends the encrypted interact message itself). Cast on oneself (target = me, at my
                own position from the hero's vtable +0x14 GetPos), exactly like the game's own
                self-cast at 0x6BF49F - what right-clicking your character does.
    0x006138FF  CHero::Jump(x, y) - what Ctrl+click does, with all the game's own checks (16 cells
                at most, no jumping in a guild war / while riding a race...).
    0x008E8720  the set of players / NPCs around us: a deque (start iterator at +0x14, finish at
                +0x24) of 8-byte entries whose first word is the role object (name at +0x15C).
    0x0069EA42  CHero::UseMagic(type, target id, ctrl) - what right-clicking another player does
                (the game's right-click handler at 0x5AC215), with all its checks (range, mana...).
    [0x008D5544] the main game UI: +0x15286C right-click mode (1 / 2), +0x152860 / +0x152868 the
                skill on the right click in that mode; +0x715A8 the id of the player in the
                chat name box (set by Shift+clicking them).
This WRITES to the game: a ~100 byte stub is copied into it, run on a thread of its own, then
freed. Unlike game_memory.py it is not read-only.
    python game_actions.py <character> buy 7StarPouch 1
    python game_actions.py <character> use 7StarOintment
    python game_actions.py <character> cast 1095          (Stigma; 1090 = Magic Shield)
    python game_actions.py <character> jump <player>      (next to a player around you)"""
import ctypes
import ctypes.wintypes
import struct

import game_memory

MSG_CTOR, MSG_CREATE, MSG_CREATE_BUY, MSG_SEND, MSG_DTOR = (
    0x0069460F, 0x00694675, 0x00694756, 0x00688F96, 0x0069466A)
MAGIC_ATTACK = 0x006A0850
HERO_JUMP    = 0x006138FF
PLAYER_SET   = 0x008E8720 + 0x14   # the deque of roles around us
ROLE_NAME    = 0x15C
USE_MAGIC    = 0x0069EA42
MAIN_UI      = 0x008D5544      # -> the main game UI object
MAIN_UI_VTABLE = 0x007FA040
RCLICK_MODE  = 0x15286C
RCLICK_SKILL = {1: 0x152860, 2: 0x152868}
CHAT_TARGET  = 0x715A8         # main UI + this = id of the player in the chat name box (Shift+click)
HERO_ID      = 0xC8         # the hero's own id
HERO_SKILLS  = 0x11FC       # vector of the hero's skills (8-byte entries: skill object, refcount)
SKILL_TYPE   = 0x4C         # skill object + this = its type (1095 Stigma, 1090 Magic Shield...)
ACTION_BUY, ACTION_USE = 1, 4
MALL_SHOP  = 2888
PAY_CP     = 1
BAG_SLOTS  = 40
POUCHES    = {'7StarPouch': 725067, 'SerenityPouch': 725068}

_k32 = ctypes.windll.kernel32
_k32.OpenProcess.restype = ctypes.wintypes.HANDLE
_k32.VirtualAllocEx.restype = ctypes.c_void_p
_k32.VirtualAllocEx.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                ctypes.wintypes.DWORD, ctypes.wintypes.DWORD]
_k32.VirtualFreeEx.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.wintypes.DWORD]
_k32.WriteProcessMemory.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
_k32.CreateRemoteThread.restype = ctypes.wintypes.HANDLE
_k32.CreateRemoteThread.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                    ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.c_void_p]
_ACCESS = 0x0002 | 0x0008 | 0x0010 | 0x0020 | 0x0400   # CREATE_THREAD, VM_OPERATION/READ/WRITE, QUERY_INFORMATION


def _stub(create, args):
    """x86 thread routine: build an item message with `create`(*args), send it, destroy it."""
    lea_ecx = b'\x8D\x8D\xEC\xFB\xFF\xFF'                      # lea ecx, [ebp-0x414]  (the message)
    call = lambda addr: b'\xB8' + struct.pack('<I', addr) + b'\xFF\xD0'   # mov eax, addr; call eax
    pushes = b''.join(b'\x68' + struct.pack('<I', a & 0xFFFFFFFF) for a in reversed(args))
    send = lea_ecx + call(MSG_SEND)
    return (b'\x55\x8B\xEC\x81\xEC\x20\x04\x00\x00'            # push ebp; mov ebp, esp; sub esp, 0x420
            + lea_ecx + call(MSG_CTOR)
            + pushes + lea_ecx + call(create)                  # thiscall: the callee pops the args
            + b'\x85\xC0' + bytes([0x74, len(send)])          # test eax, eax; jz past the send
            + send
            + lea_ecx + call(MSG_DTOR)
            + b'\x33\xC0\xC9\xC2\x04\x00')                     # xor eax, eax; leave; ret 4


def _run(pid, code, timeout_ms=5000):
    """Copies `code` into game process `pid`, runs it on a new thread and waits for it."""
    handle = _k32.OpenProcess(_ACCESS, False, pid)
    if not handle:
        raise OSError(f'cannot open game process {pid} (error {ctypes.GetLastError()})')
    try:
        mem = _k32.VirtualAllocEx(handle, None, 0x1000, 0x3000, 0x40)   # COMMIT|RESERVE, RWX
        if not mem:
            raise OSError(f'VirtualAllocEx failed ({ctypes.GetLastError()})')
        running = False   # never free the stub while it may still be running
        try:
            written = ctypes.c_size_t()
            if not _k32.WriteProcessMemory(handle, mem, code, len(code), ctypes.byref(written)):
                raise OSError(f'WriteProcessMemory failed ({ctypes.GetLastError()})')
            thread = _k32.CreateRemoteThread(handle, None, 0, mem, None, 0, None)
            if not thread:
                raise OSError(f'CreateRemoteThread failed ({ctypes.GetLastError()})')
            try:
                if _k32.WaitForSingleObject(thread, timeout_ms) != 0:
                    running = True
                    raise TimeoutError('the stub did not finish')
            finally:
                _k32.CloseHandle(thread)
        finally:
            if not running:
                _k32.VirtualFreeEx(handle, mem, 0, 0x8000)   # MEM_RELEASE
    finally:
        _k32.CloseHandle(handle)


def buy(pid, item_type, amount):
    """One Shopping Mall purchase of `amount` x `item_type`, paid in CPs (the server still
    decides - check the CPs / bag afterwards)."""
    if not 1 <= amount <= BAG_SLOTS:
        raise ValueError(f'amount {amount} out of range')
    _run(pid, _stub(MSG_CREATE_BUY, (MALL_SHOP, item_type, ACTION_BUY, amount, PAY_CP)))


def use(pid, item_id):
    """Uses the bag item with this id (game_memory.vitals_pid()['ids']) - drinks a potion, opens
    a pouch - like right-clicking it."""
    _run(pid, _stub(MSG_CREATE, (item_id, 0, ACTION_USE)))


def skills(pid):
    """The skill types this character has (read from its memory), e.g. {1095, 1090, ...}."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return set()
    try:
        hero = game_memory._u32(handle, game_memory.HERO_PTR)
        begin = game_memory._u32(handle, hero + HERO_SKILLS)
        end = game_memory._u32(handle, hero + HERO_SKILLS + 4)
        found = set()
        for at in range(begin, min(end, begin + 8 * 200), 8):
            skill = game_memory._u32(handle, at)
            if skill:
                found.add(game_memory._u32(handle, skill + SKILL_TYPE))
        return found
    except OSError:
        return set()
    finally:
        _k32.CloseHandle(handle)


def cast_self(pid, skill_type):
    """Casts `skill_type` on this character itself (Stigma, Magic Shield...), like right-clicking
    it with the skill: the game's own CHero::MagicAttack, target = me, at my position."""
    hero_ptr = struct.pack('<I', game_memory.HERO_PTR)
    code = (b'\x55\x8B\xEC\x83\xEC\x08'                        # push ebp; mov ebp, esp; sub esp, 8
            + b'\x8B\x0D' + hero_ptr                           # mov ecx, [hero]
            + b'\x8B\x11\x8D\x45\xF8\x50\xFF\x52\x14'           # mov edx,[ecx]; lea eax,[ebp-8]; push eax; call [edx+0x14] (GetPos)
            + b'\x8B\x0D' + hero_ptr                           # mov ecx, [hero]
            + b'\x8B\x81' + struct.pack('<I', HERO_ID)         # mov eax, [ecx+0xC8]  (my id)
            + b'\xFF\x75\xFC\xFF\x75\xF8\x50\x50'               # push y; push x; push target=me; push me
            + b'\x68' + struct.pack('<I', skill_type)          # push the skill
            + b'\xB8' + struct.pack('<I', MAGIC_ATTACK) + b'\xFF\xD0'   # call MagicAttack (pops its args)
            + b'\x33\xC0\xC9\xC2\x04\x00')                     # xor eax, eax; leave; ret 4
    _run(pid, code)


def players(pid):
    """{name: role object address} of the players / NPCs around this character (read only)."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return {}
    try:
        cur, first, last, node, fcur, _, _, fnode = struct.unpack('<8I', game_memory._read(handle, PLAYER_SET, 32))
        block, roles = last - first, []
        if not 0 < block <= 0x1000:
            return {}
        for _ in range(64):
            stop = fcur if node == fnode else last
            roles += [game_memory._u32(handle, a) for a in range(cur, stop, 8)]
            if node == fnode:
                break
            node += 4
            cur = game_memory._u32(handle, node)
            last = cur + block
        out = {}
        for role in roles:
            if role:
                raw = game_memory._read(handle, game_memory._u32(handle, role + ROLE_NAME), 16)
                out[raw.split(b'\0')[0].decode('cp1252', 'replace')] = role
        return out
    except (OSError, struct.error):
        return {}
    finally:
        _k32.CloseHandle(handle)


def jump_next_to(pid, role, dx=1, dy=0):
    """Jumps this character next to `role` (a players() address): its position (the game's own
    GetPos) plus (dx, dy), through CHero::Jump - so the game's checks apply (16 cells at most)."""
    code = (b'\x55\x8B\xEC\x83\xEC\x08'                        # push ebp; mov ebp, esp; sub esp, 8
            + b'\xB9' + struct.pack('<I', role)                # mov ecx, role
            + b'\x8B\x11\x8D\x45\xF8\x50\xFF\x52\x14'           # GetPos(&pt) through its vtable +0x14
            + b'\x8B\x45\xFC\x83\xC0' + struct.pack('<b', dy) + b'\x50'   # push y + dy
            + b'\x8B\x45\xF8\x83\xC0' + struct.pack('<b', dx) + b'\x50'   # push x + dx
            + b'\x8B\x0D' + struct.pack('<I', game_memory.HERO_PTR)        # mov ecx, [hero]
            + b'\xB8' + struct.pack('<I', HERO_JUMP) + b'\xFF\xD0'        # call CHero::Jump
            + b'\x33\xC0\xC9\xC2\x04\x00')                     # xor eax, eax; leave; ret 4
    _run(pid, code)


def right_click_skill(pid):
    """The skill type on this character's right click (whatever it is set to in the game), or None."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return None
    try:
        ui = game_memory._u32(handle, MAIN_UI)
        if not ui or game_memory._u32(handle, ui) != MAIN_UI_VTABLE:
            return None
        at = RCLICK_SKILL.get(game_memory._u32(handle, ui + RCLICK_MODE))
        return (game_memory._u32(handle, ui + at) or None) if at else None
    except OSError:
        return None
    finally:
        _k32.CloseHandle(handle)


def chat_target(pid):
    """Id of the player in the chat's name box (what Shift+clicking a player sets), or 0. The box
    is drawn by the game itself, so it's read from the main UI in memory."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return 0
    try:
        ui = game_memory._u32(handle, MAIN_UI)
        if not ui or game_memory._u32(handle, ui) != MAIN_UI_VTABLE:
            return 0
        return game_memory._u32(handle, ui + CHAT_TARGET)
    except OSError:
        return 0
    finally:
        _k32.CloseHandle(handle)


def role_id(pid, role):
    """The id of a players() role (what skills are aimed at)."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    try:
        return game_memory._u32(handle, role + HERO_ID)
    finally:
        _k32.CloseHandle(handle)


def use_skill_on(pid, skill_type, target_id):
    """Uses `skill_type` on the role with `target_id`, like right-clicking it with that skill:
    the game's own CHero::UseMagic, with its checks (range, mana, cooldown...)."""
    code = (b'\x55\x8B\xEC'                                    # push ebp; mov ebp, esp
            + b'\x6A\x00'                                      # push 0 (no Ctrl)
            + b'\x68' + struct.pack('<I', target_id)           # push the target
            + b'\x68' + struct.pack('<I', skill_type)          # push the skill
            + b'\x8B\x0D' + struct.pack('<I', game_memory.HERO_PTR)   # mov ecx, [hero]
            + b'\xB8' + struct.pack('<I', USE_MAGIC) + b'\xFF\xD0'   # call UseMagic (pops its args)
            + b'\x33\xC0\xC9\xC2\x04\x00')                     # xor eax, eax; leave; ret 4
    _run(pid, code)


if __name__ == '__main__':
    import sys
    import time
    name, what, kind = sys.argv[1:4]
    pid = next(p for p in game_memory.game_pids() if game_memory._name_pid(p) == name)
    v = game_memory.vitals_pid(pid)
    print('before:', v['hp'], 'HP', v['mp'], 'MP', v['cp'], 'CP', v['potions'])
    if what == 'buy':
        buy(pid, POUCHES[kind], int(sys.argv[4]))
    elif what == 'jump':
        around = players(pid)
        if kind not in around:
            sys.exit(f'{kind} is not around {name} - around: {", ".join(sorted(around))}')
        jump_next_to(pid, around[kind])
    elif what == 'cast':
        if int(kind) not in skills(pid):
            sys.exit(f'{name} does not have skill {kind}')
        cast_self(pid, int(kind))
    else:
        use(pid, v['ids'][kind][0])
    time.sleep(1.5)
    v = game_memory.vitals_pid(pid)
    print('after: ', v['hp'], 'HP', v['mp'], 'MP', v['cp'], 'CP', v['potions'])
