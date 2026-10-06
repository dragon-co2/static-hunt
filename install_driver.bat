@echo off
setlocal
rem Installs the Interception input driver so the game accepts the script's clicks.
rem Needs administrator rights (asks via UAC) and a restart afterwards.

set "INSTALLER=%~dp0apps\interception\install-interception.exe"

if not exist "%INSTALLER%" (
    echo Installer not found:
    echo   %INSTALLER%
    echo Pull the latest version of the project and try again.
    pause
    exit /b 1
)

rem Not running as administrator? Relaunch this file elevated (UAC prompt) and exit.
net session >nul 2>&1
if errorlevel 1 (
    echo Asking for administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo Installing the Interception driver...
echo.
"%INSTALLER%" /install
if errorlevel 1 (
    echo.
    echo Install FAILED. See the message above.
    pause
    exit /b 1
)

echo.
echo Driver installed. Windows must restart before it starts working.
echo.
choice /c YN /m "Restart now"
if errorlevel 2 (
    echo Restart later - the script keeps using normal clicks until you do.
    pause
    exit /b 0
)
shutdown /r /t 5 /c "Restarting to finish installing the Interception driver"
