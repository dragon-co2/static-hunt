# Live download / upload speed of this PC, refreshed every second (Ctrl+C to stop).
# Counts the physical network adapters only — the Tailscale virtual adapter carries the same
# traffic again, so including it would double the numbers.
#
#   ssh -t aymanpc@100.70.5.109 powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\AymanPC\Desktop\dragon-co2\scripts\netspeed.ps1

$names = (Get-NetAdapter -Physical | Where-Object Status -eq 'Up').Name
if (-not $names) { Write-Host 'No connected physical network adapter found.'; exit 1 }
Write-Host ("Adapters: " + ($names -join ', ') + "   (Ctrl+C to stop)")

function Totals {
    $s = Get-NetAdapterStatistics -Name $names
    [pscustomobject]@{
        Rx = ($s | Measure-Object ReceivedBytes -Sum).Sum
        Tx = ($s | Measure-Object SentBytes -Sum).Sum
    }
}

$prev = Totals
while ($true) {
    Start-Sleep -Seconds 1
    $now = Totals
    $down = ($now.Rx - $prev.Rx) / 1KB
    $up   = ($now.Tx - $prev.Tx) / 1KB
    '{0:HH:mm:ss}   down {1,9:N1} KB/s   up {2,9:N1} KB/s' -f (Get-Date), $down, $up
    $prev = $now
}
