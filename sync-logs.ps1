# Pull the bot's logs and state down from the VPS to this laptop.
#
# The bot runs on Hetzner (157.180.31.101) and writes its logs there. The daily
# recap task reads logs/ in THIS folder, so it goes stale unless you run this.
# A blank or old recap means you haven't synced -- it does NOT mean the bot
# failed to trade. The server's copy is always the authoritative one.
#
# Usage (PowerShell, from the repo folder):   .\sync-logs.ps1
#
# NOTE (2026-09-20): the destination paths deliberately have NO trailing
# backslash. Inside a double-quoted PowerShell string a trailing \ escapes
# the closing quote, so scp sees a path ending in `"` and fails with
# "Invalid argument" / "No such file or directory". That bit us on 09-20.

$Server = "root@157.180.31.101"
$Remote = "/opt/orb-bot"
$Local  = "C:\Users\ethan\Claude\Projects\Trading bot"

New-Item -ItemType Directory -Force -Path "$Local\logs", "$Local\state" | Out-Null

Write-Host "Pulling logs from $Server ..." -ForegroundColor Cyan

scp -r "${Server}:${Remote}/logs/*" "$Local\logs"
if ($LASTEXITCODE -ne 0) { Write-Host "Log sync FAILED." -ForegroundColor Red; exit 1 }

Write-Host "Pulling state ..." -ForegroundColor Cyan

scp "${Server}:${Remote}/state/trade_history.csv" "$Local\state"
scp "${Server}:${Remote}/state/daily_state.json"  "$Local\state"

$today = Get-Date -Format "yyyy-MM-dd"
$todayLog = "$Local\logs\orb_$today.log"

Write-Host ""
if (Test-Path $todayLog) {
    Write-Host "OK - today's log ($today) is here." -ForegroundColor Green
    Get-Content $todayLog -Tail 6
} else {
    Write-Host "WARNING: no log for $today." -ForegroundColor Yellow
    Write-Host "Either the market is closed today, or the session did not start." -ForegroundColor Yellow
    Write-Host "Check on the server:  systemctl status orb-bot" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Session integrity check:" -ForegroundColor Cyan
python "$Local\invariants.py"
