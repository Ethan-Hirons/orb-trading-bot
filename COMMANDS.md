# Command reference — ORB bot on the VPS

Server: `root@157.180.31.101` (Hetzner, Helsinki, Ubuntu 24.04)
Bot path: `/opt/orb-bot` · Logs: `/opt/orb-bot/logs/` · Runs as user `orb`

**The bot starts itself at 09:25 ET every weekday. Nothing here is required to
make it run.** These are for looking at it, controlling it, and keeping your
laptop copy in sync.

---

## 1. Daily — did it run, and how did it go?

From PowerShell on the laptop, or any SSH client on your phone:

```bash
ssh root@157.180.31.101
```

Then on the server:

```bash
# watch today's session live (Ctrl+C to stop watching — does NOT stop the bot)
tail -f /opt/orb-bot/logs/orb_$(date +%F).log

# read today's session after the close
tail -60 /opt/orb-bot/logs/orb_$(date +%F).log

# the end-of-day summary only
grep -A6 "END-OF-DAY SUMMARY" /opt/orb-bot/logs/orb_$(date +%F).log

# the invariant checks — ALWAYS run this before calling a red day "normal"
cd /opt/orb-bot && ./.venv/bin/python invariants.py

# did the trail fire today?
grep -E "TRAIL (ACTIVATED|DEFERRED|EXIT)" /opt/orb-bot/logs/orb_$(date +%F).log

# service state and recent journal
systemctl status orb-bot --no-pager
journalctl -u orb-bot -n 50 --no-pager

# when does it next fire?
systemctl list-timers 'orb-*'
```

---

## 2. Pull the logs down to the laptop

**This is what keeps the 16:15 recap task working.** The recap reads `logs/` in
the Windows repo folder; since the bot moved, that folder goes stale unless you
sync it. Run this in **PowerShell on the laptop** after the close:

```powershell
scp -r root@157.180.31.101:/opt/orb-bot/logs/* "C:\Users\ethan\Claude\Projects\Trading bot\logs\"
scp root@157.180.31.101:/opt/orb-bot/state/trade_history.csv "C:\Users\ethan\Claude\Projects\Trading bot\state\"
scp root@157.180.31.101:/opt/orb-bot/state/daily_state.json "C:\Users\ethan\Claude\Projects\Trading bot\state\"
```

Save it as a one-click script — create `sync-logs.ps1` in the repo folder with
those three lines, then run `.\sync-logs.ps1` whenever you want fresh data.

**Until you run this, a blank or stale recap means nothing.** It is the recap
looking at the wrong machine, not the bot failing to trade. The authoritative
copy of every log now lives on the server.

---

## 3. Control

```bash
# skip tomorrow's session (and every session until you undo it)
touch /opt/orb-bot/state/DISARMED

# resume
rm /opt/orb-bot/state/DISARMED

# am I disarmed right now?
ls -la /opt/orb-bot/state/DISARMED 2>/dev/null && echo "DISARMED" || echo "will trade"

# run a session right now, off-schedule
systemctl start orb-bot

# stop a running session -- ⚠ DOES NOT FLATTEN POSITIONS
systemctl stop orb-bot
```

**⚠ `systemctl stop` mid-session leaves open positions.** `run.py` catches the
signal, logs "Bot interrupted by user", and exits without closing anything. If
you stop a live session, **close positions by hand in the Alpaca dashboard**.
This is the 08-26/08-27 failure mode and it is still unfixed.

Remember the arming is **inverted** on the server: doing nothing means it
trades. Opting out is now the manual step.

---

## 4. Alerts

ntfy topic: `orb-bot-64daf7d341cb127c` (subscribe in the ntfy phone app)

```bash
# send yourself a test
sudo -u orb /opt/orb-bot/deploy/notify.sh "test" "hello"
```

Silence at 09:45 = the session started. A buzz = go look. **`notify.sh` exits 0
even when nobody receives the message**, so the only real proof is the phone.

---

## 5. SSH from your phone

Do **not** copy your laptop's private key to your phone. Generate a separate
one in the app instead.

1. Install **Termius** (iOS/Android) or **Blink** (iOS).
2. In the app: Keychain → generate a new key → copy its **public** key.
3. From your laptop, add it to the server:

```powershell
ssh root@157.180.31.101
```

```bash
nano ~/.ssh/authorized_keys
```

Paste the phone's public key on a new line, Ctrl+O, Enter, Ctrl+X.

4. In Termius: new host → `157.180.31.101`, user `root`, select that key.

If you ever lose the phone, delete its line from `authorized_keys` and it loses
access — which is exactly why it gets its own key rather than a copy of yours.

---

## 6. Git — keeping laptop and server in sync

The server was deployed by `git clone`, so code changes flow: **edit on laptop →
commit → push → pull on server.**

On the **laptop** (PowerShell, in the repo folder):

```powershell
cd "C:\Users\ethan\Claude\Projects\Trading bot"
git status
git add -A
git commit -m "your message"
git push
```

On the **server**:

```bash
cd /opt/orb-bot
git pull
chown -R orb:orb /opt/orb-bot
systemctl restart orb-bot      # only if a session is running
```

**Two cautions.**

`.env` is not in git and must never be committed — it holds your API keys. It
exists separately on each machine.

`state/` files (`trade_history.csv`, `daily_state.json`) are written by the bot
on the server. If `git pull` complains about local changes to them, the
**server's copy is the real one** — never overwrite it with the laptop's. Stash
the laptop side instead:

```bash
git stash && git pull && git stash drop
```

---

## 7. Server housekeeping

```bash
# pending updates (there were 60, incl. 54 security, as of 2026-09-17)
apt update && apt upgrade -y

# reboot -- do it in the EVENING, never during market hours
reboot

# disk and memory
df -h /
free -h
```

After any reboot, confirm the timers came back:

```bash
systemctl list-timers 'orb-*'
```

---

## 8. Still outstanding

1. `orb-bot.service` has `StartLimitIntervalSec`/`StartLimitBurst` in
   `[Service]`; they belong in `[Unit]` and are currently ignored, so the
   restart-loop guard is inactive.
2. Rotate the Alpaca paper keys (pasted into a chat 2026-09-17), then update
   `.env` on **both** machines.
3. No graceful shutdown — see §3.
4. Automate §2 so the recap always has fresh logs.


---

## Deploy v1.17 to the VPS (2026-09-20)

Run from the repo on Windows. The commits exist locally; nothing reaches the
server until you push and pull.

```powershell
git push                                   # sandbox cannot authenticate; you must
ssh root@157.180.31.101
```

Then on the server:

```bash
cd /opt/orb-bot
sudo -u orb git pull
sudo install -m 755 -o orb -g orb deploy/eod-summary.sh /opt/orb-bot/deploy/eod-summary.sh
sudo cp deploy/orb-bot.service deploy/orb-summary.service deploy/orb-summary.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now orb-summary.timer
systemctl list-timers 'orb-*'              # expect orb-bot, orb-healthcheck, orb-summary
sudo systemd-analyze verify /etc/systemd/system/orb-bot.service   # must print NOTHING
```

`systemd-analyze verify` printing nothing is the check that the StartLimit
keys are now in a section systemd actually reads — it was the source of the
silent `Unknown key name` before.

Smoke-test the summary push without waiting for 16:05:

```bash
sudo -u orb ORB_DATE=2026-09-18 bash /opt/orb-bot/deploy/eod-summary.sh
```

Your phone should show `ORB 2026-09-18: Trades: 3 | End equity: 1990.60 | ...`.

Test the graceful shutdown **while the market is open and a position is
open** — that is the only test that proves anything:

```bash
sudo systemctl stop orb-bot
journalctl -u orb-bot -n 30 --no-pager      # expect: SHUTDOWN requested ... Flatten verified
```

Then confirm zero open positions in the Alpaca dashboard.

## Pull the logs down to the laptop

```powershell
powershell -ExecutionPolicy Bypass -File sync_logs.ps1
```

## Disable the stale laptop launcher

The laptop no longer runs the bot, but its tasks still fire and the watchdog
still cries wolf (09-18 14:33).

```powershell
Get-ScheduledTask -TaskName "ORB*" | Disable-ScheduledTask
Get-ScheduledTask -TaskName "ORB*" | Select-Object TaskName, State
```
