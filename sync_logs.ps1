<#
.SYNOPSIS
  Pull the ORB bot's logs and state down from the VPS into this repo.

.DESCRIPTION
  Since 2026-09-17 the bot runs on the Hetzner box, so logs/ in this folder no
  longer fills itself. Nothing here reads the VPS automatically -- neither the
  sandbox nor the local shell can reach port 22 -- so this is the one command
  that makes a session reviewable from the laptop.

  Safe to run any time: scp overwrites same-named files, and finished session
  logs never change after ~15:50 ET.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File sync_logs.ps1
#>
param(
    [string]$Server = "root@157.180.31.101",
    [string]$RemoteDir = "/opt/orb-bot"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path

New-Item -ItemType Directory -Force -Path "$repo\logs", "$repo\state" | Out-Null

Write-Host "Pulling logs from $Server ..." -ForegroundColor Cyan
# NOTE: no trailing backslash inside the quotes -- it escapes the quote and
# scp then sees a path ending in `"` ("Invalid argument"). Cost us a round
# trip on 2026-09-20.
scp -r "${Server}:${RemoteDir}/logs/*" "$repo\logs"
scp    "${Server}:${RemoteDir}/state/trade_history.csv" "$repo\state"
scp    "${Server}:${RemoteDir}/state/daily_state.json"  "$repo\state"

Write-Host "`nSession integrity check:" -ForegroundColor Cyan
python "$repo\invariants.py"

$today = Get-Date -Format "yyyy-MM-dd"
$log = "$repo\logs\orb_$today.log"
if (Test-Path $log) {
    Write-Host "`nToday's summary:" -ForegroundColor Cyan
    Select-String -Path $log -Pattern "Day done|END-OF-DAY SUMMARY" -Context 0,12 |
        Select-Object -Last 1 | ForEach-Object { $_.Context.PostContext, $_.Line }
} else {
    Write-Host "`nNo log for $today (weekend/holiday, or the session never ran)." -ForegroundColor Yellow
}
