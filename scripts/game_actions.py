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
                chat name box (set by Shift+clicking them); +0x71874 / +0x71878 begin / end of
                the text typed into that box (one character behind).
    0x006D1686  CHero::ActivateNpc(npc id) - clicking an NPC; the server sends its dialog.
    0x006D1619  sends an NPC dialog answer (option, text, task id, action 0x65) - clicking a line;
                the option numbers come from the server (Exchange~DB: 2 = DBScroll, 20 = Yes).
    0x00616560  CHero attack(target, 0, 1, 1) - left-clicking a monster: jumps / walks into reach
                and keeps hitting it with the weapon (MsgInteract type 2).
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
ROLE_SET     = 0x008E8720         # the game's set of roles around us
PLAYER_SET   = ROLE_SET + 0x14    # the deque of roles around us
GET_ROLE     = 0x00691E0B         # ROLE_SET.GetRole(&shared pointer, id, 0) - a role by its id
RELEASE_REF  = 0x00404CDB         # ref count.Release() - drops a shared pointer's reference
ROLE_NAME    = 0x15C
ROLE_DEAD    = 0x18            # 1 while the role (player) is dead, 0 while alive
MONSTER_DYING = 0x9C           # 0 while a monster lives, 2 from its killing blow on (its corpse too)
ROLE_COMMAND = 0x1F8           # what the role is doing: 5 standing, 0x14 (20) jumping...
ROLE_DEST    = 0x218           # +0x218 x, +0x21C y: where that command ends (the landing cell)
CMD_JUMP     = 0x14
USE_MAGIC    = 0x0069EA42
MAIN_UI      = 0x008D5544      # -> the main game UI object
MAIN_UI_VTABLE = 0x007FA040
RCLICK_MODE  = 0x15286C
RCLICK_SKILL = {1: 0x152860, 2: 0x152868}
CHAT_TARGET  = 0x715A8         # main UI + this = id of the player in the chat name box (Shift+click)
CHAT_TEXT    = 0x71788 + 0xEC  # main UI + this = begin / end of what was typed in that box - always
                               # one character behind (it's taken before the last key lands)
HERO_ID      = 0xC8         # the hero's own id
HERO_SKILLS  = 0x11FC       # vector of the hero's skills (8-byte entries: skill object, refcount)
SKILL_TYPE   = 0x4C         # skill object + this = its type (1095 Stigma, 1090 Magic Shield...)
ACTIVATE_NPC = 0x006D1686     # CHero::ActivateNpc(npc id) - clicking an NPC (sends MsgNpc 2031)
TASK_ANSWER  = 0x006D1619     # CHero: send MsgTaskDialog 2032 (option, text, task id, action) -
                              # what clicking a line of an NPC dialog does (handler 0x524577)
TASK_ACTION_ANSWER = 0x65
HERO_ATTACK  = 0x00616560     # CHero attack(target shared pointer, 1, 1) - left-clicking a role
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


def _run(pid, code, timeout_ms=5000, read=0):
    """Copies `code` into game process `pid`, runs it on a new thread and waits for it. With
    `read`, `code` is a function of an output address (in the same block) and the `read` bytes
    written there are returned."""
    handle = _k32.OpenProcess(_ACCESS, False, pid)
    if not handle:
        raise OSError(f'cannot open game process {pid} (error {ctypes.GetLastError()})')
    try:
        size = 0x10000
        mem = _k32.VirtualAllocEx(handle, None, size, 0x3000, 0x40)   # COMMIT|RESERVE, RWX
        if not mem:
            raise OSError(f'VirtualAllocEx failed ({ctypes.GetLastError()})')
        running = False   # never free the stub while it may still be running
        try:
            if read:
                code = code(mem + size - read)
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
            if read:
                return game_memory._read(handle, mem + size - read, read)
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


def roles(pid):
    """[(name, role object address)] of everything around this character - players, NPCs and
    monsters, with repeated names (read only)."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return []
    try:
        cur, first, last, node, fcur, _, _, fnode = struct.unpack('<8I', game_memory._read(handle, PLAYER_SET, 32))
        block, found = last - first, []
        if not 0 < block <= 0x1000:
            return []
        for _ in range(64):
            stop = fcur if node == fnode else last
            found += [game_memory._u32(handle, a) for a in range(cur, stop, 8)]
            if node == fnode:
                break
            node += 4
            cur = game_memory._u32(handle, node)
            last = cur + block
        out = []
        for role in found:
            if role:
                raw = game_memory._read(handle, game_memory._u32(handle, role + ROLE_NAME), 16)
                out.append((raw.split(b'\0')[0].decode('cp1252', 'replace'), role))
        return out
    except (OSError, struct.error):
        return []
    finally:
        _k32.CloseHandle(handle)


def players(pid):
    """{name: role object address} of the players / NPCs around this character (read only)."""
    return dict(roles(pid))


def jump_next_to(pid, role):
    """Jumps this character onto the cell next to `role` (a players() address) on our side of it -
    the nearest one to us: target + (sign(me.x - target.x), sign(me.y - target.y)), positions from
    the game's own GetPos; through CHero::Jump, so the game's checks apply (16 cells at most).
    When the target is in the air (its command is a jump), its landing cell is used instead of
    where it is right now - so we land next to where it lands."""
    _run(pid, b'\x55\x8B\xEC\x83\xEC\x18'                     # push ebp; mov ebp, esp; sub esp, 0x18
         + _jump_body(b'\xB9' + struct.pack('<I', role))        # mov ecx, role
         + b'\x33\xC0\xC9\xC2\x04\x00')                         # xor eax, eax; leave; ret 4


def _jump_body(load_role):
    """The jump_next_to code; `load_role` puts the role in ecx. Uses [ebp-0x18]..[ebp-4]."""
    hero = struct.pack('<I', game_memory.HERO_PTR)
    sign = b'\x8B\xC8\xC1\xF9\x1F\xF7\xD8\xC1\xE8\x1F\x03\xC1'   # eax = sign(eax)
    return (load_role
            + b'\x8B\x11\x8D\x45\xF8\x50\xFF\x52\x14'           # GetPos(target) -> [ebp-8] x, [ebp-4] y
            + load_role
            + b'\x83\xB9' + struct.pack('<I', ROLE_COMMAND) + bytes([CMD_JUMP])   # jumping?
            + b'\x75\x12'                                      # no: keep GetPos
            + b'\x8B\x81' + struct.pack('<I', ROLE_DEST) + b'\x89\x45\xF8'       # x = landing x
            + b'\x8B\x81' + struct.pack('<I', ROLE_DEST + 4) + b'\x89\x45\xFC'   # y = landing y
            + b'\x8B\x0D' + hero                               # mov ecx, [hero]
            + b'\x8B\x11\x8D\x45\xF0\x50\xFF\x52\x14'           # GetPos(me) -> [ebp-0x10] x, [ebp-0xC] y
            + b'\x8B\x45\xF0\x2B\x45\xF8' + sign + b'\x89\x45\xEC'   # dx = sign(me.x - target.x)
            + b'\x8B\x45\xF4\x2B\x45\xFC' + sign + b'\x89\x45\xE8'   # dy = sign(me.y - target.y)
            + b'\x8B\x45\xEC\x0B\x45\xE8\x75\x07'               # on the same cell:
            + b'\xC7\x45\xEC\x01\x00\x00\x00'                  #   dx = 1
            + b'\x8B\x45\xFC\x03\x45\xE8\x50'                   # push target.y + dy
            + b'\x8B\x45\xF8\x03\x45\xEC\x50'                   # push target.x + dx
            + b'\x8B\x0D' + hero                               # mov ecx, [hero]
            + b'\xB8' + struct.pack('<I', HERO_JUMP) + b'\xFF\xD0')  # call CHero::Jump


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


def chat_text(pid):
    """What was typed into the chat's name box, minus its last character (the game keeps it one
    key behind) - so use it as the start of a name. '' if empty / unreadable."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return ''
    try:
        ui = game_memory._u32(handle, MAIN_UI)
        if not ui or game_memory._u32(handle, ui) != MAIN_UI_VTABLE:
            return ''
        begin = game_memory._u32(handle, ui + CHAT_TEXT)
        end = game_memory._u32(handle, ui + CHAT_TEXT + 4)
        if not begin or not 0 < end - begin <= 32:
            return ''
        return game_memory._read(handle, begin, end - begin).split(b'\0')[0].decode('cp1252', 'replace').strip()
    except OSError:
        return ''
    finally:
        _k32.CloseHandle(handle)


def role_id(pid, role):
    """The id of a players() role (what skills are aimed at)."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    try:
        return game_memory._u32(handle, role + HERO_ID)
    finally:
        _k32.CloseHandle(handle)


def role_dead(pid, role):
    """True if the role (a players() address) is dead (lying dead / a ghost)."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return False
    try:
        return bool(game_memory._u32(handle, role + ROLE_DEAD))
    except OSError:
        return False
    finally:
        _k32.CloseHandle(handle)


def monster_dying(pid, role):
    """True once a monster (a roles() address) has taken its killing blow: +0x9C turns 2 right away
    (~0.04s), well before its command turns 46 (a corpse, ~0.25s)."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return False
    try:
        return game_memory._u32(handle, role + MONSTER_DYING) != 0
    except OSError:
        return False
    finally:
        _k32.CloseHandle(handle)


def role_pos(pid, obj):
    """The (x, y) map cell of a role or the hero (an object address), via the game's own GetPos
    (one injected stub), or None. Used to measure how far a target is."""
    handle = _k32.OpenProcess(_ACCESS, False, pid)
    if not handle:
        return None
    try:
        mem = _k32.VirtualAllocEx(handle, None, 0x1000, 0x3000, 0x40)   # COMMIT|RESERVE, RWX
        if not mem:
            return None
        running = False
        try:
            out = mem + 0x200
            code = (b'\x55\x8B\xEC'                            # push ebp; mov ebp, esp
                    + b'\xB9' + struct.pack('<I', obj)         # mov ecx, obj
                    + b'\x8B\x11'                              # mov edx, [ecx]
                    + b'\x68' + struct.pack('<I', out) + b'\xFF\x52\x14'   # push &pt (out); GetPos: [out]x [out+4]y
                    + b'\xC9\xC2\x04\x00')                     # leave; ret 4
            written = ctypes.c_size_t()
            if not _k32.WriteProcessMemory(handle, mem, code, len(code), ctypes.byref(written)):
                return None
            thread = _k32.CreateRemoteThread(handle, None, 0, mem, None, 0, None)
            if not thread:
                return None
            try:
                if _k32.WaitForSingleObject(thread, 3000) != 0:
                    running = True
                    return None
            finally:
                _k32.CloseHandle(thread)
            return struct.unpack('<2i', game_memory._read(handle, out, 8))
        finally:
            if not running:
                _k32.VirtualFreeEx(handle, mem, 0, 0x8000)   # MEM_RELEASE
    except (OSError, struct.error):
        return None
    finally:
        _k32.CloseHandle(handle)


def my_pos(pid):
    """This character's own (x, y) map cell, or None."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return None
    try:
        hero = game_memory._u32(handle, game_memory.HERO_PTR)
    finally:
        _k32.CloseHandle(handle)
    return role_pos(pid, hero) if hero else None


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


def role_command(pid, role):
    """What the role is doing (ROLE_COMMAND): 5 standing, 0x14 jumping, 46 dead (a monster's corpse)..."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return None
    try:
        return game_memory._u32(handle, role + ROLE_COMMAND)
    except OSError:
        return None
    finally:
        _k32.CloseHandle(handle)


def _with_role(target_id, body):
    """Thread routine: looks the role with `target_id` up in the game (GET_ROLE - a shared pointer,
    so it can't be freed meanwhile), runs `body` with esi = the role and edi = its ref count (body
    may use [ebp-0x18]..[ebp-4]), then drops the reference. Nothing happens if the role is gone.
    A roles() address can't be used for this: a monster dies and is freed any moment."""
    call = lambda addr: b'\xB8' + struct.pack('<I', addr) + b'\xFF\xD0'   # mov eax, addr; call eax
    release = (b'\x85\xFF\x74\x09'                              # test edi, edi; jz past the release
               + b'\x8B\xCF' + call(RELEASE_REF))               # mov ecx, edi; drop the reference
    return (b'\x55\x8B\xEC\x83\xEC\x20\x56\x57'                 # push ebp; mov ebp,esp; sub esp,0x20; push esi; push edi
            + b'\x83\x65\xE0\x00\x83\x65\xE4\x00'               # the shared pointer = null
            + b'\x6A\x00\x68' + struct.pack('<I', target_id)    # push 0; push the id
            + b'\x8D\x45\xE0\x50'                               # lea eax, [ebp-0x20]; push eax (the shared pointer)
            + b'\xB9' + struct.pack('<I', ROLE_SET) + call(GET_ROLE)
            + b'\x8B\x75\xE0\x8B\x7D\xE4'                       # mov esi, role; mov edi, ref count
            + b'\x85\xF6\x0F\x84' + struct.pack('<i', len(body) + len(release))   # no role: to the end
            + body + release
            + b'\x5F\x5E\x33\xC0\xC9\xC2\x04\x00')              # pop edi; pop esi; xor eax,eax; leave; ret 4


def my_command(pid):
    """What this character is doing (see role_command), or None."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return None
    try:
        return game_memory._u32(handle, game_memory._u32(handle, game_memory.HERO_PTR) + ROLE_COMMAND)
    except OSError:
        return None
    finally:
        _k32.CloseHandle(handle)


def attack(pid, target_id):
    """Attacks the role with `target_id` with the weapon, like left-clicking it: the game's own
    CHero attack (0x616560: target shared pointer, 1, 1 - it drops the reference it's given, so
    one is added for it). The game then keeps attacking by itself while it's in reach."""
    _run(pid, _with_role(target_id,
                         b'\x85\xFF\x74\x04\xF0\xFF\x47\x04'    # test edi,edi; jz; lock inc [edi+4] (its reference)
                         + b'\x6A\x01\x6A\x01\x57\x56'          # push 1; push 1; push ref count; push role
                         + b'\x8B\x0D' + struct.pack('<I', game_memory.HERO_PTR)   # mov ecx, [hero]
                         + b'\xB8' + struct.pack('<I', HERO_ATTACK) + b'\xFF\xD0'))


def jump_next_to_id(pid, target_id):
    """jump_next_to, for the role with `target_id` - safe even if it dies meanwhile (a monster)."""
    _run(pid, _with_role(target_id, _jump_body(b'\x8B\xCE')))   # mov ecx, esi


def positions(pid, ids):
    """{id: (x, y)} of the roles with these ids that are still around - one injected stub, each
    role held by a reference while its GetPos runs (see _with_role)."""
    ids = list(ids)[:200]
    if not ids:
        return {}
    call = lambda addr: b'\xB8' + struct.pack('<I', addr) + b'\xFF\xD0'

    def code(out):
        parts = [b'\x55\x8B\xEC\x83\xEC\x08']                   # push ebp; mov ebp, esp; sub esp, 8
        for n, target_id in enumerate(ids):
            at = out + 12 * n                                   # x, y, found
            getpos = (b'\x8B\x11\x68' + struct.pack('<I', at) + b'\xFF\x52\x14'   # GetPos(role, at)
                      + b'\xC7\x05' + struct.pack('<I', at + 8) + b'\x01\x00\x00\x00')   # found = 1
            release = b'\x8B\x4D\xFC\x85\xC9\x74\x07' + call(RELEASE_REF)
            parts.append(b'\x83\x65\xF8\x00\x83\x65\xFC\x00'   # the shared pointer = null
                         + b'\x6A\x00\x68' + struct.pack('<I', target_id)
                         + b'\x8D\x45\xF8\x50'                  # lea eax, [ebp-8]; push eax
                         + b'\xB9' + struct.pack('<I', ROLE_SET) + call(GET_ROLE)
                         + b'\x8B\x4D\xF8\x85\xC9'              # mov ecx, role; test ecx, ecx
                         + b'\x74' + bytes([len(getpos) + len(release)])   # gone: next id
                         + getpos + release)
        parts.append(b'\x33\xC0\xC9\xC2\x04\x00')
        return b''.join(parts)

    data = _run(pid, code, read=12 * len(ids))
    out = {}
    for n, target_id in enumerate(ids):
        x, y, found = struct.unpack_from('<3i', data, 12 * n)
        if found:
            out[target_id] = (x, y)
    return out


def _hero_call(pid, function, args):
    """Calls the hero's `function`(*args) (thiscall, the callee pops its args) inside the game."""
    pushes = b''.join(b'\x68' + struct.pack('<I', a & 0xFFFFFFFF) for a in reversed(args))
    _run(pid, b'\x55\x8B\xEC'                                  # push ebp; mov ebp, esp
         + pushes
         + b'\x8B\x0D' + struct.pack('<I', game_memory.HERO_PTR)    # mov ecx, [hero]
         + b'\xB8' + struct.pack('<I', function) + b'\xFF\xD0'     # call function
         + b'\x33\xC0\xC9\xC2\x04\x00')                         # xor eax, eax; leave; ret 4


def talk_to_npc(pid, npc_id):
    """Clicks the NPC with this id (its role id, see role_id): the server answers with its dialog."""
    _hero_call(pid, ACTIVATE_NPC, (npc_id,))


def npc_answer(pid, option):
    """Picks `option` in the open NPC dialog (the number the server gave that line, not its place)."""
    _hero_call(pid, TASK_ANSWER, (option, 0, 0, TASK_ACTION_ANSWER))


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
