"""Watches the game's chat for "<our character> was killed by <killer>" and remembers who did it.

The chat is read live from the game's memory by chat_reader.ps1 (read-only: ReadProcessMemory,
never writes to the game). It runs as a child process for the whole run and prints one JSON
line per chat record; a background thread here parses them. The kill messages come on the
[System] channel in a few shapes:
    [System]Victim was killed by Killer!
    [System]Victim has been killed by Killer!
    [System]Killer has killed Victim.
Only [System] lines count, so a player typing those words in chat can't fake a kill."""
import base64
import json
import os
import re
import subprocess
import threading
import time

from _paths import app_root

READER = next((p for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chat_reader.ps1'),
                           os.path.join(app_root(), 'scripts', 'chat_reader.ps1')) if os.path.exists(p)),
              'chat_reader.ps1')
RESTART_WAIT = 5     # seconds before restarting the reader if it stops
DEDUPE_FOR   = 120   # seconds: every open game prints the same [System] line — count it once

_NAME = r'(?P<{}>[^\s\[\]]+?)'
_END  = r'[!.]*\s*(?:\[\d{4}-|$)'   # the line ends with "!" / "." and the "[2026-10-10, 23:24]" time
_PATTERNS = [
    re.compile(r'^\[System\]' + _NAME.format('victim') + r' (?:was|has been) killed by '
               + _NAME.format('killer') + _END, re.I),
    re.compile(r'^\[System\]' + _NAME.format('killer') + r' has killed ' + _NAME.format('victim') + _END, re.I),
]

_lock = threading.Lock()
_names = set()       # our characters' names, lower-case — kills of anyone else are ignored
_pending = {}        # victim (lower) -> [killer, ...] not yet handled by the main loop
_last_killer = {}    # victim (lower) -> killer of their previous death
_recent = {}         # chat text -> time.time() first seen (dedupe across game windows)
_started = False
_job = None          # Windows job object: the reader dies with this script, even on a crash


def _tie_to_us(proc):
    """Puts the reader in a job that Windows closes when this process ends — so a reader is
    never left running after the script stops (or is killed / crashes)."""
    global _job
    try:
        import win32api
        import win32con
        import win32job
        if _job is None:
            _job = win32job.CreateJobObject(None, '')
            info = win32job.QueryInformationJobObject(_job, win32job.JobObjectExtendedLimitInformation)
            info['BasicLimitInformation']['LimitFlags'] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(_job, win32job.JobObjectExtendedLimitInformation, info)
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, proc.pid)
        win32job.AssignProcessToJobObject(_job, handle)
    except Exception as e:
        print(f'[KILL] could not tie the chat reader to the script ({type(e).__name__}: {e})')


def parse_kill(text):
    """(victim, killer) if `text` is a [System] kill message, else None."""
    for pat in _PATTERNS:
        m = pat.search(text.strip())
        if m:
            return m.group('victim'), m.group('killer')
    return None


def _on_text(text):
    now = time.time()
    with _lock:
        if text in _recent:
            return
        for t in [t for t, at in _recent.items() if now - at > DEDUPE_FOR]:
            del _recent[t]
        _recent[text] = now
    kill = parse_kill(text)
    if not kill:
        return
    victim, killer = kill
    if victim.lower() not in _names:
        return
    with _lock:
        _pending.setdefault(victim.lower(), []).append(killer)
    print(f'[KILL] {victim} was killed by {killer}')


def _read_loop():
    while True:
        try:
            proc = subprocess.Popen(
                ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', READER],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as e:
            print(f'[KILL] could not start the chat reader ({e})')
            time.sleep(RESTART_WAIT * 6)
            continue
        _tie_to_us(proc)
        for raw in proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if 'status' in msg:
                print(f'[KILL] chat reader (game pid {msg.get("pid")}): {msg["status"]}')
            elif 'text' in msg:
                _on_text(base64.b64decode(msg['text']).decode('cp1252', 'replace'))
        print(f'[KILL] chat reader stopped (exit {proc.wait()}) — restarting in {RESTART_WAIT}s')
        time.sleep(RESTART_WAIT)


def start(names):
    """Starts watching (once) for kills of the given character names."""
    global _started
    with _lock:
        _names.update(n.strip().lower() for n in names if n and n.strip())
    if _started or not _names:
        return
    _started = True
    print(f'[KILL] watching the chat for kills of: {", ".join(sorted(_names))}')
    threading.Thread(target=_read_loop, daemon=True).start()


def take(name):
    """Kills of character `name` since the last call, oldest first, as
    [(killer, same_killer_as_their_previous_death), ...]."""
    key = (name or '').strip().lower()
    with _lock:
        killers = _pending.pop(key, [])
        out = []
        for killer in killers:
            out.append((killer, _last_killer.get(key, '').lower() == killer.lower()))
            _last_killer[key] = killer
    return out
