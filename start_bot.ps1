# start_bot.ps1 - crash-proof launcher for the ORB bot.
# Waits until the bot is armed for today, starts run.py, and restarts it if it crashes.
# Registered in Task Scheduler by setup_tasks.ps1 (weekdays 14:40 local).

$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $proj
$wrapperLog = Join-Path $proj "logs\wrapper.log"
New-Item -ItemType Directory -Force -Path (Join-Path $proj "logs") | Out-Null

function Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [WRAPPER] $msg" | Add-Content $wrapperLog
}

# Crash/restart events also go into the day's orb log so the daily recap
# (which only reads logs\orb_YYYY-MM-DD.log) can see them.
function LogAlert($msg) {
    Log $msg
    $orbLog = Join-Path $proj ("logs\orb_{0}.log" -f (Get-Date -Format 'yyyy-MM-dd'))
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss,000') [WRAPPER] $msg" |
        Add-Content $orbLog -Encoding UTF8
}

function IsArmedToday {
    $stateFile = Join-Path $proj "state\daily_state.json"
    if (-not (Test-Path $stateFile)) { return $false }
    try {
        $state = Get-Content $stateFile -Raw | ConvertFrom-Json
        return ($state.trade_date -eq (Get-Date -Format 'yyyy-MM-dd')) -and $state.armed
    } catch { return $false }
}

$maxRestarts = 5
$restarts = 0
$giveUpAt = Get-Date -Hour 22 -Minute 30 -Second 0   # ~30 min after US close (CEST)

Log "Launcher started."

# Wait for today's arming (bot exits immediately if not armed).
while (-not (IsArmedToday)) {
    if ((Get-Date) -gt $giveUpAt) { Log "Never armed today. Giving up."; exit 0 }
    Log "Not armed for today yet. Run 'python arm.py'. Re-checking in 5 min."
    Start-Sleep -Seconds 300
}
Log "Bot is armed. Starting run.py."

while ($true) {
    $p = Start-Process -FilePath "python" -ArgumentList "run.py" -WorkingDirectory $proj -NoNewWindow -PassThru -Wait
    if ($p.ExitCode -eq 0) {
        Log "Bot exited normally (code 0). Done for today."
        break
    }
    $restarts++
    LogAlert "Bot exited with code $($p.ExitCode) (crash?). Restart $restarts of $maxRestarts."
    if ($restarts -ge $maxRestarts) {
        LogAlert "Too many crashes. Giving up."
        (New-Object -ComObject WScript.Shell).Popup("ORB bot crashed $maxRestarts times and was NOT restarted. Check logs\wrapper.log.", 0, "ORB Bot Alert", 16) | Out-Null
        break
    }
    if ((Get-Date) -gt $giveUpAt) { LogAlert "Past close. Not restarting."; break }
    Start-Sleep -Seconds 30
}
Log "Launcher finished."
