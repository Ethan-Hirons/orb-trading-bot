#!/usr/bin/env bash
# One-command setup for the E16 QQQ paper runner. Run as root on the VPS:
#
#     bash /opt/orb-bot/deploy/setup-noise.sh
#
# Safe to run more than once: every step checks before it acts. It stops and tells
# you where to paste the E16 paper keys; it never types keys itself.
#
# It proves the keys open a THIRD account (not ORB paper, not ORB live) before
# recording that account as NOISE_ACCOUNT, which noise_bot.py checks every morning.
set -euo pipefail

PAPER=/opt/orb-bot
LIVE=/opt/orb-bot-live
NOISE=/opt/orb-noise
ORIGIN=https://github.com/Ethan-Hirons/orb-trading-bot.git
UNITS=/etc/systemd/system

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[0;32mOK\033[0m  %s\n' "$*"; }
warn() { printf '    \033[0;33m!!\033[0m  %s\n' "$*"; }
die()  { printf '\n\033[0;31mSTOPPED: %s\033[0m\n\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run this as root (use: sudo bash $0)"
[[ -d $PAPER ]]   || die "$PAPER not found -- is this the right machine?"

# ------------------------------------------------------------------ 1. repo
say "1/6  Creating the E16 instance at $NOISE"
if [[ -d $NOISE/.git ]]; then
  sudo -u orb git -C "$NOISE" pull --ff-only
  ok "already existed, pulled"
else
  mkdir -p "$NOISE"
  chown orb:orb "$NOISE"
  sudo -u orb git clone --quiet "$PAPER" "$NOISE"
  sudo -u orb git -C "$NOISE" remote set-url origin "$ORIGIN"
  sudo -u orb git -C "$NOISE" pull --ff-only --quiet
  ok "cloned at $(sudo -u orb git -C "$NOISE" rev-parse --short HEAD)"
fi
[[ -f $NOISE/noise_bot.py ]] || die "noise_bot.py missing -- push the E16 commit, then re-run"
sudo -u orb mkdir -p "$NOISE/logs" "$NOISE/state" "$NOISE/.index_cache"

if [[ ! -d $NOISE/.venv ]]; then
  say "     building its virtualenv (a minute or so)"
  sudo -u orb python3 -m venv "$NOISE/.venv"
  sudo -u orb "$NOISE/.venv/bin/pip" install --quiet --upgrade pip
  sudo -u orb "$NOISE/.venv/bin/pip" install --quiet -r "$NOISE/requirements.txt"
fi
ok "virtualenv ready"
[[ -f $NOISE/deploy/notify.env ]] || sudo -u orb cp "$PAPER/deploy/notify.env" "$NOISE/deploy/notify.env"
ok "phone alerts configured (same ntfy topic, pushes are tagged 'E16 QQQ')"

# ------------------------------------------------------------------ 2. keys
say "2/6  E16 paper keys"
if [[ ! -f $NOISE/.env ]]; then
  sudo -u orb tee "$NOISE/.env" >/dev/null <<'ENVEOF'
# E16 QQQ trial: the keys of the NEW paper account ("E16 QQQ" / "Paper 2"),
# NOT the ORB paper account and NEVER live keys. ALPACA_PAPER must stay true.
ALPACA_API_KEY=PUT_THE_E16_PAPER_KEY_HERE
ALPACA_SECRET_KEY=PUT_THE_E16_PAPER_SECRET_HERE
ALPACA_PAPER=true
ENVEOF
  chmod 600 "$NOISE/.env"; chown orb:orb "$NOISE/.env"
fi
if grep -q "PUT_THE_E16" "$NOISE/.env"; then
  cat <<MSG

    The E16 keys are not in yet. Do this now:

        sudo -u orb nano $NOISE/.env

    Paste the NEW paper account's key and secret over the two placeholders,
    leave ALPACA_PAPER=true, save with Ctrl+O, Enter, then Ctrl+X.

    Then run this script again:

        sudo bash $PAPER/deploy/setup-noise.sh

MSG
  exit 0
fi
grep -q "^ALPACA_PAPER=true" "$NOISE/.env" || die "$NOISE/.env must contain ALPACA_PAPER=true"
ok "keys present"

# --------------------------------------------- 3. prove it is a third account
say "3/6  Checking the keys open a NEW paper account"
cd "$NOISE"
ACCT="$(sudo -u orb "$NOISE/.venv/bin/python" - <<'PY'
import sys
from dotenv import dotenv_values
from alpaca.trading.client import TradingClient

def account(path, paper):
    v = dotenv_values(path)
    if not v.get("ALPACA_API_KEY"):
        return None
    try:
        return str(TradingClient(v["ALPACA_API_KEY"], v["ALPACA_SECRET_KEY"], paper=paper)
                   .get_account().account_number)
    except Exception:
        return None

v = dotenv_values(".env")
try:
    cli = TradingClient(v["ALPACA_API_KEY"], v["ALPACA_SECRET_KEY"], paper=True)
    a = cli.get_account()
except Exception as e:
    sys.exit(f"the E16 keys did not authenticate on the PAPER endpoint: {e}")
mine = str(a.account_number)
if mine in (account("/opt/orb-bot/.env", True), account("/opt/orb-bot-live/.env", False)):
    sys.exit(f"these keys open {mine}, which is an ORB account. Use the NEW paper account's keys.")
held = [p.symbol for p in cli.get_all_positions()]
print(f"    account {mine}  equity ${float(a.equity):,.2f}  positions {held or 'none'}", file=sys.stderr)
if float(a.equity) < 12000:
    print("    !!  equity is under $12,000; the trial sizes each position at $10,000", file=sys.stderr)
if set(held) - {"QQQ"}:
    sys.exit(f"the account holds {held}: E16 needs an empty account")
print(mine)
PY
)" || die "account check failed (message above)"
if grep -q "^NOISE_ACCOUNT=" "$NOISE/.env"; then
  [[ "$(grep '^NOISE_ACCOUNT=' "$NOISE/.env" | cut -d= -f2)" == "$ACCT" ]] \
    || die "NOISE_ACCOUNT in $NOISE/.env differs from the account these keys open ($ACCT)"
else
  printf 'NOISE_ACCOUNT=%s\n' "$ACCT" | sudo -u orb tee -a "$NOISE/.env" >/dev/null
fi
ok "E16 account is $ACCT (a third account: not ORB paper, not ORB live)"

# ------------------------------------------------------------------ 4. tests
say "4/6  Running the E16 test suites"
sudo -u orb "$NOISE/.venv/bin/python" tests/test_index_scan.py | tail -1
sudo -u orb "$NOISE/.venv/bin/python" tests/test_noise_bot.py 2>/dev/null | tail -1

# ------------------------------------------------------------------ 5. units
say "5/6  Installing the systemd units"
cp "$NOISE/deploy/orb-noise.service" "$NOISE/deploy/orb-noise.timer" \
   "$NOISE/deploy/orb-noise-eod.service" "$NOISE/deploy/orb-noise-eod.timer" "$UNITS/"
systemctl daemon-reload
systemctl enable --now orb-noise.timer orb-noise-eod.timer >/dev/null 2>&1
ok "timers enabled: session 09:15, evening job 16:40 (Mon-Fri, ET)"

# ------------------------------------------------- 6. first data download
say "6/6  Starting the first SIP download in the background (~6 minutes)"
systemctl start --no-block orb-noise-eod.service
ok "follow it with: journalctl -u orb-noise-eod -f"

cat <<MSG

------------------------------------------------------------------
E16 is installed. Nothing else to do: it runs itself.

  Every market weekday  09:15  runner starts, trades QQQ 10:00-15:30,
                               market-on-close exit, verified flat 16:05
                        16:40  evening job: Stage A score + forward status
                               pushed to your phone ("E16 QQQ evening")

  Check any time:   cd $NOISE && sudo -u orb .venv/bin/python noise_bot.py status
  Skip sessions:    sudo -u orb touch $NOISE/state/NOISE_DISARMED
  Stop right now:   sudo systemctl stop orb-noise   (then close QQQ in the
                    E16 paper account by hand if it holds any)

The ORB paper and live bots are not touched by any of this.
------------------------------------------------------------------

MSG
