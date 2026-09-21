#!/usr/bin/env bash
# Runs at 09:45 ET on market weekdays. Answers the one question invariants.py
# structurally cannot: "is there a session running RIGHT NOW?"
#
# invariants.py sweeps only up to the last log on disk, so a tail of days that
# never started is invisible to it. This catches that class of failure while
# the day is still salvageable.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."
TODAY="$(date +%F)"
LOG="logs/orb_${TODAY}.log"

if [[ -f state/DISARMED ]]; then
  exit 0   # deliberately skipped; not a failure
fi

if [[ ! -f "$LOG" ]]; then
  deploy/notify.sh "ORB bot DID NOT START" \
    "No log for ${TODAY} at 09:45 ET. Check: systemctl status orb-bot" high
  exit 1
fi

# Log exists -- is it still being written to? (catches a frozen/dead process)
AGE=$(( $(date +%s) - $(stat -c %Y "$LOG") ))
if (( AGE > 600 )); then
  deploy/notify.sh "ORB bot looks STALLED" \
    "logs/orb_${TODAY}.log last written ${AGE}s ago. Check: systemctl status orb-bot" high
  exit 1
fi

exit 0
