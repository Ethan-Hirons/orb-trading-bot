# Moving the ORB bot off the laptop

**Why:** every process failure this project has had traces to one cause — the
bot only lives while Ethan's laptop is open and online, and that laptop goes in
a bag between class and practice during market hours. 2026-08-26 and 08-27 were
mid-day kills; 08-28 and 09-01 through 09-04 never started; 09-08 froze for
2h56m mid-session (a 4-second retry backoff that took nearly three hours =
suspend, not a network blip).

**The specific danger:** the bracket stop and the native trailing stop live at
Alpaca and keep working while the laptop sleeps. The **EOD flatten does not** —
it is a loop in `runner.py` at ~15:50 ET. A laptop asleep at 15:50 with an open
position carries it overnight. That is the AHCO incident (Aug 4→5, −37.37,
≈3.6× intended risk).

---

## 1. Pick a host

| option | cost | notes |
|---|---|---|
| Oracle Cloud always-free ARM VM | free | 4 vCPU / 24 GB; plenty. Signup is fussy. |
| Hetzner CX22 | ~€4/mo | easiest, reliable, EU or US regions |
| DigitalOcean basic droplet | $6/mo | most documentation |
| Raspberry Pi in the dorm | ~$70 once | dorm power + Wi-Fi make it the least reliable |

Ubuntu 22.04 or 24.04. Anything with 1 vCPU / 1 GB is enough — the bot is a
polling loop, not a compute job.

## 2. Base setup

```bash
# as root on the new box
adduser --system --group --home /opt/orb-bot orb
apt update && apt install -y python3 python3-pip python3-venv git curl

# CRITICAL: the systemd timers below use local time so DST is handled for you
timedatectl set-timezone America/New_York
timedatectl        # confirm it says EDT or EST
```

## 3. Get the code across

```bash
git clone <your repo> /opt/orb-bot     # or scp the folder
cd /opt/orb-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
chown -R orb:orb /opt/orb-bot
```

If you use the venv, change `python3` to `/opt/orb-bot/.venv/bin/python` in
`deploy/start.sh`.

## 4. Secrets

```bash
sudo -u orb tee /opt/orb-bot/.env >/dev/null <<'ENV'
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ENV
chmod 600 /opt/orb-bot/.env
```

**Paper keys only** until you have watched this run for a couple of weeks.
Keys on a rented box are a different risk profile from keys on your laptop.

## 5. Phone alerts (2 minutes, free)

Install the **ntfy** app, pick a long random topic name, subscribe to it, then:

```bash
echo 'NTFY_TOPIC=orb-bot-<long-random-string>' > /opt/orb-bot/deploy/notify.env
chown orb:orb /opt/orb-bot/deploy/notify.env
/opt/orb-bot/deploy/notify.sh "test" "hello from the VPS"
```

Anyone who guesses the topic can read your alerts, so make it random. Nothing
sensitive goes in the messages.

## 6. Install the units

```bash
cp /opt/orb-bot/deploy/orb-bot.service        /etc/systemd/system/
cp /opt/orb-bot/deploy/orb-bot.timer          /etc/systemd/system/
cp /opt/orb-bot/deploy/orb-healthcheck.service /etc/systemd/system/
cp /opt/orb-bot/deploy/orb-healthcheck.timer   /etc/systemd/system/
cp '/opt/orb-bot/deploy/orb-alert@.service'    /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now orb-bot.timer orb-healthcheck.timer
systemctl list-timers 'orb-*'    # confirm the next fire time is tomorrow 09:25 ET
```

## 7. Smoke test before trusting it

```bash
sudo -u orb /opt/orb-bot/.venv/bin/python /opt/orb-bot/smoke_test.py
systemctl start orb-bot          # run a session manually, market open or not
journalctl -u orb-bot -f         # watch it
```

On a closed market you should see `Market closed. Next open ...` — that is the
bot working. Stop it with `systemctl stop orb-bot`.

---

## Daily operation

| you want to | do this |
|---|---|
| skip tomorrow | `touch /opt/orb-bot/state/DISARMED` |
| resume | `rm /opt/orb-bot/state/DISARMED` |
| stop a session in progress | `systemctl stop orb-bot` — **does NOT flatten**, see below |
| see today's log | `tail -f /opt/orb-bot/logs/orb_$(date +%F).log` |
| check health | `python3 /opt/orb-bot/invariants.py` |

An SSH client on your phone (Termius, Blink) makes all of this doable between
classes.

## The arming policy changed — read this

On the laptop, arming was **opt-in**: you ran `arm.py` each morning or nothing
happened. That existed because the bot ran on a machine nobody was supervising.
It is also exactly what cost six consecutive sessions.

On an always-on host `deploy/start.sh` inverts it: the bot arms and trades every
market weekday unless `state/DISARMED` exists. **Revisit this before funding
real money** — with live capital, opt-in is the safer default. Deleting the
`DISARMED` check from `start.sh` restores manual arming.

## What this does and does not fix

Fixes: sessions that never start, mid-day kills, laptop sleep, no alert when a
day is missed, and the unprotected EOD flatten.

Does not fix: anything about whether the strategy has an edge. The bot will now
reliably execute a strategy whose expectancy is still negative (83 trades,
−56.28). That is the point — a reliable process is what makes the sample worth
collecting.

## Still open after this

- Incremental trade booking. `trade_history.csv` is only written at day end, so
  any interrupted session loses its trades until `backfill.py` runs.
- `invariants.py` cannot see a day that never started; `healthcheck.sh` covers
  that gap from outside, but the checker itself is still blind.
- **There is no graceful shutdown that flattens.** `run.py` catches
  KeyboardInterrupt, logs "Bot interrupted by user" and exits — it does not
  close positions. So `systemctl stop` mid-session leaves exactly the mess the
  08-26 and 08-27 Ctrl+C kills left: open positions, no `Day done`, nothing
  written to `trade_history.csv`. The unit sends SIGINT rather than SIGTERM so
  at least the kill is logged, but the real fix is a signal handler in `run.py`
  that calls `_flatten_all_verified` before exiting. **Until that exists, close
  positions in the Alpaca dashboard by hand after any mid-session stop.**
