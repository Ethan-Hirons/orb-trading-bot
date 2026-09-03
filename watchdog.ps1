# watchdog.ps1 - alerts if the ORB bot is not running / not armed.
# Registered in Task Scheduler by setup_tasks.ps1 (weekdays 15:15 local ~ 9:15 ET).

$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
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

if ($problems.Count -gt 0) {
    $msg = "ORB bot check at $(Get-Date -Format 'HH:mm'):`n`n- " + ($problems -join "`n- ")
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [WATCHDOG] ALERT: $($problems -join ' | ')" |
        Add-Content (Join-Path $proj "logs\wrapper.log")
    # Popup stays up to 1 hour or until dismissed. Icon 48 = warning.
    (New-Object -ComObject WScript.Shell).Popup($msg, 3600, "ORB Bot Alert", 48) | Out-Null
} else {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [WATCHDOG] OK: bot running and armed." |
        Add-Content (Join-Path $proj "logs\wrapper.log")
}
