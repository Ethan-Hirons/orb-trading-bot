<#
.SYNOPSIS
  Register the weekday task that syncs both instances and rebuilds BRIEF.md.

.DESCRIPTION
  Run ONCE, from the repo folder, in an elevated PowerShell:

      powershell -ExecutionPolicy Bypass -File .\setup-sync-task.ps1

  Settings mirror setup_tasks.ps1 deliberately. The battery flags are not
  optional: New-ScheduledTaskSettingsSet defaults BOTH of them to blocking on
  a laptop, which is what silently killed sessions through August and was only
  found on 2026-09-12.

      -AllowStartIfOnBatteries      otherwise an unplugged laptop skips the run
      -DontStopIfGoingOnBatteries   otherwise unplugging mid-run kills it
      -StartWhenAvailable           catches up if the machine was asleep at 16:21
      -WakeToRun                    wakes it rather than missing the window

  The 16:21 trigger is an EASTERN clock time, written as a literal. If this
  machine is not on Eastern it fires at the wrong moment - the same class of
  bug as the CEST scheduler incident. The guard below warns loudly.

.EXAMPLE
  .\setup-sync-task.ps1
  .\setup-sync-task.ps1 -At 17:05
#>
param([string]$At = "16:21")

$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
$days = 'Monday','Tuesday','Wednesday','Thursday','Friday'

$tz = (Get-TimeZone).Id
if ($tz -notmatch 'Eastern') {
    Write-Host ""
    Write-Host "WARNING: this machine's timezone is '$tz', not US Eastern." -ForegroundColor Yellow
    Write-Host "The $At trigger is written as an EASTERN clock time." -ForegroundColor Yellow
    Write-Host "Pass -At with your local equivalent of 16:21 ET before relying on it." -ForegroundColor Yellow
    Write-Host ""
}

if (-not (Test-Path "$proj\sync-and-brief.ps1")) {
    Write-Host "sync-and-brief.ps1 not found next to this script." -ForegroundColor Red
    exit 1
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$proj\sync-and-brief.ps1`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $At

Register-ScheduledTask -TaskName "ORB Log Sync" -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description "Pulls paper+live logs from the VPS after the close, then rebuilds BRIEF.md and dashboard.html."

Write-Host ""
Write-Host "Registered 'ORB Log Sync' for weekdays at $At ($tz)." -ForegroundColor Green
Write-Host ""
Write-Host "Verify the flags that have bitten this project before:" -ForegroundColor Cyan
$s = (Get-ScheduledTask -TaskName "ORB Log Sync").Settings
Write-Host ("  StartsOnBattery  : {0}" -f (-not $s.DisallowStartIfOnBatteries))
Write-Host ("  SurvivesUnplug   : {0}" -f (-not $s.StopIfGoingOnBatteries))
Write-Host ("  StartWhenAvailable: {0}" -f $s.StartWhenAvailable)
Write-Host ("  WakeToRun        : {0}" -f $s.WakeToRun)
Write-Host ""
Write-Host "Test it now without waiting for 16:21:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName 'ORB Log Sync'"
Write-Host "  Get-Content .\logs\sync.log -Tail 20"
