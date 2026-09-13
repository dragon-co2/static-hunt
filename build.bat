@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ===============================================
echo   Building run.exe
echo ===============================================
echo.

set "PYTHON_EXE="
where python >nul 2>nul
if not errorlevel 1 (
    python --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=python"
)

if not defined PYTHON_EXE (
    echo Python not found on PATH - installing Python 3.13 first, this can take a minute...
    if exist "apps\python-3.13.0-amd64.exe" (
        set "INSTALLER=apps\python-3.13.0-amd64.exe"
    ) else (
        echo   local installer not found, downloading from python.org ...
        set "INSTALLER=%TEMP%\python-installer.exe"
        curl -L -o "!INSTALLER!" "https://www.python.org/ftp/python/3.13.0/python-3.13.0-amd64.exe"
    )
    "!INSTALLER!" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
    if exist "C:\Program Files\Python313\python.exe" set "PYTHON_EXE=C:\Program Files\Python313\python.exe"
)

if not defined PYTHON_EXE (
    echo [ERROR] Could not find or install Python.
    echo         Install it manually from python.org - make sure to check
    echo         "Add python.exe to PATH" during setup - then run build.bat again.
    pause
    exit /b 1
)

echo Using Python: !PYTHON_EXE!
echo.
echo [1/2] Installing PyInstaller ...
"!PYTHON_EXE!" -m pip install --upgrade pip
"!PYTHON_EXE!" -m pip install pyinstaller
if errorlevel 1 goto :fail

echo.
echo [2/2] Building run.exe ...
"!PYTHON_EXE!" -m PyInstaller --noconfirm --clean --onefile --uac-admin --name run --distpath . --workpath build_tmp --specpath build_tmp run.py
if errorlevel 1 goto :fail

echo.
echo ===============================================
echo   Done - run.exe is right here in this folder.
echo   Double-click it, it will ask for Administrator.
echo   It checks/installs Python, updates the code and
echo   requirements, then starts static.py.
echo.
echo   You only need to re-run build.bat if you change
echo   run.py itself. Changes to static.py or anything
echo   in scripts folder take effect immediately, no
echo   rebuild needed.
echo ===============================================
pause
exit /b 0

:fail
echo.
echo [FAILED] Something went wrong above - scroll up for the actual error.
pause
exit /b 1
