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
import time

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
MAP_MSG_CTOR, MAP_MSG_CREATE, MAP_MSG_SEND, MAP_MSG_DTOR = (   # CMsgMapItem (1101): the ground items
    0x006E7174, 0x006E71DA, 0x006E73A6, 0x006E71CF)            # Create(id, x, y, action) - 3 = pick up
HERO_TALK    = 0x0061419D     # CHero::Talk(to, text, 0, color, channel, style) - the chat box (MsgTalk 1004)
CHAT_WHISPER = 2001           # the whisper channel
BANK_OPEN    = 0x0068D811     # warehouse manager.Open(kind, id): load a warehouse (MsgPackage 0)
BANK_WITHDRAW = 0x0068DA98    # warehouse manager.Withdraw(kind, id, item id) (MsgPackage 2)
CLOSE_BUTTONS = [(1622, 1536),   # the warehouse window: its X (1622), its first tab (1536)
                 (1128, 1126)]   # the bag window: its Close (1128), its item grid (1126)
BANK_DEPOSIT = 0x0068D9FD     # warehouse manager.Deposit(kind, id, item id) (MsgPackage 1)
BANK_QUERY   = 0x0068D957     # warehouse manager.Query(kind, id, page): its items again (MsgPackage 0)
HERO_REVIVE  = 0x006186C3     # CHero::Revive(mode) - the death window: 0 = revive, 1 = revive here
STATUS_DEAD  = 0x400          # hero status bit 10: dead
MAP_FLAGS, MAP_REVIVE_HERE = 0xE8, 0x2000   # [game map] + this: flags; 0x2000 = Revive Here allowed
CO_COMPOSE_DEPOSIT = 0x8BE70  # Co.dll + this: the VIP Compose Warehouse 'Deposit' button's function
CO_SEND      = 0x8B940        # Co.dll + this: its message send (ecx = type, edx = data, push length)
CO_MSG_REMOTE_WH = 0x1F45     # its message 8005: a VIP command (vip_command)
VIP_REPAIR_ALL = 30           # 8005 {30}: Repair All
CO_MSG_COMPOSE = 0x1F6B       # its message 8043: the VIP compose warehouse - {1} list, {3, type, plus, amount} withdraw
COMPOSE_LIST, COMPOSE_WITHDRAW = 1, 3
CO_MSG_MAIL  = 0x1FA4         # its message 8100: the mail window's (NoteData.dll) messages, wrapped
MAIL_SEARCH, MAIL_WRITE = 1, 0x14   # its messages: {1, name[32]} Search, {0x14, ...} Write > Send,
MAIL_LIST, MAIL_CLAIM_ALL = 0x16, 0x15   # {0x16} the mailbox's mails (opening it), {0x15} Claim All
MAIL_ITEMS_MAX = 5            # items one mail can carry
HERO_PICK_UP = 0x00683A4F     # CHero::PickUp(item id, x, y) - left-clicking an item on the ground
ON_HOTKEY    = 0x0059170B     # main UI.OnHotkey(slot | id << 8) - what pressing F1..F10 does
HOTKEY_BAR   = 0x008D5C74     # -> [this] + HOTKEY_IDS + 4 * slot = the id in hotkey slot (F1 = 0)
HOTKEY_IDS   = 0x11CA8C + 0x14B8
STATUS_XP    = 0x10           # hero status bit: the XP bar is full (the XP skill icons are up)
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


def _run(pid, code, timeout_ms=5000, read=0, data=b''):
    """Copies `code` into game process `pid`, runs it on a new thread and waits for it. With
    `read`, `code` is a function of an output address (in the same block) and the `read` bytes
    written there are returned. With `data`, `code` is a function of the address (in the same
    block) `data` is copied to."""
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
            written = ctypes.c_size_t()
            if read:
                code = code(mem + size - read)
            elif data:
                code = code(mem + size - len(data))
                if not _k32.WriteProcessMemory(handle, mem + size - len(data), data, len(data),
                                               ctypes.byref(written)):
                    raise OSError(f'WriteProcessMemory failed ({ctypes.GetLastError()})')
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


def cast_at(pid, skill_type, x, y):
    """Casts `skill_type` at the map cell (x, y) with no target - a ground skill like Scatter, aimed
    the way right-clicking that spot does: CHero::MagicAttack(type, me, 0, x, y)."""
    code = (b'\x55\x8B\xEC'                                    # push ebp; mov ebp, esp
            + b'\x68' + struct.pack('<i', y)                   # push y
            + b'\x68' + struct.pack('<i', x)                   # push x
            + b'\x6A\x00'                                      # push 0 (no target)
            + b'\x8B\x0D' + struct.pack('<I', game_memory.HERO_PTR)   # mov ecx, [hero]
            + b'\xFF\xB1' + struct.pack('<I', HERO_ID)         # push [ecx+0xC8] (me)
            + b'\x68' + struct.pack('<I', skill_type)          # push the skill
            + b'\xB8' + struct.pack('<I', MAGIC_ATTACK) + b'\xFF\xD0'   # call MagicAttack (pops its args)
            + b'\x33\xC0\xC9\xC2\x04\x00')                     # xor eax, eax; leave; ret 4
    _run(pid, code)


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


def xp_ready(pid):
    """True while this character's XP bar is full - the XP skill icons are up (status bit 0x10)."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return False
    try:
        hero = game_memory._u32(handle, game_memory.HERO_PTR)
        return bool(game_memory._u32(handle, hero + game_memory.HERO_STATUS) & STATUS_XP)
    except OSError:
        return False
    finally:
        _k32.CloseHandle(handle)


def press_hotkey(pid, slot):
    """Uses what's in hotkey slot `slot` (0 = F1 ... 9 = F10) exactly like pressing that key, but
    from inside the game - no key, no window in front: the game's own hotkey handler
    UI.OnHotkey(slot | slot's id << 8), the id read in the game at the moment of the call."""
    if not 0 <= slot <= 9:
        raise ValueError(f'slot {slot} out of range')
    _run(pid, b'\x55\x8B\xEC'                                  # push ebp; mov ebp, esp
         + b'\xA1' + struct.pack('<I', HOTKEY_BAR)             # mov eax, [hotkey bar owner]
         + b'\x8B\x80' + struct.pack('<I', HOTKEY_IDS + 4 * slot)   # mov eax, [eax + the slot's id]
         + b'\xC1\xE0\x08\x0D' + struct.pack('<I', slot)       # shl eax, 8; or eax, slot
         + b'\x50'                                             # push eax
         + b'\x8B\x0D' + struct.pack('<I', MAIN_UI)            # mov ecx, [main UI]
         + b'\xB8' + struct.pack('<I', ON_HOTKEY) + b'\xFF\xD0'   # call OnHotkey (pops its arg)
         + b'\x33\xC0\xC9\xC2\x04\x00')                        # xor eax, eax; leave; ret 4


def jump_to(pid, x, y):
    """Jumps onto the map cell (x, y) - CHero::Jump, with the game's checks (16 cells at most)."""
    _hero_call(pid, HERO_JUMP, (x, y))


def whisper(pid, to, text, channel=None):
    """Sends a chat line from this character - like typing it in the chat box: CHero::Talk(to, text,
    0, color -1, channel, 0), which builds and sends MsgTalk 1004. channel defaults to a whisper
    (2001) to the character named `to`."""
    to_b, text_b = to.encode('cp1252')[:15], text.encode('cp1252')[:250]
    channel = CHAT_WHISPER if channel is None else channel

    def code(out):                       # the two strings sit in the block's output area
        to_at, text_at = out, out + 16
        return (b'\x55\x8B\xEC'
                + b'\xB8' + struct.pack('<I', to_at)           # mov eax, to (copy the strings in)
                + b''.join(b'\xC6\x00' + bytes([c]) + b'\x40' for c in to_b + b'\0')   # mov byte [eax], c; inc eax
                + b'\xB8' + struct.pack('<I', text_at)
                + b''.join(b'\xC6\x00' + bytes([c]) + b'\x40' for c in text_b + b'\0')
                + b'\x6A\x00'                                  # push 0 (style)
                + b'\x68' + struct.pack('<I', channel)         # push the channel
                + b'\x6A\xFF'                                  # push -1 (color)
                + b'\x6A\x00'                                  # push 0
                + b'\x68' + struct.pack('<I', text_at)         # push text
                + b'\x68' + struct.pack('<I', to_at)           # push to
                + b'\x8B\x0D' + struct.pack('<I', game_memory.HERO_PTR)   # mov ecx, [hero]
                + b'\xB8' + struct.pack('<I', HERO_TALK) + b'\xFF\xD0'   # call CHero::Talk (pops 6 args)
                + b'\x33\xC0\xC9\xC2\x04\x00')

    _run(pid, code, read=16 + 256)


def remote_bank_open(pid, warehouse=10):
    """VIP > Storage > Remote Cities Warehouse (Alt+P): tells the server the warehouse is opened
    from afar; without it the server ignores a withdraw made away from the warehouse NPC."""
    vip_command(pid, warehouse)


def repair_all(pid):
    """VIP > Repair All (VIP Gold): repairs everything worn, for 1000 gold."""
    vip_command(pid, VIP_REPAIR_ALL)


def vip_command(pid, code):
    """A VIP menu command - Co.dll's own send of its message 8005 {code, 0}: 10 = open the remote
    warehouse, 30 = repair all."""
    _co_send(pid, CO_MSG_REMOTE_WH, struct.pack('<HI', code, 0))


def _co_send(pid, message, data):
    """Sends Co.dll's (the server's add-on) message `message` with `data` - through its own send."""
    base = game_memory._module_base(pid, 'Co.dll')
    if not base:
        raise OSError('Co.dll is not loaded in the game')
    _run(pid, lambda at: b'\x55\x8B\xEC'                       # push ebp; mov ebp, esp
         + b'\x68' + struct.pack('<I', len(data))              # push the length
         + b'\xBA' + struct.pack('<I', at)                     # mov edx, the data
         + b'\xB9' + struct.pack('<I', message)                # mov ecx, the message
         + b'\xB8' + struct.pack('<I', base + CO_SEND) + b'\xFF\xD0'   # call Co.dll's send
         + b'\x83\xC4\x04'                                     # add esp, 4 (the caller pops it)
         + b'\x33\xC0\xC9\xC2\x04\x00', data=data)


def compose_list(pid):
    """Asks the server for the VIP compose warehouse's items (game_memory.compose_items) - what
    opening its window does: message 8043 {1}."""
    _co_send(pid, CO_MSG_COMPOSE, struct.pack('<3I', COMPOSE_LIST, 0, 0))


def compose_withdraw(pid, item_type, amount, plus):
    """VIP > Compose > Compose Warehouse: takes `amount` x `item_type` of `plus` out into the bag -
    its Withdraw: message 8043 {3, type, plus, amount} (tested: 5 x +2 for plus 2, amount 5)."""
    _co_send(pid, CO_MSG_COMPOSE, struct.pack('<4I', COMPOSE_WITHDRAW, item_type, plus, amount))


def _note_send(pid, data):
    """Sends a message of the mail window (NoteData.dll) - what its link to Co.dll does: Co.dll's
    message 8100 with it. Works with the mail window never opened."""
    _co_send(pid, CO_MSG_MAIL, data)


def mail_search(pid, name):
    """Mail > Write: the To box's Search for the character `name` - the server's answer sets the
    receiver (game_memory.mail_receiver), only while the mail window is open."""
    _note_send(pid, struct.pack('<I32s', MAIL_SEARCH, name.encode('cp1252')[:31]))


def mail_claim_all(pid):
    """Mail: asks for the mailbox (what opening it does), then its Claim All - the items, gold and
    CPs of every mail go to this character."""
    _note_send(pid, struct.pack('<I', MAIL_LIST))
    time.sleep(1)
    _note_send(pid, struct.pack('<I', MAIL_CLAIM_ALL))


def mail_send(pid, to_id, item_ids, gold=0, cps=0, subject='', text=''):
    """Mail > Write > Send: a mail to the character with id `to_id` (mail_search) carrying the bag
    items `item_ids` (MAIL_ITEMS_MAX at most), `gold` and `cps`."""
    if len(item_ids) > MAIL_ITEMS_MAX:
        raise ValueError(f'a mail carries {MAIL_ITEMS_MAX} items at most')
    subject_b, text_b = subject.encode('cp1252')[:32], text.encode('cp1252')[:200]
    ids = list(item_ids) + [0] * (MAIL_ITEMS_MAX - len(item_ids))
    _note_send(pid, struct.pack('<5IB5IB32sB200s', MAIL_WRITE, to_id, gold & 0xFFFFFFFF, gold >> 32,
                                cps, len(item_ids), *ids, len(subject_b), subject_b, len(text_b), text_b))


def close_bank_windows(pid):
    """Closes the warehouse window and the bag (inventory) that opening the bank brings up - as if
    their close buttons were clicked: WM_COMMAND to each (MFC) dialog with its button's id. Returns
    how many were closed."""
    user32 = ctypes.windll.user32
    closed = 0
    cb = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    dialogs = []

    def _child(hwnd, _):
        name = ctypes.create_unicode_buffer(16)
        user32.GetClassNameW(hwnd, name, 16)
        if name.value == '#32770' and user32.IsWindowVisible(hwnd):
            dialogs.append(hwnd)
        return True

    def _top(hwnd, _):
        owner = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            user32.EnumChildWindows(hwnd, cb(_child), 0)
        return True

    user32.EnumWindows(cb(_top), 0)
    for dialog in dialogs:
        for button_id, other_id in CLOSE_BUTTONS:          # (its close button, a button only it has)
            button = user32.GetDlgItem(dialog, button_id)
            if button and user32.GetDlgItem(dialog, other_id):
                user32.PostMessageW(dialog, 0x111, button_id, button)   # WM_COMMAND, BN_CLICKED
                closed += 1
                break
    return closed


def bank_open(pid, key=game_memory.ARROW_BANK):
    """Loads warehouse `key` (kind, id) into this client - what opening it does: the warehouse
    manager's Open, which asks the server for its items (they arrive a moment later; read them
    with game_memory.bank_items). Does nothing if it's loaded already."""
    _run(pid, b'\x55\x8B\xEC'                                  # push ebp; mov ebp, esp
         + b'\x68' + struct.pack('<I', key[1]) + b'\x68' + struct.pack('<I', key[0])
         + b'\xB9' + struct.pack('<I', game_memory.PACKAGES)   # mov ecx, the warehouse manager
         + b'\xB8' + struct.pack('<I', BANK_OPEN) + b'\xFF\xD0'   # call Open(kind, id)
         + b'\x33\xC0\xC9\xC2\x04\x00')


def bank_refresh(pid, key=game_memory.ARROW_BANK, page=1):
    """Asks the server again for the items of an already loaded warehouse (MsgPackage 0) - what
    turning to its page does. The server only takes a withdraw after such a request."""
    _run(pid, b'\x55\x8B\xEC'
         + b'\x6A' + bytes([page])
         + b'\x68' + struct.pack('<I', key[1]) + b'\x68' + struct.pack('<I', key[0])
         + b'\xB9' + struct.pack('<I', game_memory.PACKAGES)
         + b'\xB8' + struct.pack('<I', BANK_QUERY) + b'\xFF\xD0'   # call Query(kind, id, page)
         + b'\x33\xC0\xC9\xC2\x04\x00')


def bank_deposit(pid, item_id, key):
    """Puts bag item `item_id` into the (loaded, opened) warehouse `key` - Alt+clicking it: the
    warehouse manager's Deposit, which sends MsgPackage action 1."""
    _run(pid, b'\x55\x8B\xEC'
         + b'\x68' + struct.pack('<I', item_id)
         + b'\x68' + struct.pack('<I', key[1]) + b'\x68' + struct.pack('<I', key[0])
         + b'\xB9' + struct.pack('<I', game_memory.PACKAGES)
         + b'\xB8' + struct.pack('<I', BANK_DEPOSIT) + b'\xFF\xD0'   # call Deposit(kind, id, item)
         + b'\x33\xC0\xC9\xC2\x04\x00')


def bank_withdraw(pid, item_id, key=game_memory.ARROW_BANK):
    """Takes item `item_id` out of the (loaded) warehouse `key` into the bag - clicking it there:
    the warehouse manager's Withdraw, which sends MsgPackage action 2."""
    _run(pid, b'\x55\x8B\xEC'
         + b'\x68' + struct.pack('<I', item_id)
         + b'\x68' + struct.pack('<I', key[1]) + b'\x68' + struct.pack('<I', key[0])
         + b'\xB9' + struct.pack('<I', game_memory.PACKAGES)
         + b'\xB8' + struct.pack('<I', BANK_WITHDRAW) + b'\xFF\xD0'   # call Withdraw(kind, id, item)
         + b'\x33\xC0\xC9\xC2\x04\x00')


def i_am_dead(pid):
    """True while this character is dead (status bit 10 - what CHero::Revive checks)."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return False
    try:
        hero = game_memory._u32(handle, game_memory.HERO_PTR)
        return bool(hero and game_memory._u32(handle, hero + game_memory.HERO_STATUS) & STATUS_DEAD)
    except OSError:
        return False
    finally:
        _k32.CloseHandle(handle)


def revive_here_allowed(pid):
    """True if this map lets the character 'Revive Here' (map flag 0x2000 - CHero::Revive refuses
    mode 1 without it, before sending anything)."""
    handle = _k32.OpenProcess(0x0410, False, pid)
    if not handle:
        return False
    try:
        game_map = game_memory._u32(handle, game_memory.GAME_MAP)
        return bool(game_map and game_memory._u32(handle, game_map + MAP_FLAGS) & MAP_REVIVE_HERE)
    except OSError:
        return False
    finally:
        _k32.CloseHandle(handle)


def revive(pid, here=False):
    """Revives this (dead) character - the death window's buttons: CHero::Revive(mode), which sends
    MsgAction 94. here=False: the usual revive (tested: back at the revive point); here=True: Revive
    Here, on the spot - only on maps that allow it (revive_here_allowed), else the game refuses it
    without sending. The game does nothing if the character isn't dead."""
    _hero_call(pid, HERO_REVIVE, (1 if here else 0,))


def compose_deposit(pid):
    """VIP > Compose > Compose Warehouse > Deposit: puts the compose materials (the + items) from the
    bag into the compose warehouse - the button's own function in Co.dll (the server's add-on), which
    sends its message 8043."""
    base = game_memory._module_base(pid, 'Co.dll')
    if not base:
        raise OSError('Co.dll is not loaded in the game')
    _run(pid, b'\x55\x8B\xEC'                                  # push ebp; mov ebp, esp
         + b'\xB8' + struct.pack('<I', base + CO_COMPOSE_DEPOSIT) + b'\xFF\xD0'   # call it
         + b'\x33\xC0\xC9\xC2\x04\x00')                        # xor eax, eax; leave; ret 4


def go_pick_up(pid, item_id, x, y):
    """Runs to the item `item_id` lying on (x, y) (game_memory.ground_items) and picks it up - what
    left-clicking it does: CHero::PickUp (0x683A4F) gives the hero a 'pick up' command."""
    _hero_call(pid, HERO_PICK_UP, (item_id, x, y))


def pick_up(pid, item_id, x, y):
    """Picks up the item `item_id` lying on (x, y) (game_memory.ground_items) - the message the game
    sends once you stand on it: MsgMapItem (1101) action 3. Stand on its cell first."""
    lea_ecx = b'\x8D\x8D\x00\xFB\xFF\xFF'                      # lea ecx, [ebp-0x500]  (the message)
    call = lambda addr: b'\xB8' + struct.pack('<I', addr) + b'\xFF\xD0'
    send = lea_ecx + call(MAP_MSG_SEND)
    _run(pid, b'\x55\x8B\xEC\x81\xEC\x00\x05\x00\x00'         # push ebp; mov ebp, esp; sub esp, 0x500
         + lea_ecx + call(MAP_MSG_CTOR)
         + b'\x6A\x03'                                         # push 3 (pick up)
         + b'\x68' + struct.pack('<I', y) + b'\x68' + struct.pack('<I', x)
         + b'\x68' + struct.pack('<I', item_id)
         + lea_ecx + call(MAP_MSG_CREATE)                      # Create(id, x, y, 3) pops its args
         + b'\x85\xC0' + bytes([0x74, len(send)])              # test eax, eax; jz past the send
         + send
         + lea_ecx + call(MAP_MSG_DTOR)
         + b'\x33\xC0\xC9\xC2\x04\x00')                        # xor eax, eax; leave; ret 4


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
