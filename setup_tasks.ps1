# setup_tasks.ps1 - registers the ORB bot launcher + watchdog in Windows Task Scheduler.
# Run once (no admin needed):  powershell -ExecutionPolicy Bypass -File setup_tasks.ps1
#
# Times are LOCAL to this machine.
#
# v1.15 (2026-09-09): these were 14:40 / 15:15 because the machine was on CEST,
# where that IS 08:40 / 09:15 ET. The machine is now on US Eastern, so those
# same numbers meant 14:40 ET - over five hours after the open, with the entire
# opening-range window gone. Anchored to ET below.
#
# If you move timezones again, THIS is the file to change: the numbers must be
# whatever local clock time corresponds to ~08:40 and ~09:15 in New York.

$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
$days = 'Monday','Tuesday','Wednesday','Thursday','Friday'

# Guard: warn loudly if the machine is not on Eastern time, since the literal
# times below would then fire at the wrong point in the session.
$tz = (Get-TimeZone).Id
if ($tz -notmatch 'Eastern') {
    Write-Host ""
    Write-Host "WARNING: this machine's timezone is '$tz', not US Eastern." -ForegroundColor Yellow
    Write-Host "The 08:40/09:15 triggers below are written as EASTERN clock times." -ForegroundColor Yellow
    Write-Host "Edit setup_tasks.ps1 to your local equivalent before relying on them." -ForegroundColor Yellow
    Write-Host ""
}
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12)

# 1. Launcher: starts (and restarts) the bot every weekday.
$botAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$proj\start_bot.ps1`""
$botTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 08:40
Register-ScheduledTask -TaskName "ORB Bot" -Action $botAction -Trigger $botTrigger `
    -Settings $settings -Description "Starts the ORB trading bot; restarts it on crash." -Force

# 2. Watchdog: alerts at ~9:15 ET if the bot is not running or not armed.
$wdAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$proj\watchdog.ps1`""
$wdTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 09:15
Register-ScheduledTask -TaskName "ORB Bot Watchdog" -Action $wdAction -Trigger $wdTrigger `
    -Settings $settings -Description "Alerts if the ORB bot is not running/armed after the open." -Force

Write-Host ""
Write-Host "Done. Registered 'ORB Bot' (weekdays 08:40) and 'ORB Bot Watchdog' (weekdays 09:15), local time."
Write-Host "You still need to run 'python arm.py' each trading day - the launcher waits for it."
