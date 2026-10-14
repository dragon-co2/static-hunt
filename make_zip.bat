@echo off
setlocal
rem Packs what someone needs to run the bot into dragon-co2_bank_compose_<version>.zip, next to
rem this file. <version> is the last commit's version (e.g. "6.2.12 ..." -> 6.2.12).
rem The zip holds one folder of the same name; inside it: setup.bat (run it first),
rem run_release.exe and the rest. It's a plain copy (no .git), so it doesn't pull updates.
cd /d "%~dp0"

set "VERSION="
for /f "tokens=1" %%V in ('git log -1 --format^=%%s 2^>nul') do set "VERSION=%%V"
if not defined VERSION (
    for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "VERSION=%%D"
    echo Could not read the version from git - using the date instead.
)
set "NAME=dragon-co2_bank_compose_%VERSION%"
set "STAGE=%TEMP%\%NAME%"
set "ZIP=%~dp0%NAME%.zip"

echo Packing %NAME%.zip ...
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%"

for %%D in (apps reference_images scripts) do (
    if not exist "%%D\" (
        echo   [ERROR] folder %%D is missing.
        goto fail
    )
    robocopy "%%D" "%STAGE%\%%D" /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 (
        echo   [ERROR] could not copy %%D.
        goto fail
    )
)
for %%F in (install_driver.bat requirements.txt run_release.exe setup.bat static.py update.py user_settings.bat) do (
    if not exist "%%F" (
        echo   [ERROR] file %%F is missing.
        goto fail
    )
    copy /y "%%F" "%STAGE%\" >nul || goto fail
)

if exist "%ZIP%" del /q "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path '%STAGE%' -DestinationPath '%ZIP%' -CompressionLevel Optimal"
if errorlevel 1 goto fail
rmdir /s /q "%STAGE%"

for %%Z in ("%ZIP%") do echo Done: %%~nxZ (%%~zZ bytes)
pause
exit /b 0

:fail
if exist "%STAGE%" rmdir /s /q "%STAGE%"
echo [FAILED] the zip was not made - see the message above.
pause
exit /b 1
