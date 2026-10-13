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
    'SHADOW':    True,
})
_T = get_settings('overlay_timers', {
    'ENABLED':   True,
    'POSITION':  'top-right',
    'FONT_SIZE': 14,
    'COLOR':     '#00ff00',
    'MARGIN':    10,
    'SHADOW':    True,
})


ENABLED         = bool(_S['ENABLED'])
LINES           = int(_S['LINES'])
TIMER_ENABLED   = bool(_T['ENABLED'])

_KEY = '#010101'  # background color made fully transparent (must differ from the text colors)
SHADOW_COLOR  = '#000000'   # drop shadow behind the text, so it reads on light game backgrounds too
SHADOW_OFFSET = 2           # px down and to the right

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

        import tkinter.font as tkfont
        side = self.position.rpartition('-')[2]
        self.justify = {'left': 'left', 'right': 'right'}.get(side, 'center')
        self.color = cfg['COLOR']
        self.shadow = SHADOW_OFFSET if cfg['SHADOW'] else 0
        self.font = tkfont.Font(root=root, family='Consolas', size=int(cfg['FONT_SIZE']), weight='bold')
        # a canvas, not a label: the text is drawn twice — the shadow, then the text over it
        self.canvas = tk.Canvas(self.win, bg=_KEY, highlightthickness=0, bd=0, width=1, height=1)
        self.canvas.pack()
        self.text = ''
        self.w, self.h = 1, 1
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
        w, h = self.w, self.h
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

    def _draw(self):
        """Redraws the text (and its shadow) and sizes the canvas to fit it."""
        lines = self.text.split('\n') if self.text else ['']
        pad = 2
        self.w = max(self.font.measure(line) for line in lines) + 2 * pad + self.shadow
        self.h = self.font.metrics('linespace') * len(lines) + 2 * pad + self.shadow
        self.canvas.config(width=self.w, height=self.h)
        self.canvas.delete('all')
        anchor, x = {'left': ('nw', pad), 'right': ('ne', self.w - pad - self.shadow)}.get(
            self.justify, ('n', (self.w - self.shadow) // 2))
        if self.shadow:
            self.canvas.create_text(x + self.shadow, pad + self.shadow, text=self.text, anchor=anchor,
                                    justify=self.justify, font=self.font, fill=SHADOW_COLOR)
        self.canvas.create_text(x, pad, text=self.text, anchor=anchor, justify=self.justify,
                                font=self.font, fill=self.color)

    def set_text(self, text):
        if self.text != text:
            self.text = text
            self._draw()
            self._place()

    def keep_on_top(self):
        self.win.attributes('-topmost', True)  # stay above the game even after it grabs focus


def _run():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()  # invisible owner; each panel is its own Toplevel
    console = _Panel(root, _S) if ENABLED else None
    timers = _Panel(root, _T) if TIMER_ENABLED else None
    lines = []

    def _poll():
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
    if _started or not (ENABLED or TIMER_ENABLED):
        return
    _started = True
    threading.Thread(target=_run, daemon=True, name='overlay').start()
