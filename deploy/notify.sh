#!/usr/bin/env bash
# Push a message to your phone via ntfy.sh (free, no account needed).
#
# Setup: install the ntfy app, subscribe to a topic that is long and random
# (anyone who guesses the topic name can read it), then put it in
# deploy/notify.env as:  NTFY_TOPIC=orb-bot-<something-random>
set -euo pipefail

HERE="$(dirname "$(readlink -f "$0")")"
[[ -f "$HERE/notify.env" ]] && . "$HERE/notify.env"

TITLE="${1:-ORB bot}"
BODY="${2:-(no message)}"
PRIORITY="${3:-default}"

if [[ -z "${NTFY_TOPIC:-}" ]]; then
  echo "NTFY_TOPIC not set (deploy/notify.env) -- would have sent: $TITLE / $BODY" >&2
  exit 0
fi

curl -fsS \
  -H "Title: $TITLE" \
  -H "Priority: $PRIORITY" \
  -d "$BODY" \
  "https://ntfy.sh/${NTFY_TOPIC}" >/dev/null
