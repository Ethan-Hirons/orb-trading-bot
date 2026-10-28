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
      /opt/orb-noise/logs     -> noise\logs\    (E16 QQQ)

  The E16 instance keeps a different record: noise_<day>.log per session,
  noise_eod_<day>.log from the 16:40 evening job, and state\noise_ledger.csv
  (one row per checkpoint). Its log prefix is noise_, not orb_, so each
  instance carries its own prefix and state-file list below.

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

$OrbState = @("trade_history.csv", "trade_features.csv", "daily_state.json")

$Instances = @(
    @{ Name = "paper"; Remote = "/opt/orb-bot";      Logs = "$Local\logs";       State = "$Local\state";       Prefix = "orb";   Files = $OrbState },
    @{ Name = "live";  Remote = "/opt/orb-bot-live"; Logs = "$Local\live\logs";  State = "$Local\live\state";  Prefix = "orb";   Files = $OrbState },
    @{ Name = "noise"; Remote = "/opt/orb-noise";    Logs = "$Local\noise\logs"; State = "$Local\noise\state"; Prefix = "noise"; Files = @("noise_ledger.csv") }
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
        scp -q "$($Server):$($i.Remote)/logs/$($i.Prefix)_2026-*.log" $i.Logs 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  pulled all $($i.Prefix)_2026-*.log" -ForegroundColor Green
        } else {
            Write-Host "  no logs matched" -ForegroundColor Yellow
        }
    }
    else {
        foreach ($d in $dates) {
            scp -q "$($Server):$($i.Remote)/logs/$($i.Prefix)_$d.log" $i.Logs 2>$null
            if ($LASTEXITCODE -eq 0) {
                $report[$name] += $d
                Write-Host "  $d  ok" -ForegroundColor Green
            } else {
                Write-Host "  $d  --" -ForegroundColor DarkGray
            }
        }
    }

    # E16's evening job writes Stage A and the forward verdict to a log and
    # nowhere else. Without these the noise block in BRIEF.md has no scores.
    if ($i.Prefix -eq "noise") {
        scp -q "$($Server):$($i.Remote)/logs/noise_eod_*.log" $i.Logs 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  no noise_eod_*.log yet" -ForegroundColor DarkGray
        }
    }

    # State files. The first one listed is the record that matters; the rest are a nicety.
    foreach ($f in $i.Files) {
        scp -q "$($Server):$($i.Remote)/state/$f" $i.State 2>$null
    }

    $key = Join-Path $i.State $i.Files[0]
    if (Test-Path $key) {
        $n = [Math]::Max(0, (Get-Content $key).Count - 1)
        $what = if ($i.Prefix -eq "noise") { "checkpoint rows" } else { "trades" }
        Write-Host "  $($i.Files[0]): $n $what" -ForegroundColor Gray
    } else {
        Write-Host "  $($i.Files[0]): not present" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "=== TODAY ($today) ===" -ForegroundColor Cyan
foreach ($i in $Instances) {
    $f = Join-Path $i.Logs "$($i.Prefix)_$today.log"
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
Write-Host "Noise logs -> $Local\noise\logs" -ForegroundColor DarkGray
