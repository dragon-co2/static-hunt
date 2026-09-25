"""On-screen console: shows the last few printed lines in an always-on-top, click-through,
transparent window on the primary monitor. Settings live under 'overlay' in settings_schema.json.

Usage: overlay.start() once, then overlay.push(line) for every line to show."""
import ctypes
import queue
import threading

from dragon_settings import get_settings

_S = get_settings('overlay', {
    'ENABLED':   1,
    'POSITION':  'top-center',
    'LINES':     5,
    'FONT_SIZE': 14,
    'COLOR':     '#00ff00',
    'MARGIN':    10,
})

ENABLED   = bool(_S['ENABLED'])
POSITION  = _S['POSITION']    # top-left | top-center | top-right | center | bottom-left | bottom-center | bottom-right
LINES     = int(_S['LINES'])
FONT_SIZE = int(_S['FONT_SIZE'])
COLOR     = _S['COLOR']
MARGIN    = int(_S['MARGIN'])  # px gap from the screen edge

_KEY = '#010101'  # background color made fully transparent (must differ from COLOR)

_queue = queue.Queue()
_started = False


def push(line):
    if _started:
        _queue.put(line)


def _make_click_through(root):
    """Lets every mouse click pass through to the game underneath, and keeps the overlay
    out of the taskbar / Alt+Tab and from ever taking focus."""
    GWL_EXSTYLE       = -20
    WS_EX_LAYERED     = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW  = 0x00000080
    WS_EX_NOACTIVATE  = 0x08000000
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(root.winfo_id())
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                          style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)


def _place(root):
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    vert, _, horiz = POSITION.partition('-')
    if POSITION == 'center':
        vert, horiz = 'center', 'center'
    x = {'left': MARGIN, 'right': sw - w - MARGIN}.get(horiz, (sw - w) // 2)
    y = {'top': MARGIN, 'bottom': sh - h - MARGIN}.get(vert, (sh - h) // 2)
    root.geometry(f'+{x}+{y}')


def _run():
    import tkinter as tk

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes('-topmost', True)
    root.configure(bg=_KEY)
    root.attributes('-transparentcolor', _KEY)

    anchor = {'left': 'w', 'right': 'e'}.get(POSITION.rpartition('-')[2], 'center')
    justify = {'w': 'left', 'e': 'right'}.get(anchor, 'center')
    label = tk.Label(root, text='', fg=COLOR, bg=_KEY, font=('Consolas', FONT_SIZE, 'bold'),
                     justify=justify, anchor=anchor)
    label.pack()

    lines = []

    def _poll():
        changed = False
        while True:
            try:
                lines.append(_queue.get_nowait())
                changed = True
            except queue.Empty:
                break
        if changed:
            del lines[:-LINES]
            label.config(text='\n'.join(lines))
            _place(root)
        root.attributes('-topmost', True)  # stay above the game even after it grabs focus
        root.after(100, _poll)

    _place(root)
    root.update()
    _make_click_through(root)
    _poll()
    root.mainloop()


def start():
    """Starts the overlay on its own thread (tkinter lives entirely on that thread)."""
    global _started
    if not ENABLED or _started:
        return
    _started = True
    threading.Thread(target=_run, daemon=True, name='overlay').start()
