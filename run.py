"""
run.exe - the one thing you double-click.

Every time it starts, it:
  1. Makes sure Python is installed (installs it silently if it's missing).
  2. If (and only if) a .git folder is sitting next to this file, installs
     git if needed and pulls the latest code. A plain copy of this folder
     (e.g. handed to someone as a zip, with no .git in it) skips this step
     entirely - there's nothing to pull, and no reason to install git just
     for that.
  3. pip-installs requirements.txt (this step always needs internet, since
     the packages come from PyPI).
  4. Runs static.py with that Python.

static.py (and everything in scripts/) is run straight from source every
time - it is NOT baked into this exe. That's on purpose: editing static.py
or the files in scripts/ takes effect the very next time you run run.exe,
with no rebuild needed. The only reason to ever rebuild run.exe itself
(with build.bat) is if you change run.py.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request


PYTHON_DOWNLOAD_URL = "https://www.python.org/ftp/python/3.13.0/python-3.13.0-amd64.exe"
GIT_LATEST_RELEASE_API = "https://api.github.com/repos/git-for-windows/git/releases/latest"


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = base_dir()
LOCAL_PYTHON_INSTALLER = os.path.join(BASE_DIR, "apps", "python-3.13.0-amd64.exe")


def _is_real_python(path):
    """Windows ships a fake python.exe stub (the Microsoft Store alias) that
    sits on PATH even when Python isn't actually installed. shutil.which()
    happily finds it, but running it does nothing useful. Confirm it can
    actually report a version before trusting it."""
    try:
        result = subprocess.run([path, "--version"], capture_output=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def find_python():
    for candidate in ("py", "python", "python3"):
        path = shutil.which(candidate)
        if path and _is_real_python(path):
            return path
    return None


def refresh_path_from_registry():
    """A just-finished installer updates PATH system-wide, but this already-
    running process doesn't see it. Pull the fresh value from the registry
    into our own os.environ so shutil.which() can find python right away."""
    import winreg

    for hive, key in (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    ):
        try:
            with winreg.OpenKey(hive, key) as k:
                value, _ = winreg.QueryValueEx(k, "Path")
                os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + value
        except OSError:
            pass


def install_python():
    print("Python not found - installing Python 3.13 ...")

    if os.path.exists(LOCAL_PYTHON_INSTALLER):
        installer_path = LOCAL_PYTHON_INSTALLER
        print(f"  using local installer: {installer_path}")
    else:
        installer_path = os.path.join(tempfile.gettempdir(), "python-installer.exe")
        print("  downloading Python installer from python.org ...")
        urllib.request.urlretrieve(PYTHON_DOWNLOAD_URL, installer_path)

    # Silent, all-users install, adds python + pip to PATH for everyone.
    # (run.exe already runs elevated, so this can write to Program Files.)
    result = subprocess.run([
        installer_path,
        "/quiet",
        "InstallAllUsers=1",
        "PrependPath=1",
        "Include_test=0",
    ])
    if result.returncode != 0:
        raise RuntimeError(f"Python installer exited with code {result.returncode}")

    refresh_path_from_registry()


def ensure_python():
    python_exe = find_python()
    if python_exe:
        return python_exe

    install_python()

    python_exe = find_python()
    if not python_exe:
        raise RuntimeError(
            "Python was installed but still isn't visible on PATH in this "
            "window. Close this window and run run.exe again - a fresh "
            "process will pick up the new PATH."
        )
    return python_exe


def _is_real_git(path):
    try:
        result = subprocess.run([path, "--version"], capture_output=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def find_git():
    path = shutil.which("git")
    if path and _is_real_git(path):
        return path
    return None


def _git_installer_url():
    with urllib.request.urlopen(GIT_LATEST_RELEASE_API, timeout=30) as resp:
        data = json.load(resp)
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if name.startswith("Git-") and name.endswith("-64-bit.exe"):
            return asset["browser_download_url"]
    raise RuntimeError("Could not find a 64-bit Git for Windows installer in the latest release.")


def install_git():
    print("Git not found - installing Git for Windows ...")
    url = _git_installer_url()
    installer_path = os.path.join(tempfile.gettempdir(), "git-installer.exe")
    print(f"  downloading {url} ...")
    urllib.request.urlretrieve(url, installer_path)

    # Silent Inno Setup install (Git for Windows' installer is Inno Setup based).
    result = subprocess.run([
        installer_path,
        "/VERYSILENT",
        "/NORESTART",
        "/NOCANCEL",
        "/SP-",
        "/SUPPRESSMSGBOXES",
    ])
    if result.returncode != 0:
        raise RuntimeError(f"Git installer exited with code {result.returncode}")

    refresh_path_from_registry()


def ensure_git():
    """Best-effort: return a git executable, installing it if missing. If
    installing fails for any reason (offline, blocked download, ...), warn
    and return None so the caller can just skip the git pull step."""
    git_exe = find_git()
    if git_exe:
        return git_exe

    try:
        install_git()
    except Exception as e:
        print(f"  could not install git automatically ({e}) - skipping git pull this run.")
        return None

    git_exe = find_git()
    if not git_exe:
        print("  git was installed but isn't visible on PATH in this window yet - skipping git pull this run.")
    return git_exe


def run(cmd):
    print(">", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=BASE_DIR)


def _same_file_bytes(path_a, path_b):
    """True if both files exist and are byte-for-byte identical."""
    try:
        if os.path.getsize(path_a) != os.path.getsize(path_b):
            return False
        with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def try_self_update():
    """If `git pull` just brought down a newer run_release.exe, swap it in
    for the run.exe that is currently executing and relaunch.

    run.exe can't overwrite itself while it's running (Windows keeps the
    running exe's file locked), so instead: the new build is copied to a
    temp folder, a tiny plain-text .bat helper is written (a .bat has no
    lock problem, so it never needs updating itself), this process exits
    to release the lock, and the .bat waits for that, then moves the new
    exe into place and starts it again.

    Only matters for the compiled exe - running `python run.py` directly
    has no lock to worry about, so this is a no-op in that case.
    """
    if not getattr(sys, "frozen", False):
        return

    release_path = os.path.join(BASE_DIR, "run_release.exe")
    current_path = sys.executable

    if not os.path.isfile(release_path):
        return
    if _same_file_bytes(release_path, current_path):
        return

    print("\n[update] A new run.exe build was pulled - restarting to apply it...")

    tmp_dir = tempfile.mkdtemp(prefix="dragon_update_")
    staged_new_exe = os.path.join(tmp_dir, "run_new.exe")
    shutil.copy2(release_path, staged_new_exe)

    pid = os.getpid()
    bat_path = os.path.join(tmp_dir, "apply_update.bat")
    bat_content = (
        "@echo off\n"
        ":wait\n"
        f'tasklist /fi "PID eq {pid}" | find "{pid}" >nul\n'
        "if not errorlevel 1 (\n"
        "    timeout /t 1 /nobreak >nul\n"
        "    goto wait\n"
        ")\n"
        f'copy /y "{staged_new_exe}" "{current_path}" >nul\n'
        f'start "" "{current_path}"\n'
        f'rmdir /s /q "{tmp_dir}" 2>nul\n'
    )
    with open(bat_path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(bat_content)

    detached = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd", "/c", bat_path], creationflags=detached, cwd=tmp_dir)
    sys.exit(0)


def main():
    os.chdir(BASE_DIR)
    python_exe = ensure_python()

    print("\n[1/3] Pulling latest code ...")
    if os.path.isdir(os.path.join(BASE_DIR, ".git")):
        git_exe = ensure_git()
        if git_exe:
            # On a machine/account git hasn't seen this exact folder on
            # before, it can refuse to touch it ("detected dubious ownership
            # in repository") purely as an ownership-mismatch safety check -
            # harmless to pre-approve since this is our own folder.
            subprocess.run([git_exe, "config", "--global", "--add", "safe.directory", BASE_DIR])
            try:
                run([git_exe, "reset", "--hard"])
                run([git_exe, "log", "--oneline", "-1"])
                run([git_exe, "pull", "--ff-only"])
            except subprocess.CalledProcessError as e:
                print(f"  (git pull failed, continuing with the code already on disk: {e})")
        else:
            print("  continuing without git pull - running the code already on disk.")
    else:
        # No .git folder here - this is a plain copy (e.g. someone got a
        # zipped version of this folder), not a git checkout. Nothing to
        # pull from, and no reason to bother installing git for that person.
        print("  no .git folder here - this looks like a standalone copy, skipping.")

    try_self_update()

    print("\n[2/3] Installing/updating requirements.txt ...")
    run([python_exe, "-m", "pip", "install", "--upgrade", "pip"])
    run([python_exe, "-m", "pip", "install", "-r", "requirements.txt"])

    print("\n[3/3] Starting static.py ...\n")
    run([python_exe, "static.py"])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        input("\nSomething went wrong - press Enter to close...")
