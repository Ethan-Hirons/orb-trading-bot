# setup_tasks.ps1 - registers the ORB bot launcher + watchdog in Windows Task Scheduler.
# Run once (no admin needed):  powershell -ExecutionPolicy Bypass -File setup_tasks.ps1
# Times are LOCAL (CEST): 14:40 launcher, 15:15 watchdog (~9:15 ET).
# Note: for ~2-3 weeks a year US/EU daylight saving shifts differ by 1 hour - adjust then.

$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
$days = 'Monday','Tuesday','Wednesday','Thursday','Friday'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12)

# 1. Launcher: starts (and restarts) the bot every weekday.
$botAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$proj\start_bot.ps1`""
$botTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 14:40
Register-ScheduledTask -TaskName "ORB Bot" -Action $botAction -Trigger $botTrigger `
    -Settings $settings -Description "Starts the ORB trading bot; restarts it on crash." -Force

# 2. Watchdog: alerts at ~9:15 ET if the bot is not running or not armed.
$wdAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$proj\watchdog.ps1`""
$wdTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 15:15
Register-ScheduledTask -TaskName "ORB Bot Watchdog" -Action $wdAction -Trigger $wdTrigger `
    -Settings $settings -Description "Alerts if the ORB bot is not running/armed after the open." -Force

Write-Host ""
Write-Host "Done. Registered 'ORB Bot' (weekdays 14:40) and 'ORB Bot Watchdog' (weekdays 15:15)."
Write-Host "You still need to run 'python arm.py' each trading day - the launcher waits for it."
