#!/usr/bin/env bash
# Runs at 16:05 ET on market weekdays, after the bot's EOD summary is written.
# Pushes the day's result to your phone.
#
# Why this exists: the logs live on the VPS now, and the old 16:15 recap read
# the laptop's logs/ folder -- so from 2026-09-17 a clean session and a session
# that never ran looked identical from the laptop. This makes every trading day
# report itself, with no laptop involved.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."
TODAY="${ORB_DATE:-$(date +%F)}"   # ORB_DATE lets you dry-run a past day
LOG="logs/orb_${TODAY}.log"

if [[ -f state/DISARMED ]]; then
  exit 0   # deliberately skipped; not a failure
fi

if [[ ! -f "$LOG" ]]; then
  deploy/notify.sh "ORB ${TODAY}: NO SESSION" \
    "No log at all for ${TODAY}. The session never ran. Check: systemctl status orb-bot" high
  exit 1
fi

DAY_DONE="$(grep -F 'Day done.' "$LOG" | tail -1 || true)"
SUMMARY="$(sed -n '/END-OF-DAY SUMMARY/,$p' "$LOG" \
             | sed 's/^[0-9-]* [0-9:,]* \[INFO\] *//' \
             | grep -vE '^=+$' | head -20 || true)"
ERRORS="$(grep -cE '\[ERROR\]|\[CRITICAL\]' "$LOG" || true)"

if [[ -z "$DAY_DONE" ]]; then
  # Ran but never reached the end: the 08-26/08-27 failure shape.
  deploy/notify.sh "ORB ${TODAY}: SESSION DID NOT FINISH" \
    "No 'Day done' line -- the bot was killed or crashed mid-session. CHECK FOR OPEN POSITIONS in Alpaca. Errors logged: ${ERRORS:-0}" \
    urgent
  exit 1
fi

if ! grep -qF 'Flatten verified' "$LOG"; then
  deploy/notify.sh "ORB ${TODAY}: FLATTEN NOT VERIFIED" \
    "Day done, but no 'Flatten verified' line. CHECK FOR OPEN POSITIONS in Alpaca." urgent
  exit 1
fi

RESULT="${DAY_DONE#*Day done. }"
PRIORITY=default
[[ "${ERRORS:-0}" != "0" ]] && PRIORITY=high

deploy/notify.sh "ORB ${TODAY}: ${RESULT}" \
  "${SUMMARY}

errors/criticals in log: ${ERRORS:-0}" "$PRIORITY"
