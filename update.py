import os
import shutil
import subprocess
import sys


def _base_dir():
    """Folder that should be the working directory for git pull / pip
    install: the .exe's own folder when frozen, otherwise this .py file's
    folder (the project root, since update.py lives next to .git/)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _python_for_pip():
    """A real Python interpreter to run `-m pip` with.

    When this script runs as plain update.py, sys.executable IS that
    interpreter. When it's frozen into update.exe, sys.executable is the
    .exe itself (running -m pip on it would just try to relaunch update.exe),
    so we need to find an actual Python installed on the machine instead.
    """
    if not getattr(sys, 'frozen', False):
        return [sys.executable]
    for candidate in (shutil.which("py"), shutil.which("python"), shutil.which("python3")):
        if candidate:
            return [candidate]
    raise RuntimeError(
        "No Python interpreter found on PATH - it's needed to run "
        "'pip install -r requirements.txt'. Install Python and make sure "
        "it's on PATH, then run update again."
    )


def update():
    os.chdir(_base_dir())
    try:
        subprocess.run(["git", "reset", "--hard"])
        subprocess.run(["git", "log", "-1"])
        subprocess.run(["git", "status"])
        subprocess.run(["git", "pull"], check=True)
        subprocess.run(_python_for_pip() + ["-m", "pip", "install", "-r", "requirements.txt"], check=True)
    except Exception as e:
        print(f"{e}")


if __name__ == "__main__":
    update()
    input("\nDone - press Enter to close...")
