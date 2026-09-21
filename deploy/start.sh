#!/usr/bin/env bash
# Launch wrapper for the ORB bot under systemd.
#
# Arming policy (INVERTED from the Windows setup, deliberately):
#   default            -> arm and trade every market weekday
#   touch state/DISARMED -> skip the session; remove the file to resume
#
# Rationale: manual arming existed because the bot ran on a laptop nobody was
# watching. On an always-on host the failure mode flipped -- "forgot to arm"
# cost six consecutive sessions in Aug/Sep 2026. Opting OUT is now the manual
# step. REVISIT THIS BEFORE FUNDING REAL MONEY: with live capital the safer
# default is opt-in, i.e. delete the DISARMED check and arm by hand.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."

if [[ -f state/DISARMED ]]; then
  echo "state/DISARMED present -- skipping today's session."
  exit 0
fi

python3 arm.py
exec python3 -u run.py
