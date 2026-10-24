@echo off
setlocal enabledelayedexpansion
rem First-time setup on a new PC: installs Python 3.13 and Git if they're missing, then
rem starts run_release.exe. Asks for administrator rights once (UAC) - the installers need them.
rem Downloads go through Windows' own curl.exe / PowerShell, which fetch any missing root
rem certificate by themselves (a fresh Windows can lack the one python.org needs).

net session >nul 2>&1
if errorlevel 1 (
    echo Asking for administrator rights ...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"

set "PYTHON_URL=https://www.python.org/ftp/python/3.13.0/python-3.13.0-amd64.exe"

echo ===============================================
echo   Dragon CO2 - setup
echo ===============================================
echo.

rem ---------- Python ----------
echo [1/3] Python ...
call :has_python
if defined PY (
    echo   already installed.
    goto git
)
set "INSTALLER=%~dp0apps\python-3.13.0-amd64.exe"
if exist "%INSTALLER%" (
    echo   using local installer: %INSTALLER%
) else (
    set "INSTALLER=%TEMP%\python-installer.exe"
    call :download "%PYTHON_URL%" "!INSTALLER!" || goto fail
)
echo   installing Python 3.13 - this can take a minute ...
"%INSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
if errorlevel 1 (
    echo   [ERROR] the Python installer failed.
    goto fail
)
call :refresh_path
call :has_python
if not defined PY (
    echo   [ERROR] Python was installed but can't be found.
    goto fail
)
echo   done.

:git
rem ---------- Git ----------
echo.
echo [2/3] Git ...
git --version >nul 2>&1
if not errorlevel 1 (
    echo   already installed.
    goto start
)
set "GIT_URL="
for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $r=Invoke-RestMethod -UseBasicParsing 'https://api.github.com/repos/git-for-windows/git/releases/latest'; ($r.assets | Where-Object { $_.name -match '^Git-.*-64-bit\.exe$' } | Select-Object -First 1).browser_download_url"`) do set "GIT_URL=%%U"
if not defined GIT_URL (
    echo   [ERROR] could not find the latest Git for Windows download.
    goto fail
)
set "INSTALLER=%TEMP%\git-installer.exe"
call :download "%GIT_URL%" "%INSTALLER%" || goto fail
echo   installing Git - this can take a minute ...
"%INSTALLER%" /VERYSILENT /NORESTART /NOCANCEL /SP- /SUPPRESSMSGBOXES
if errorlevel 1 (
    echo   [ERROR] the Git installer failed.
    goto fail
)
call :refresh_path
git --version >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Git was installed but can't be found.
    goto fail
)
echo   done.

:start
rem ---------- run_release.exe ----------
echo.
echo [3/3] Starting run_release.exe ...
rem A zip from make_zip.bat holds only this file and .git: unpack the project from .git first.
rem (Only when run_release.exe is missing - on a folder already in use this never resets anything.)
if not exist "%~dp0run_release.exe" if exist "%~dp0.git\" (
    echo   unpacking the project files from .git ...
    git config --global --add safe.directory "%CD:\=/%" >nul 2>&1
    git reset -q --hard || goto fail
)
if not exist "%~dp0run_release.exe" (
    echo   [ERROR] run_release.exe is not next to this file.
    goto fail
)
start "" "%~dp0run_release.exe"
echo   started - this window can be closed.
timeout /t 5 >nul
exit /b 0

:fail
echo.
echo [FAILED] Setup did not finish - see the message above.
pause
exit /b 1

rem ---------- helpers ----------

:has_python
rem Sets PY to a working Python ("py" or "python"); empty if none. Windows' fake python.exe
rem (the Microsoft Store shortcut) fails "--version", so it doesn't count.
set "PY="
py --version >nul 2>&1 && set "PY=py"
if not defined PY python --version >nul 2>&1 && set "PY=python"
exit /b 0

:download
rem :download <url> <file> - Windows' curl.exe first, then PowerShell.
echo   downloading %~1 ...
curl.exe -L --fail -o "%~2" "%~1"
if not errorlevel 1 exit /b 0
echo   curl failed - trying PowerShell ...
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri '%~1' -OutFile '%~2'"
if not errorlevel 1 exit /b 0
echo   [ERROR] could not download %~1
exit /b 1

:refresh_path
rem The installers update PATH for new windows only - pull the new value into this one, so
rem run_release.exe (started from here) finds Python and Git straight away.
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%P"
exit /b 0
