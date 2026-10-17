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
This WRITES to the game: a ~100 byte stub is copied into it, run on a thread of its own, then
freed. Unlike game_memory.py it is not read-only.
    python game_actions.py <character> buy 7StarPouch 1
    python game_actions.py <character> use 7StarOintment"""
import ctypes
import ctypes.wintypes
import struct

import game_memory

MSG_CTOR, MSG_CREATE, MSG_CREATE_BUY, MSG_SEND, MSG_DTOR = (
    0x0069460F, 0x00694675, 0x00694756, 0x00688F96, 0x0069466A)
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


if __name__ == '__main__':
    import sys
    import time
    name, what, kind = sys.argv[1:4]
    pid = next(p for p in game_memory.game_pids() if game_memory._name_pid(p) == name)
    v = game_memory.vitals_pid(pid)
    print('before:', v['hp'], 'HP', v['mp'], 'MP', v['cp'], 'CP', v['potions'])
    if what == 'buy':
        buy(pid, POUCHES[kind], int(sys.argv[4]))
    else:
        use(pid, v['ids'][kind][0])
    time.sleep(1.5)
    v = game_memory.vitals_pid(pid)
    print('after: ', v['hp'], 'HP', v['mp'], 'MP', v['cp'], 'CP', v['potions'])
