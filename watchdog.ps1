# watchdog.ps1 - alerts if the ORB bot is not running / not armed.
# Registered in Task Scheduler by setup_tasks.ps1 (weekdays 09:15 local ET).
#
# v1.15 (2026-09-09): the Aug 28 - Sep 4 outage (six lost sessions) was invisible
# because wrapper.log contained NO watchdog lines at all - not even "OK" ones.
# That silence was the only evidence, and silence is not evidence anybody reads.
# Three changes:
#   1. Log a line on EVERY run, before any check can fail. A day with no
#      [WATCHDOG] line now unambiguously means the task did not fire.
#   2. Write state\watchdog_heartbeat.json so the evening recap can assert the
#      watchdog itself is alive, not just the bot.
#   3. Stop relying solely on a modal popup: under Task Scheduler's
#      "run whether user is logged on or not" there is no interactive desktop
#      and WScript.Shell.Popup goes nowhere. Detect that and leave a durable
#      ALERT file instead, so the alert survives having no one at the screen.

$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
$logFile = Join-Path $proj "logs\wrapper.log"
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

function Write-WatchdogLog($text) {
    "$stamp [WATCHDOG] $text" | Add-Content $logFile
}

# 0. Prove the task ran at all, before anything below can throw.
Write-WatchdogLog "run started."

$problems = @()

# 1. Is a python process running run.py?
$botProc = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
    Where-Object { $_.CommandLine -match 'run\.py' }
if (-not $botProc) {
    $problems += "The bot process is NOT running."
}

# 2. Is it armed for today?
$stateFile = Join-Path $proj "state\daily_state.json"
$armed = $false
if (Test-Path $stateFile) {
    try {
        $state = Get-Content $stateFile -Raw | ConvertFrom-Json
        $armed = ($state.trade_date -eq (Get-Date -Format 'yyyy-MM-dd')) -and $state.armed
    } catch {}
}
if (-not $armed) {
    $problems += "The bot is NOT armed for today (run 'python arm.py')."
}

# 3. Heartbeat: written every run, pass or fail, so absence is meaningful.
$heartbeat = @{
    checked_at = $stamp
    date       = (Get-Date -Format 'yyyy-MM-dd')
    armed      = $armed
    running    = [bool]$botProc
    problems   = $problems
}
try {
    $heartbeat | ConvertTo-Json | Set-Content (Join-Path $proj "state\watchdog_heartbeat.json")
} catch {
    Write-WatchdogLog "WARNING: could not write heartbeat: $($_.Exception.Message)"
}

$alertFile = Join-Path $proj "logs\WATCHDOG-ALERT.txt"

if ($problems.Count -gt 0) {
    $msg = "ORB bot check at $(Get-Date -Format 'HH:mm'):`n`n- " + ($problems -join "`n- ")
    Write-WatchdogLog "ALERT: $($problems -join ' | ')"

    # Durable alert - survives no-one-at-the-screen, and the evening recap
    # reads it. Deleted automatically on the next clean run.
    "$stamp`n$msg`n" | Set-Content $alertFile

    # Interactive desktop? If not, a modal popup is a no-op and pretending
    # otherwise is how an alert gets lost.
    $interactive = [Environment]::UserInteractive
    if ($interactive) {
        try {
            # Icon 48 = warning. Stays up to 1 hour or until dismissed.
            (New-Object -ComObject WScript.Shell).Popup($msg, 3600, "ORB Bot Alert", 48) | Out-Null
        } catch {
            Write-WatchdogLog "WARNING: popup failed: $($_.Exception.Message)"
        }
    } else {
        Write-WatchdogLog "no interactive desktop - popup skipped, alert written to logs\WATCHDOG-ALERT.txt"
    }
} else {
    Write-WatchdogLog "OK: bot running and armed."
    if (Test-Path $alertFile) { Remove-Item $alertFile -Force }
}
