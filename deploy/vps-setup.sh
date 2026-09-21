#!/usr/bin/env bash
# ORB bot -> VPS, one shot. Run as root on a fresh Ubuntu 22.04/24.04 box.
#   scp this file over, then:  bash vps-setup.sh
# Full explanation of every step is in README-VPS.md.
set -euo pipefail

# ============================ EDIT THESE THREE ============================
REPO_URL="git@github.com:YOURNAME/trading-bot.git"   # or "" to skip clone and scp the folder yourself
NTFY_TOPIC="orb-bot-CHANGE-ME-to-something-long-and-random"
# .env is written interactively at the end -- keys are never stored in this file
# =========================================================================

APP=/opt/orb-bot

echo "==> 1/8  packages"
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl

echo "==> 2/8  timezone (CRITICAL: the timers use local time)"
timedatectl set-timezone America/New_York
timedatectl | grep -i "time zone"

echo "==> 3/8  service user"
id orb &>/dev/null || adduser --system --group --home "$APP" orb

echo "==> 4/8  code"
if [[ -n "$REPO_URL" ]]; then
  [[ -d "$APP/.git" ]] || git clone "$REPO_URL" "$APP"
else
  echo "    REPO_URL empty -- expecting you already put the code in $APP"
  [[ -f "$APP/run.py" ]] || { echo "    ERROR: $APP/run.py not found"; exit 1; }
fi

echo "==> 5/8  virtualenv"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
# point the launcher at the venv interpreter
sed -i "s|^python3 arm.py|$APP/.venv/bin/python arm.py|; s|^exec python3 -u run.py|exec $APP/.venv/bin/python -u run.py|" "$APP/deploy/start.sh"
chmod +x "$APP"/deploy/*.sh

echo "==> 6/8  phone alerts"
echo "NTFY_TOPIC=$NTFY_TOPIC" > "$APP/deploy/notify.env"
chmod 600 "$APP/deploy/notify.env"

echo "==> 7/8  systemd units"
cp "$APP/deploy/orb-bot.service"          /etc/systemd/system/
cp "$APP/deploy/orb-bot.timer"            /etc/systemd/system/
cp "$APP/deploy/orb-healthcheck.service"  /etc/systemd/system/
cp "$APP/deploy/orb-healthcheck.timer"    /etc/systemd/system/
cp "$APP/deploy/orb-alert@.service"       /etc/systemd/system/
systemctl daemon-reload

echo "==> 8/8  API keys"
if [[ ! -f "$APP/.env" ]]; then
  read -rp "    ALPACA_API_KEY (PAPER key): " K
  read -rsp "    ALPACA_SECRET_KEY: " S; echo
  printf 'ALPACA_API_KEY=%s\nALPACA_SECRET_KEY=%s\n' "$K" "$S" > "$APP/.env"
fi
chmod 600 "$APP/.env"
chown -R orb:orb "$APP"

systemctl enable --now orb-bot.timer orb-healthcheck.timer

echo
echo "======================= DONE -- now verify ======================="
"$APP/deploy/notify.sh" "ORB bot" "VPS setup finished -- if you see this, alerts work."
echo
systemctl list-timers 'orb-*' --no-pager
echo
echo "Next open should be the coming weekday at 09:25 ET. Then:"
echo "  sudo -u orb $APP/.venv/bin/python $APP/smoke_test.py"
echo "  systemctl start orb-bot && journalctl -u orb-bot -f"
echo "  touch $APP/state/DISARMED   # to skip a day"
echo "REMINDER: systemctl stop does NOT flatten positions. Close by hand in Alpaca."
