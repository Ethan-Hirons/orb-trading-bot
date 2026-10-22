<#
.SYNOPSIS
  Pull BOTH the paper and live instances' logs and state down from the VPS.

.DESCRIPTION
  sync-logs.ps1 and sync-from-vps.ps1 only ever pull the PAPER instance
  (/opt/orb-bot). The live instance (/opt/orb-bot-live) was never synced by
  either, which is why live\logs\ went stale on 2026-10-12.

  This pulls both, keeping them separate on disk:

      /opt/orb-bot/logs       -> logs\          (paper)
      /opt/orb-bot-live/logs  -> live\logs\     (live)

  Missing days are normal and are reported, not treated as errors: the live
  instance is manual-arm only, so it has far fewer sessions than paper.

  NOTE: destination paths deliberately have NO trailing backslash. Inside a
  double-quoted PowerShell string a trailing \ escapes the closing quote and
  scp fails with "Invalid argument". That bit us on 2026-09-20.

.EXAMPLE
  .\sync-both.ps1              # today + the last 5 days
  .\sync-both.ps1 -Days 30     # today + the last 30 days
  .\sync-both.ps1 -All         # every orb_2026-*.log on the server
#>
param(
    [int]$Days = 5,
    [switch]$All
)

$Server = "root@157.180.31.101"
$Local  = "C:\Users\ethan\Claude\Projects\Trading bot"

$Instances = @(
    @{ Name = "paper"; Remote = "/opt/orb-bot";      Logs = "$Local\logs";      State = "$Local\state" },
    @{ Name = "live";  Remote = "/opt/orb-bot-live"; Logs = "$Local\live\logs"; State = "$Local\live\state" }
)

foreach ($i in $Instances) {
    New-Item -ItemType Directory -Force -Path $i.Logs, $i.State | Out-Null
}

# Dates to try: today first, then backwards.
$dates = @(0..$Days | ForEach-Object { (Get-Date).AddDays(-$_).ToString("yyyy-MM-dd") })
$today = $dates[0]

$report = @{}

foreach ($i in $Instances) {
    $name = $i.Name
    Write-Host ""
    Write-Host "=== $($name.ToUpper())  ($($i.Remote)) ===" -ForegroundColor Cyan
    $report[$name] = @()

    if ($All) {
        scp -q "$($Server):$($i.Remote)/logs/orb_2026-*.log" $i.Logs 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  pulled all orb_2026-*.log" -ForegroundColor Green
        } else {
            Write-Host "  no logs matched" -ForegroundColor Yellow
        }
    }
    else {
        foreach ($d in $dates) {
            scp -q "$($Server):$($i.Remote)/logs/orb_$d.log" $i.Logs 2>$null
            if ($LASTEXITCODE -eq 0) {
                $report[$name] += $d
                Write-Host "  $d  ok" -ForegroundColor Green
            } else {
                Write-Host "  $d  --" -ForegroundColor DarkGray
            }
        }
    }

    # State files. trade_history is the one that matters; daily_state is a nicety.
    scp -q "$($Server):$($i.Remote)/state/trade_history.csv" $i.State 2>$null
    scp -q "$($Server):$($i.Remote)/state/trade_features.csv" $i.State 2>$null
    scp -q "$($Server):$($i.Remote)/state/daily_state.json"  $i.State 2>$null

    $hist = Join-Path $i.State "trade_history.csv"
    if (Test-Path $hist) {
        $n = [Math]::Max(0, (Get-Content $hist).Count - 1)
        Write-Host "  trade_history.csv: $n trades" -ForegroundColor Gray
    } else {
        Write-Host "  trade_history.csv: not present" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "=== TODAY ($today) ===" -ForegroundColor Cyan
foreach ($i in $Instances) {
    $f = Join-Path $i.Logs "orb_$today.log"
    if (Test-Path $f) {
        Write-Host ""
        Write-Host "--- $($i.Name) ---" -ForegroundColor Green
        Get-Content $f -Tail 6
    } else {
        Write-Host "  $($i.Name): no log for $today" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Paper logs -> $Local\logs" -ForegroundColor DarkGray
Write-Host "Live  logs -> $Local\live\logs" -ForegroundColor DarkGray
