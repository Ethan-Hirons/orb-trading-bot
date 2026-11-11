#!/usr/bin/env bash
# One-command setup for the E20 BTC trend paper runner. Run as root on the VPS:
#
#     bash /opt/orb-bot/deploy/setup-btc.sh
#
# Safe to run more than once: every step checks before it acts. It stops and tells
# you where to paste the E20 paper keys; it never types keys itself.
#
# It proves the keys open a FOURTH account (not ORB paper, not ORB live, not E16)
# before recording that account as BTC_ACCOUNT, which btc_bot.py checks on every run.
#
# Written 2026-11-11 from setup-noise.sh. NOT yet run on the VPS: read the output of
# each step the first time.
set -euo pipefail

PAPER=/opt/orb-bot
BTC=/opt/orb-btc
ORIGIN=https://github.com/Ethan-Hirons/orb-trading-bot.git
UNITS=/etc/systemd/system

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[0;32mOK\033[0m  %s\n' "$*"; }
warn() { printf '    \033[0;33m!!\033[0m  %s\n' "$*"; }
die()  { printf '\n\033[0;31mSTOPPED: %s\033[0m\n\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run this as root (use: sudo bash $0)"
[[ -d $PAPER ]]   || die "$PAPER not found -- is this the right machine?"

# ------------------------------------------------------------------ 1. repo
say "1/5  Creating the E20 instance at $BTC"
if [[ -d $BTC/.git ]]; then
  sudo -u orb git -C "$BTC" pull --ff-only
  ok "already existed, pulled"
else
  mkdir -p "$BTC"
  chown orb:orb "$BTC"
  sudo -u orb git clone --quiet "$PAPER" "$BTC"
  sudo -u orb git -C "$BTC" remote set-url origin "$ORIGIN"
  sudo -u orb git -C "$BTC" pull --ff-only --quiet
  ok "cloned at $(sudo -u orb git -C "$BTC" rev-parse --short HEAD)"
fi
[[ -f $BTC/btc_bot.py ]] || die "btc_bot.py missing -- push the E20 commit, then re-run"
sudo -u orb mkdir -p "$BTC/logs" "$BTC/state"

if [[ ! -d $BTC/.venv ]]; then
  say "     building its virtualenv (numpy and pandas: a few minutes)"
  sudo -u orb python3 -m venv "$BTC/.venv"
  sudo -u orb "$BTC/.venv/bin/pip" install --quiet --upgrade pip
fi
sudo -u orb "$BTC/.venv/bin/pip" install --quiet -r "$BTC/requirements-btc.txt"
cd "$BTC"
sudo -u orb "$BTC/.venv/bin/python" -c "import numpy, pandas, requests, alpaca, dotenv, survey" \
  || die "the virtualenv cannot import the runner's packages (message above)"
ok "virtualenv ready"
[[ -f $BTC/deploy/notify.env ]] || sudo -u orb cp "$PAPER/deploy/notify.env" "$BTC/deploy/notify.env"
ok "phone alerts configured (same ntfy topic, pushes are tagged 'E20 BTC')"

# ------------------------------------------------------------------ 2. keys
say "2/5  E20 paper keys"
if [[ ! -f $BTC/.env ]]; then
  sudo -u orb tee "$BTC/.env" >/dev/null <<'ENVEOF'
# E20 BTC trial: the keys of a NEW paper account ("E20 BTC"),
# NOT the ORB or E16 paper accounts and NEVER live keys. ALPACA_PAPER must stay true.
ALPACA_API_KEY=PUT_THE_E20_PAPER_KEY_HERE
ALPACA_SECRET_KEY=PUT_THE_E20_PAPER_SECRET_HERE
ALPACA_PAPER=true
ENVEOF
  chmod 600 "$BTC/.env"; chown orb:orb "$BTC/.env"
fi
if grep -q "PUT_THE_E20" "$BTC/.env"; then
  cat <<MSG

    The E20 keys are not in yet. Do this now:

        sudo -u orb nano $BTC/.env

    Paste the NEW paper account's key and secret over the two placeholders,
    leave ALPACA_PAPER=true, save with Ctrl+O, Enter, then Ctrl+X.

    Then run this script again:

        sudo bash $PAPER/deploy/setup-btc.sh

MSG
  exit 0
fi
grep -q "^ALPACA_PAPER=true" "$BTC/.env" || die "$BTC/.env must contain ALPACA_PAPER=true"
ok "keys present"

# -------------------------------------------- 3. prove it is a fourth account
say "3/5  Checking the keys open a NEW, empty paper account"
cd "$BTC"
ACCT="$(sudo -u orb "$BTC/.venv/bin/python" - <<'PY'
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
    sys.exit(f"the E20 keys did not authenticate on the PAPER endpoint: {e}")
mine = str(a.account_number)
others = {"ORB paper": account("/opt/orb-bot/.env", True),
          "ORB live": account("/opt/orb-bot-live/.env", False),
          "E16": account("/opt/orb-noise/.env", True)}
for name, acct in others.items():
    if acct and acct == mine:
        sys.exit(f"these keys open {mine}, which is the {name} account. Use the NEW account's keys.")
held = [p.symbol for p in cli.get_all_positions()]
print(f"    account {mine}  equity ${float(a.equity):,.2f}  positions {held or 'none'}", file=sys.stderr)
if set(held) - {"BTCUSD", "BTC/USD"}:
    sys.exit(f"the account holds {held}: E20 needs an account with nothing but BTC/USD in it")
print(mine)
PY
)" || die "account check failed (message above)"
if grep -q "^BTC_ACCOUNT=" "$BTC/.env"; then
  [[ "$(grep '^BTC_ACCOUNT=' "$BTC/.env" | cut -d= -f2)" == "$ACCT" ]] \
    || die "BTC_ACCOUNT in $BTC/.env differs from the account these keys open ($ACCT)"
else
  printf 'BTC_ACCOUNT=%s\n' "$ACCT" | sudo -u orb tee -a "$BTC/.env" >/dev/null
fi
ok "E20 account is $ACCT (not ORB paper, not ORB live, not E16)"

# ------------------------------------------------------------- 4. dry look
say "4/5  Today's plan (read-only: market data, no orders)"
sudo -u orb "$BTC/.venv/bin/python" btc_bot.py plan || die "btc_bot.py plan failed (message above)"
sudo -u orb "$BTC/.venv/bin/python" btc_bot.py status || die "btc_bot.py status failed"

# ------------------------------------------------------------------ 5. units
say "5/5  Installing the systemd units"
cp "$BTC/deploy/orb-btc.service" "$BTC/deploy/orb-btc.timer" "$UNITS/"
systemctl daemon-reload
systemctl enable --now orb-btc.timer >/dev/null 2>&1
ok "timer enabled: one rebalance a day at 00:10 UTC"

cat <<MSG

------------------------------------------------------------------
E20 is installed. It runs itself once a day, just after the 00:00 UTC close
(19:10 New York in winter, 20:10 in summer), every day of the week.

  Check any time:   cd $BTC && sudo -u orb .venv/bin/python btc_bot.py status
  Today's target:   cd $BTC && sudo -u orb .venv/bin/python btc_bot.py plan
  Forward record:   cd $BTC && sudo -u orb .venv/bin/python btc_bot.py shadow
  Skip days:        sudo -u orb touch $BTC/state/BTC_DISARMED
  Stop for good:    sudo systemctl disable --now orb-btc.timer   (then sell the BTC
                    in the E20 paper account by hand if it holds any)

Most days it will do nothing: it only trades when the target weight moves by
more than 5% of the account. The ORB and E16 bots are not touched by any of this.
------------------------------------------------------------------

MSG
