# One-shot health check of this PC for the bot, readable over SSH:
#   ssh aymanpc@100.70.5.109 powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\AymanPC\Desktop\dragon-co2\scripts\status.ps1

$root = Split-Path $PSScriptRoot -Parent
function Section($t) { Write-Host ''; Write-Host "=== $t ===" }

Section 'Session (want: console + Active)'
query user 2>&1 | ForEach-Object { "  $_" }

Section 'Displays'
Get-CimInstance Win32_VideoController | ForEach-Object {
    $res = if ($_.CurrentHorizontalResolution) { "$($_.CurrentHorizontalResolution)x$($_.CurrentVerticalResolution)" } else { 'inactive' }
    '  {0,-45} {1,-12} {2}' -f $_.Name, $res, $_.Status
}
$vdd = Get-PnpDevice -Class Display -ErrorAction SilentlyContinue | Where-Object FriendlyName -match 'Virtual'
foreach ($d in $vdd) { '  virtual display driver: {0} ({1})' -f $d.FriendlyName, $d.Status }

Section 'Bot processes (want: run_release + python)'
$procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(run_release|python|py)\.exe$' }
if ($procs) { $procs | ForEach-Object { '  {0,-16} pid {1,-6} {2}' -f $_.Name, $_.ProcessId, ($_.CommandLine -replace '^.*\\', '') } }
else { '  none running' }

Section 'Game windows'
$games = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match '^conquer$' }
if ($games) { $games | ForEach-Object { '  {0} pid {1}  responding={2}' -f $_.ProcessName, $_.Id, $_.Responding } }
else { '  no conquer.exe running' }

Section 'Screen capture (want: no recent "cannot capture")'
$log = Join-Path $root 'logs\static.log'
if (Test-Path $log) {
    $tail = Get-Content $log -Tail 200 -Encoding UTF8
    $bad = $tail | Where-Object { $_ -match 'cannot capture the screen' } | Select-Object -Last 1
    $ok = $tail | Where-Object { $_ -match 'screen is available again' } | Select-Object -Last 1
    if ($bad) { "  last problem: $bad" } else { '  no capture problems in the last 200 lines' }
    if ($ok) { "  last recovery: $ok" }
    Section 'Last 10 log lines'
    Get-Content $log -Tail 10 -Encoding UTF8 | ForEach-Object { "  $_" }
} else { "  no log yet ($log)" }
