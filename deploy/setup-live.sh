#!/usr/bin/env bash
# One-command live setup. Run as root on the VPS:
#
#     bash /opt/orb-bot/deploy/setup-live.sh
#
# Safe to run more than once -- every step checks before it acts, so if it
# stops and asks you for something you just run it again afterwards.
#
# It will NOT do two things for you, on purpose:
#   1. type your live Alpaca keys       -> it stops and tells you where
#   2. create state/LIVE_CONFIRMED      -> that file is the actual go switch
set -euo pipefail

PAPER=/opt/orb-bot
LIVE=/opt/orb-bot-live
ORIGIN=https://github.com/Ethan-Hirons/orb-trading-bot.git
UNITS=/etc/systemd/system

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[0;32mOK\033[0m  %s\n' "$*"; }
warn() { printf '    \033[0;33m!!\033[0m  %s\n' "$*"; }
die()  { printf '\n\033[0;31mSTOPPED: %s\033[0m\n\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run this as root (use: sudo bash $0)"
[[ -d $PAPER ]]   || die "$PAPER not found -- is this the right machine?"

# ---------------------------------------------------------------- 1. paper
say "1/7  Updating the paper instance"
sudo -u orb git -C "$PAPER" pull --ff-only
ok "paper repo at $(sudo -u orb git -C "$PAPER" rev-parse --short HEAD)"

say "2/7  Running the test suites"
cd "$PAPER"
sudo -u orb ORB_NO_FILE_LOG=1 "$PAPER/.venv/bin/python" tests/test_logic.py     | tail -1
sudo -u orb ORB_NO_FILE_LOG=1 "$PAPER/.venv/bin/python" tests/test_preflight.py | tail -1

# ------------------------------------------------------------- 3. live repo
say "3/7  Creating the live instance at $LIVE"
if [[ -d $LIVE/.git ]]; then
  sudo -u orb git -C "$LIVE" pull --ff-only
  ok "already existed, pulled"
else
  # /opt is root-owned, so make the directory first and hand it to orb --
  # a plain `sudo -u orb git clone` into /opt fails on permissions.
  mkdir -p "$LIVE"
  chown orb:orb "$LIVE"
  sudo -u orb git clone --quiet "$PAPER" "$LIVE"
  sudo -u orb git -C "$LIVE" remote set-url origin "$ORIGIN"
  ok "cloned"
fi
sudo -u orb mkdir -p "$LIVE/logs" "$LIVE/state"

if [[ ! -d $LIVE/.venv ]]; then
  say "     building its virtualenv (a minute or so)"
  sudo -u orb python3 -m venv "$LIVE/.venv"
  sudo -u orb "$LIVE/.venv/bin/pip" install --quiet --upgrade pip
  sudo -u orb "$LIVE/.venv/bin/pip" install --quiet -r "$LIVE/requirements.txt"
fi
ok "virtualenv ready"

[[ -f $LIVE/deploy/notify.env ]] || sudo -u orb cp "$PAPER/deploy/notify.env" "$LIVE/deploy/notify.env"
ok "phone alerts configured (same ntfy topic, live pushes are tagged 'ORB LIVE')"

# --------------------------------------------------------------- 4. the keys
say "4/7  Live API keys"
if [[ ! -f $LIVE/.env ]]; then
  sudo -u orb tee "$LIVE/.env" >/dev/null <<'ENVEOF'
# LIVE Alpaca keys. Get them at app.alpaca.markets -> API keys.
# These are NOT your paper keys, and ALPACA_PAPER must be false.
ALPACA_API_KEY=PUT_YOUR_LIVE_KEY_HERE
ALPACA_SECRET_KEY=PUT_YOUR_LIVE_SECRET_HERE
ALPACA_PAPER=false
ENVEOF
  chmod 600 "$LIVE/.env"; chown orb:orb "$LIVE/.env"
fi

if grep -q "PUT_YOUR_LIVE" "$LIVE/.env"; then
  cat <<MSG

    The live keys are not in yet. Do this now:

        sudo -u orb nano $LIVE/.env

    Paste your LIVE key and secret (app.alpaca.markets -> API keys),
    leave ALPACA_PAPER=false, save with Ctrl+O then Ctrl+X.

    Then run this script again:

        sudo bash $PAPER/deploy/setup-live.sh

MSG
  exit 0
fi
grep -q "^ALPACA_PAPER=false" "$LIVE/.env" || die "$LIVE/.env must contain ALPACA_PAPER=false"
ok "keys present"

say "5/7  Checking the live account answers"
cd "$LIVE"
sudo -u orb ORB_CONFIG=config-live.yaml "$LIVE/.venv/bin/python" - <<'PY' || die "the live keys did not authenticate -- check them and re-run"
from orb_bot.config import load_config
from orb_bot.broker import Broker
a = Broker(load_config()).trading.get_account()
print(f"    account {a.account_number}  status {a.status}")
print(f"    equity  ${float(a.equity):,.2f}   cash ${float(a.cash):,.2f}")
eq = float(a.equity)
if eq < 2000:
    print(f"    !!  equity is under the $2,000 margin minimum for intraday debits.")
if eq > 1800 * 3:
    print("    !!  equity is large enough that config-live.yaml sizing is conservative.")
PY

# ------------------------------------------------------------- 6. the units
say "6/7  Installing the systemd units"
cp "$PAPER/deploy/orb-bot.service" "$UNITS/"
cp "$LIVE/deploy/orb-bot-live.service" "$LIVE/deploy/orb-bot-live.timer" \
   "$LIVE/deploy/orb-summary-live.service" "$LIVE/deploy/orb-summary-live.timer" "$UNITS/"
systemctl daemon-reload
if systemd-analyze verify "$UNITS/orb-bot-live.service" 2>&1 | grep -q .; then
  warn "systemd-analyze had something to say about orb-bot-live.service (above)"
else
  ok "unit files valid (systemd-analyze silent)"
fi
systemctl enable --now orb-bot-live.timer orb-summary-live.timer >/dev/null 2>&1
ok "live timers enabled"

# --------------------------------------------------- 7. prove preflight works
say "7/7  Proving the safety check actually refuses"
if [[ -f $LIVE/state/LIVE_CONFIRMED ]]; then
  warn "state/LIVE_CONFIRMED already exists, so the refusal test is skipped."
  warn "Delete it and re-run if you want to see the check work."
else
  set +e
  OUT=$(cd "$LIVE" && sudo -u orb ORB_MODE=live ORB_CONFIG=config-live.yaml \
        ORB_NO_FILE_LOG=1 "$LIVE/.venv/bin/python" run.py 2>&1)
  RC=$?
  set -e
  if [[ $RC -eq 2 ]] && grep -q "LIVE_CONFIRMED is missing" <<<"$OUT"; then
    ok "preflight refused to trade, exactly as it should"
  else
    printf '%s\n' "$OUT" | tail -20
    die "preflight did NOT refuse (exit $RC). Do not go live. Send me this output."
  fi
fi

cat <<MSG

------------------------------------------------------------------
Everything is installed. Two steps left, both by hand, both by you.

  1. Arm the go switch (once, ever):

       sudo -u orb touch $LIVE/state/LIVE_CONFIRMED

  2. Arm tomorrow's session (every trading day you want one,
     before 09:25 ET -- the live bot never arms itself):

       cd $LIVE && sudo -u orb .venv/bin/python arm.py

If you skip step 2 the timer fires, sees no arm, sends "ORB LIVE not
armed" to your phone and exits. That is a normal day, not a fault.

To stop everything right now and flatten every position:

       sudo systemctl stop orb-bot-live

------------------------------------------------------------------

MSG
