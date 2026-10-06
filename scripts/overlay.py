"""On-screen overlays on the primary monitor — always on top, click-through, transparent:
  * console: the last few printed lines          (settings under 'overlay')
  * timers:  a status block, e.g. the countdowns (settings under 'overlay_timers')

Usage: overlay.start() once, then overlay.push(line) for every console line and
overlay.set_status(text) whenever the status block should change."""
import ctypes
import queue
import threading

from dragon_settings import get_settings

_S = get_settings('overlay', {
    'ENABLED':   True,
    'POSITION':  'top-center',
    'LINES':     5,
    'FONT_SIZE': 14,
    'COLOR':     '#00ff00',
    'MARGIN':    10,
})
_T = get_settings('overlay_timers', {
    'ENABLED':   True,
    'POSITION':  'top-right',
    'FONT_SIZE': 14,
    'COLOR':     '#00ff00',
    'MARGIN':    10,
})

_C = get_settings('overlay_curtain', {'ENABLED': False})

ENABLED         = bool(_S['ENABLED'])
LINES           = int(_S['LINES'])
TIMER_ENABLED   = bool(_T['ENABLED'])
CURTAIN_ENABLED = bool(_C['ENABLED'])

_KEY = '#010101'  # background color made fully transparent (must differ from the text colors)
CURTAIN_COLOR = '#020103'  # looks black; an exact, unusual colour so a capture can tell it's the curtain

_queue = queue.Queue()
_status = {'text': ''}
_started = False


def push(line):
    if _started and ENABLED:
        _queue.put(line)


def set_status(text):
    """Replaces the whole timers block (multi-line text is fine)."""
    _status['text'] = text


def _exclude_from_capture(hwnd):
    """Hides the window from screen captures (mss / pyautogui / PIL) while it stays visible on
    the display itself — and so in an RDP session. The script captures the screen to find the
    game's buttons, so the overlay must never show up in those captures. Windows 10 2004+."""
    WDA_EXCLUDEFROMCAPTURE = 0x11
    ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)


def _make_click_through(win):
    """Lets every mouse click pass through to the game underneath, and keeps the overlay
    out of the taskbar / Alt+Tab and from ever taking focus."""
    GWL_EXSTYLE       = -20
    WS_EX_LAYERED     = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW  = 0x00000080
    WS_EX_NOACTIVATE  = 0x08000000
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(win.winfo_id())
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                          style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)


class _Panel:
    """One transparent, click-through text block pinned to a screen position."""

    def __init__(self, root, cfg):
        import tkinter as tk
        self.position = cfg['POSITION']  # top-left | top-center | top-right | center | bottom-left | bottom-center | bottom-right
        self.margin = int(cfg['MARGIN'])  # px gap from the screen edge

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.configure(bg=_KEY)
        self.win.attributes('-transparentcolor', _KEY)

        anchor = {'left': 'w', 'right': 'e'}.get(self.position.rpartition('-')[2], 'center')
        justify = {'w': 'left', 'e': 'right'}.get(anchor, 'center')
        self.label = tk.Label(self.win, text='', fg=cfg['COLOR'], bg=_KEY,
                              font=('Consolas', int(cfg['FONT_SIZE']), 'bold'),
                              justify=justify, anchor=anchor)
        self.label.pack()
        self.win.update()
        self.hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id())
        _make_click_through(self.win)
        _exclude_from_capture(self.hwnd)
        self._place()

    def _place(self):
        """Sizes the window to its text and pins it to POSITION — both in Tk's own geometry and
        straight through SetWindowPos, since Tk alone doesn't reliably move these overrideredirect
        click-through Toplevels."""
        self.win.update_idletasks()
        w, h = self.label.winfo_reqwidth(), self.label.winfo_reqheight()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        vert, _, horiz = self.position.partition('-')
        if self.position == 'center':
            vert, horiz = 'center', 'center'
        m = self.margin
        x = {'left': m, 'right': sw - w - m}.get(horiz, (sw - w) // 2)
        y = {'top': m, 'bottom': sh - h - m}.get(vert, (sh - h) // 2)
        self.win.geometry(f'{w}x{h}+{x}+{y}')  # keep Tk's record in sync, or it moves the window back later
        self.win.update_idletasks()
        HWND_TOPMOST, SWP_NOACTIVATE = -1, 0x0010
        ctypes.windll.user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)

    def set_text(self, text):
        if self.label.cget('text') != text:
            self.label.config(text=text)
            self._place()

    def keep_on_top(self):
        self.win.attributes('-topmost', True)  # stay above the game even after it grabs focus


class _Curtain:
    """Solid black window over the whole primary screen, under the text panels. On the display
    (and an RDP session watching it) the picture is just black + the log, so RDP has almost
    nothing to send — while the script's captures don't see it at all (excluded from capture)
    and clicks pass through to the game."""

    def __init__(self, root):
        import tkinter as tk
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.configure(bg=CURTAIN_COLOR)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f'{sw}x{sh}+0+0')
        self.win.update()
        self.hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id())
        _make_click_through(self.win)
        _exclude_from_capture(self.hwnd)
        HWND_TOPMOST, SWP_NOACTIVATE = -1, 0x0010
        ctypes.windll.user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, sw, sh, SWP_NOACTIVATE)
        self.ok = self._hidden_from_capture()
        if not self.ok:
            self.win.destroy()

    def _hidden_from_capture(self):
        """The capture exclusion isn't honoured everywhere (e.g. inside an RDP session), and a
        curtain the script can see would blind it. Capture the screen once: if most of it is
        exactly the curtain's colour, the script sees the curtain — so it must go."""
        import time
        import numpy as np
        import screen
        self.win.update()
        time.sleep(0.5)
        bgr = screen.grab_bgr()
        r, g, b = (int(CURTAIN_COLOR[i:i + 2], 16) for i in (1, 3, 5))
        share = float(((bgr[:, :, 2] == r) & (bgr[:, :, 1] == g) & (bgr[:, :, 0] == b)).mean())
        if share > 0.5:
            print('[OVERLAY] black screen is visible to the script here (capture exclusion not '
                  'supported in this session) — turned it off so the script can still see the game')
            return False
        print('[OVERLAY] black screen on (hidden from the script)')
        return True

    def keep_on_top(self):
        self.win.attributes('-topmost', True)


def _run():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()  # invisible owner; each panel is its own Toplevel
    curtain = _Curtain(root) if CURTAIN_ENABLED else None  # created first: the text panels sit above it
    if curtain and not curtain.ok:
        curtain = None
    console = _Panel(root, _S) if ENABLED else None
    timers = _Panel(root, _T) if TIMER_ENABLED else None
    lines = []

    def _poll():
        if curtain:
            curtain.keep_on_top()  # re-assert first, so the panels re-asserted below stay above it
        if console:
            changed = False
            while True:
                try:
                    lines.append(_queue.get_nowait())
                    changed = True
                except queue.Empty:
                    break
            if changed:
                del lines[:-LINES]
                console.set_text('\n'.join(lines))
            console.keep_on_top()
        if timers:
            timers.set_text(_status['text'])
            timers.keep_on_top()
        root.after(100, _poll)

    _poll()
    root.mainloop()


def start():
    """Starts the overlays on their own thread (tkinter lives entirely on that thread)."""
    global _started
    if _started or not (ENABLED or TIMER_ENABLED or CURTAIN_ENABLED):
        return
    _started = True
    threading.Thread(target=_run, daemon=True, name='overlay').start()
