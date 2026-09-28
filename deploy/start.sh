#!/usr/bin/env bash
# Launch wrapper for the ORB bot under systemd.
#
# ORB_MODE decides the arming policy, and the two are deliberately opposite:
#
#   paper (default)  -- arm automatically every market weekday.
#                       `touch state/DISARMED` to skip a session.
#                       Manual arming existed because the bot ran on a laptop
#                       nobody watched; on an always-on host "forgot to arm"
#                       cost six consecutive sessions in Aug/Sep 2026. For an
#                       unsupervised paper trial, opting OUT is the right
#                       manual step -- the sample is the whole point.
#
#   live             -- NEVER arms itself. A human runs `python arm.py` for
#                       that day, or there is no live session. With real money
#                       the failure modes swap places: a missed session costs
#                       nothing, an unattended one can cost the account.
#
# Set by the systemd unit: Environment=ORB_MODE=live
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."
MODE="${ORB_MODE:-paper}"

if [[ -f state/DISARMED ]]; then
  echo "state/DISARMED present -- skipping today's session."
  exit 0
fi

if [[ "$MODE" == "live" ]]; then
  TODAY="$(TZ=America/New_York date +%F)"
  if ! python3 arm.py --status 2>/dev/null | grep -q "trade_date : ${TODAY}"; then
    echo "LIVE mode: not armed for ${TODAY}. Run 'python arm.py' on the host."
    deploy/notify.sh "ORB LIVE not armed" \
      "No live session for ${TODAY} -- nobody armed it. Run 'python arm.py' on the live host if you want today." \
      default || true
    exit 0
  fi
  if ! python3 arm.py --status 2>/dev/null | grep -qi "armed      : True"; then
    echo "LIVE mode: state says disarmed for ${TODAY}; skipping."
    exit 0
  fi
  echo "LIVE mode: armed for ${TODAY} by hand. Starting."
else
  python3 arm.py
fi

exec python3 -u run.py
