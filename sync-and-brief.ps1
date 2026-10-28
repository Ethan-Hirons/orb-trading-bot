<#
.SYNOPSIS
  Pull BOTH instances off the VPS, then regenerate BRIEF.md and dashboard.html.

.DESCRIPTION
  This is what the scheduled task runs. Order matters: sync first, then build,
  so the brief can never describe data older than the sync that fed it.

  Runs AFTER the close on purpose. A sync taken mid-session copies a log that
  is still being written, and the result is indistinguishable from a session
  that was killed - that is exactly what happened on 2026-10-23 and cost an
  hour of diagnosis. The VPS EOD summaries fire at 16:05 (paper) and 16:07
  (live), so 16:21 ET leaves room.

  Everything is appended to logs\sync.log. Silence is the failure mode this
  project keeps losing days to, so every run writes a line whether or not it
  worked.

.EXAMPLE
  .\sync-and-brief.ps1
#>
param([int]$Days = 5)

$ErrorActionPreference = "Continue"
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $proj

New-Item -ItemType Directory -Force -Path "$proj\logs" | Out-Null
$logFile = "$proj\logs\sync.log"

# ENCODING (fixed 2026-10-26): PowerShell 5.1's Tee-Object -FilePath writes
# UTF-16LE while Add-Content defaults to ASCII, so mixing them produced a
# sync.log that `file` reports as binary "data" and that grep/tail cannot
# read. Everything below goes through these two helpers with one explicit
# encoding instead.
function Note($msg, $color = "Gray") {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    Write-Host $line -ForegroundColor $color
}

function Run($label, $exe, $argList) {
    # Capture, log, echo. Not streaming, but these steps take seconds and a
    # readable log matters more than live output in a hidden window.
    $out = & $exe @argList 2>&1 | Out-String
    $code = $LASTEXITCODE
    if ($out.Trim()) {
        Add-Content -Path $logFile -Value $out.TrimEnd() -Encoding UTF8
        Write-Host $out.TrimEnd()
    }
    return $code
}

Note "=== sync-and-brief start ===" "Cyan"

# 1. Pull both instances.
$code = Run "sync" "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", "$proj\sync-both.ps1", "-Days", "$Days")
if ($code -ne 0) {
    Note "sync-both.ps1 exited $code - brief may be stale" "Yellow"
}

# 2. Pick an interpreter. 'py' is absent on some installs; fall back.
$py = $null
foreach ($c in @("py", "python")) {
    if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
}
if (-not $py) { Note "NO PYTHON FOUND - cannot build brief" "Red"; exit 1 }

# 3. Rebuild the digest and the dashboard.
Run "brief" $py @("$proj\brief.py") | Out-Null
Run "dashboard" $py @("$proj\dashboard.py", "--limit", "30") | Out-Null

# 4. Ops checks on both instances. Non-zero means a FAIL, which is worth
#    seeing in the log even though it must not fail the sync itself.
if ((Run "ops-paper" $py @("$proj\ops_report.py")) -ne 0) {
    Note "ops_report: paper has FAILing checks" "Yellow"
}
if ((Run "ops-live" $py @("$proj\ops_report.py", "--root", "$proj\live")) -ne 0) {
    Note "ops_report: live has FAILing checks" "Yellow"
}

# 5. State the outcome in one greppable line. A log you have to reconstruct
#    from is a log nobody reads.
$paperLatest = (Get-ChildItem "$proj\logs\orb_*.log" -ErrorAction SilentlyContinue |
    ForEach-Object { $_.BaseName -replace 'orb_', '' } | Sort-Object | Select-Object -Last 1)
$liveLatest = (Get-ChildItem "$proj\live\logs\orb_*.log" -ErrorAction SilentlyContinue |
    ForEach-Object { $_.BaseName -replace 'orb_', '' } | Sort-Object | Select-Object -Last 1)
$today = Get-Date -Format "yyyy-MM-dd"
Note "latest session on disk: paper=$paperLatest live=$liveLatest (today=$today)" "Cyan"
if ($paperLatest -ne $today) {
    Note "NO PAPER SESSION FOR TODAY - bot did not run, or VPS has no log yet" "Yellow"
}

Note "=== sync-and-brief done ===" "Green"
exit 0
