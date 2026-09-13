"""
Shared path helpers.

When the project runs as plain .py files, `__file__` points at real files on
disk and the old "go up from scripts/ to find reference_images/" logic works
fine. Once PyInstaller freezes everything into a single .exe, the scripts get
extracted into a temporary folder at startup and `__file__` no longer points
anywhere near the real project folder - so any code that still does
`os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` ends up
looking for reference_images/ inside a temp folder that doesn't have it.

app_root() fixes this: when frozen (sys.frozen is set by PyInstaller) it
returns the folder that contains the .exe itself. That's where the build
script copies reference_images/ (and requirements.txt for update.exe), so
everything keeps working exactly like it did as loose .py files - you just
need to keep reference_images/ sitting next to the .exe files.
"""

import os
import sys


def app_root():
    """Project root: folder with reference_images/ (source) or the folder
    containing the .exe (frozen build)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    # scripts/_paths.py -> up one level = project root (co2/)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def scripts_dir():
    """scripts/ folder (source) or the folder containing the .exe (frozen build)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))
