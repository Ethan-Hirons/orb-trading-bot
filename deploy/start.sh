#!/usr/bin/env bash
# Launch wrapper for the ORB bot under systemd.
#
# INTERPRETER (2026-09-28): this script resolves the venv python itself. It
# used to call bare `python3`, and vps-setup.sh patched that at install time
# with a sed. That patch was a local modification to a tracked file, so the
# first `git pull` that touched start.sh conflicted, the local copy was
# discarded to resolve it, and both instances lost their interpreter:
# ModuleNotFoundError: No module named 'dotenv', five crash-restarts, session
# gone. A launcher must not depend on an edit someone has to remember to redo.
#
# ORB_MODE decides the arming policy, and the two are deliberately opposite:
#
#   paper (default)  -- arm automatically every market weekday.
#                       `touch state/DISARMED` to skip a session.
#
#   live             -- NEVER arms itself. A human runs `arm.py` (or
#                       `arm.py --next` the evening before), or there is no
#                       live session. A missed live day costs nothing; an
#                       unattended one can cost the account.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."
MODE="${ORB_MODE:-paper}"

PY="$PWD/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "No venv interpreter at $PY -- falling back to python3." >&2
  PY="$(command -v python3 || true)"
fi
[[ -n "$PY" ]] || { echo "No python interpreter at all. Aborting."; exit 1; }

# Fail fast and LOUDLY if the interpreter cannot load the bot. Without this a
# broken environment is indistinguishable from "nothing to do", which is
# exactly how 2026-09-28 was lost in silence.
if ! "$PY" -c "import orb_bot.state" >/dev/null 2>&1; then
  echo "FATAL: $PY cannot import orb_bot -- wrong interpreter or broken venv." >&2
  "$PY" -c "import orb_bot.state" || true
  deploy/notify.sh "ORB ${MODE} BROKEN ENV" \
    "start.sh cannot import orb_bot using $PY. No session today. Check: journalctl -u orb-bot${MODE/paper/} -n 50" \
    urgent || true
  exit 1
fi

if [[ -f state/DISARMED ]]; then
  echo "state/DISARMED present -- skipping today's session."
  exit 0
fi

if [[ "$MODE" == "live" ]]; then
  TODAY="$(TZ=America/New_York date +%F)"
  # Read the arm state ONCE, and keep "the check failed" separate from "not
  # armed" -- conflating them is what turned a crash into a silent skip.
  if ! STATUS="$("$PY" arm.py --status 2>&1)"; then
    echo "FATAL: could not read the arm state." >&2
    printf '%s\n' "$STATUS" >&2
    deploy/notify.sh "ORB LIVE arm check FAILED" \
      "arm.py --status did not run. This is NOT 'unarmed' -- something is broken. No live session today." \
      urgent || true
    exit 1
  fi
  if ! grep -q "trade_date : ${TODAY}" <<<"$STATUS"; then
    echo "LIVE mode: not armed for ${TODAY}."
    deploy/notify.sh "ORB LIVE not armed" \
      "No live session for ${TODAY} -- nobody armed it. Run 'arm.py' on the live host if you want today." \
      default || true
    exit 0
  fi
  if ! grep -qi "armed *: True" <<<"$STATUS"; then
    echo "LIVE mode: state says disarmed for ${TODAY}; skipping."
    exit 0
  fi
  echo "LIVE mode: armed for ${TODAY} by hand. Starting."
else
  "$PY" arm.py
fi

exec "$PY" -u run.py
