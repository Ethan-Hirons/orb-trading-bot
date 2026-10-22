<#
.SYNOPSIS
  Pull the PAPER instance's trade history and logs down from the VPS.

.DESCRIPTION
  replay.py reads the LOCAL state/trade_history.csv and logs/. The bot runs on
  the VPS, so those local copies go stale the moment a session trades. Run this
  before any E12 holdout check or the window will look emptier than it is.

  PAPER is the source for the trial history (the 100-trade baseline is paper's).
  Live keeps its own separate, much shorter history.

  Existing local files are backed up to state/_backup/ first - the VPS is the
  source of truth, but this never destroys the only copy of anything.

.EXAMPLE
  .\sync-from-vps.ps1
  .\sync-from-vps.ps1 -WhatIf      # show what would change, touch nothing
#>
param([switch]$WhatIf)

$Server = "root@157.180.31.101"
$Paper  = "/opt/orb-bot"
$Stamp  = Get-Date -Format "yyyy-MM-dd-HHmm"

Write-Host "Remote (paper) trade count:" -ForegroundColor Cyan
ssh $Server "wc -l < $Paper/state/trade_history.csv"
Write-Host "Local trade count:" -ForegroundColor Cyan
if (Test-Path state\trade_history.csv) { (Get-Content state\trade_history.csv).Count } else { "none" }

if ($WhatIf) { Write-Host "`n-WhatIf: nothing copied." -ForegroundColor Yellow; exit 0 }

New-Item -ItemType Directory -Force -Path state\_backup | Out-Null
if (Test-Path state\trade_history.csv) {
    Copy-Item state\trade_history.csv "state\_backup\trade_history.$Stamp.csv"
    Write-Host "backed up -> state\_backup\trade_history.$Stamp.csv" -ForegroundColor DarkGray
}

scp "${Server}:$Paper/state/trade_history.csv"  state\trade_history.csv
scp "${Server}:$Paper/state/trade_features.csv" state\trade_features.csv
scp "${Server}:$Paper/logs/orb_2026-*.log"      logs\

Write-Host "`nLocal trade count now:" -ForegroundColor Green
(Get-Content state\trade_history.csv).Count
Write-Host "`nNow run:  python -u replay.py --robust --since 2026-10-02" -ForegroundColor Cyan
