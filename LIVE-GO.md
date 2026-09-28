# Going live — what to run

_Written 2026-09-27 for a first live session on 2026-09-28._

Everything below is mechanical. The judgement call is yours and you have made
it; this file is about making sure the machine does what you intend.

**Read first:** live and paper are now two separate instances, two accounts,
two directories, two systemd units. They share one git repo and one strategy
config. `orb_bot/preflight.py` refuses to start either one if the credentials
in its directory do not match the mode it is running in.

| | paper | live |
|---|---|---|
| directory | `/opt/orb-bot` | `/opt/orb-bot-live` |
| unit | `orb-bot.service` | `orb-bot-live.service` |
| arming | automatic every weekday | **`python arm.py` by hand, or no session** |
| restart on crash | yes, 5/hour | **no — a human looks at it** |
| position cap | $700 | $600 (3 × $600 fits $2,000 without borrowing) |
| daily loss breaker | 5% | **2%** |
| trades/day | 10 | 3 |
| every strategy parameter | — | **identical, inherited** |

---

## 1. Push and deploy (PowerShell, in the repo)

```powershell
cd "C:\Users\ethan\Claude\Projects\Trading bot"
git log --oneline -3
git push
```

## 2. Update the paper instance

```bash
ssh root@157.180.31.101
cd /opt/orb-bot && sudo -u orb git pull
sudo cp deploy/orb-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo -u orb ORB_NO_FILE_LOG=1 python3 tests/test_logic.py | tail -2
sudo -u orb ORB_NO_FILE_LOG=1 python3 tests/test_preflight.py | tail -2
```

Expect `All 35 tests passed.` and `All 8 checks passed.`

## 3. Create the live instance

```bash
sudo -u orb git clone /opt/orb-bot /opt/orb-bot-live
cd /opt/orb-bot-live
sudo -u orb git remote set-url origin https://github.com/Ethan-Hirons/orb-trading-bot.git
sudo -u orb python3 -m venv .venv
sudo -u orb .venv/bin/pip install -r requirements.txt
sudo -u orb mkdir -p logs state
sudo -u orb cp /opt/orb-bot/deploy/notify.env deploy/notify.env
```

**Then put the LIVE keys in `/opt/orb-bot-live/.env`** — live key, live secret,
and `ALPACA_PAPER=false`. Do not copy the paper `.env` into this directory;
preflight will refuse to start, which is the point, but you will have wasted a
morning.

```bash
sudo -u orb nano /opt/orb-bot-live/.env
sudo chmod 600 /opt/orb-bot-live/.env
```

Verify it is the live account, and check the equity is what you transferred:

```bash
cd /opt/orb-bot-live && sudo -u orb .venv/bin/python -c "
from orb_bot.config import load_config
from orb_bot.broker import Broker
a = Broker(load_config()).trading.get_account()
print(a.account_number, a.status, 'equity', a.equity, 'cash', a.cash)"
```

## 4. Install the live units

```bash
sudo cp /opt/orb-bot-live/deploy/orb-bot-live.service \
        /opt/orb-bot-live/deploy/orb-bot-live.timer \
        /opt/orb-bot-live/deploy/orb-summary-live.service \
        /opt/orb-bot-live/deploy/orb-summary-live.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/orb-bot-live.service   # must print nothing
sudo systemctl enable --now orb-bot-live.timer orb-summary-live.timer
systemctl list-timers 'orb-*' --all
```

## 5. Prove preflight actually refuses

Do this before you trust it. It should **fail**, loudly:

```bash
cd /opt/orb-bot-live
sudo -u orb ORB_MODE=live ORB_CONFIG=config-live.yaml .venv/bin/python run.py
```

Expected: `PREFLIGHT FAILED -- refusing to trade` and
`state/LIVE_CONFIRMED is missing`. If it starts trading instead, stop
everything and tell me.

Then, and only then:

```bash
sudo -u orb touch /opt/orb-bot-live/state/LIVE_CONFIRMED
```

That file is the switch. Nothing in the repo creates it. Deleting it takes the
account offline at the next start.

## 6. Arm it for tomorrow

The live bot will not arm itself. On any morning you want a live session,
before 09:25 ET:

```bash
ssh root@157.180.31.101 "cd /opt/orb-bot-live && sudo -u orb .venv/bin/python arm.py"
```

If you do not, the timer fires, `start.sh` sees no arm, pushes
`ORB LIVE not armed` to your phone, and exits. A skipped live day is a normal,
healthy outcome.

---

## Kill switch — know this before you need it

```bash
# Stop today's live session and FLATTEN every position (v1.17 graceful stop):
sudo systemctl stop orb-bot-live

# Keep it off for future days:
sudo -u orb touch /opt/orb-bot-live/state/DISARMED

# Take live off the table entirely:
sudo -u orb rm /opt/orb-bot-live/state/LIVE_CONFIRMED
```

`systemctl stop` flattens because of the SIGTERM handler shipped in v1.17 and
proved on 09-24. If the flatten ever hangs, send it twice and close by hand in
the Alpaca dashboard.

---

## What to expect, so it is not a surprise later

- **Live will underperform paper on identical signals.** Alpaca's paper engine
  fills optimistically; live pays the real spread plus SEC/FINRA fees paper
  does not simulate. The 100-trade baseline of −0.23/trade is a *paper*
  number. See `EXPERIMENTS.md` E3 — measuring this gap is the most valuable
  thing running both instances buys you.
- **The account is right at the $2,000 margin line.** FINRA retired the
  pattern-day-trader rule in 2026 and Alpaca replaced it with an intraday
  margin framework, so day-trade counting is no longer a constraint — but a
  margin account still needs $2,000 of equity to carry an intraday debit.
  `config-live.yaml` sizes 3 × $600 so the bot never borrows. If equity falls
  under $2,000, lower `max_position_notional` again.
- **Three trades a day at 1% risk means a bad day is about −$40** and the
  breaker ends the session at −2%.
- **MAE figures before v1.18 are unreliable** and some after it will be too
  until you see `EXCURSION ... DROPPED an impossible adverse reading` lines
  and can confirm the filter is doing its job. Do not argue about stop width
  from that column yet.

## Sources

- [FINRA retires the PDT rule — Alpaca's intraday margin framework](https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/)
- [Alpaca docs — the intraday margin rule](https://docs.alpaca.markets/us/docs/the-intraday-margin-rule)
