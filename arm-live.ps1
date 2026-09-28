<#
.SYNOPSIS
  Arm the LIVE bot for today. Run before 09:25 ET on any day you want it to trade.

.DESCRIPTION
  The live bot never arms itself -- that is the whole safety design. This is
  the one command that gives it permission for the current day.

  No arm = no live session. The timer fires, sees nothing, pushes
  "ORB LIVE not armed" to your phone, and exits. That is a normal day.

.EXAMPLE
  .\arm-live.ps1            # arm today
  .\arm-live.ps1 -Off       # cancel today
  .\arm-live.ps1 -Status    # what is it going to do?
#>
param([switch]$Off, [switch]$Status)

$Server = "root@157.180.31.101"
$Live   = "/opt/orb-bot-live"

if     ($Status) { $cmd = "arm.py --status" }
elseif ($Off)    { $cmd = "arm.py --off" }
else             { $cmd = "arm.py" }

ssh $Server "cd $Live && sudo -u orb .venv/bin/python $cmd"

if (-not $Status) {
    Write-Host ""
    Write-Host "Live session state above. Paper runs regardless." -ForegroundColor Cyan
}
