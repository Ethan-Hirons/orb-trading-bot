#!/usr/bin/env bash
# E16 evening job. Refreshes the SIP cache, scores Stage A from the paper ledger,
# prints the forward-test status (the evidence), and pushes a two-line summary.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."
PY="$PWD/.venv/bin/python"
DAY="$(TZ=America/New_York date +%F)"

"$PY" -u index_scan.py fetch > "logs/noise_fetch_$DAY.log" 2>&1
REC="$("$PY" -u noise_bot.py reconcile 2>&1)"
FWD="$("$PY" -u index_scan.py forward 2>&1)"
printf '%s\n\n%s\n' "$REC" "$FWD" | tee "logs/noise_eod_$DAY.log"

STATUS="$(grep -m1 -E 'RUNNING|STOPPED|FINAL' <<<"$FWD" | sed 's/^ *//')"
STAGE_A="$(grep -m1 -E 'decision agreement|ledger is empty' <<<"$REC" | sed 's/^ *//')"
deploy/notify.sh "E16 QQQ evening" "$STATUS
$STAGE_A" || true
