# Live readiness — 2026-09-21

Two questions, deliberately kept apart:

1. **Is the machinery safe to point at real money?** — an ops checklist, below.
2. **Is there an edge worth pointing it at?** — §4. The answer today is no, and
   §1–3 do not change it.

Confusing these is the failure mode this file exists to prevent. Every ops item
can go green and the funding answer is still no.

---

## 0. Do this first — one command, everything else depends on it

`invariants.py` now FAILs 2026-09-21 with:

```
FAIL  excursion within stop — WBD MAE -2.57% past a -1.50% stop that never
      fired (exit +0.92%) — but the whole book's worst unrealized point all
      session was only -2.82
```

Two readings, and they need opposite responses:

- **(a) the excursion number is fiction** — the pre-fill bar scan scoring the
  opening range as the position's own MAE, i.e. the bug v1.17 §1 fixed. Then
  v1.17 is not running on the host that traded.
- **(b) the excursion is real** and the stop-limit blew through without
  filling. That is `VALIDATION-GO-NOGO.md` hard fail #3 — a risk-containment
  failure, and disqualifying on its own.

**The arithmetic says (a).** The 30s poll logs account equity, and with nothing
realized intraday, equity is start-of-day plus the unrealized P&L of everything
open. Across all 647 polls the deepest the *entire book* ever went underwater
was **−2.82** (10:08:07). WBD alone is claimed at **−17.27**. At 10:08 only
BITO and WBD were open, so WBD could only have been at −17.27 if BITO was
simultaneously at **+14.45 (+1.94%)** — above BITO's own recorded high-water
mark for the whole day (+13.42 / +1.92%), which it did not reach until 15:22.
The position never experienced that price.

Confirm which host is running what:

```bash
ssh root@157.180.31.101
cd /opt/orb-bot && sudo -u orb git log --oneline -3
```

Expect `e767a47 v1.17: excursion clamp, graceful shutdown, systemd + EOD
summary fixes` in that list. If it is absent, v1.17 was never deployed —
`RUN-ME-2026-09-20.md` §2 is the deploy procedure, and graceful shutdown and
the systemd crash-loop guard are not live either. If it IS present, stop and
treat this as reading (b).

**Either way the MAE column is not evidence for anything right now.** The
regression sweep flags six sessions — 08-21, 08-24, 09-09, 09-10, 09-18,
09-21 — so this has been corrupting excursion data for a month. Do not cite
MAE in any stop-width argument until a clean session produces it.

```powershell
cd "C:\Users\ethan\Claude\Projects\Trading bot"
python invariants.py --all --quiet
```

---

## 1. Blockers that must close before live keys go in `.env`

### 1a. Arming policy is inverted — the single most important item

The bot trades unless `state/DISARMED` exists. Fail-open: any state-directory
mishap, fresh checkout, or restore-from-backup produces an armed bot. Correct
for an unsupervised paper trial, wrong the moment the account is real. Your own
note (`UPDATE-2026-09-20.md`, Still open #4) says revisit before live money.

This is a code change and this week's config is frozen, so it is a
**post-Friday** task. Flagged here so it is not discovered on funding day.

### 1b. Rotate the Alpaca keys

Pasted into a chat on 09-17. Still open. The 09-17 session died on 401s across
`get_account`, the screener and the movers endpoint, so something may already
have changed underneath you.

```bash
sudo -u orb nano /opt/orb-bot/.env      # ALPACA_API_KEY, ALPACA_SECRET_KEY
sudo systemctl restart orb-bot          # only if a session is running
cd /opt/orb-bot && sudo -u orb .venv/bin/python -c "
from orb_bot.config import load_config
from orb_bot.broker import Broker
a = Broker(load_config()).trading.get_account()
print(a.account_number, a.status, a.equity)"
```

```powershell
notepad "C:\Users\ethan\Claude\Projects\Trading bot\.env"
```

### 1c. Disable the laptop's scheduled tasks

Still enabled, and still firing: the watchdog raised "bot NOT running" at
11:32 today about a host that has no job. It cost an hour of this evening's
recap chasing a phantom. Two hosts arming off one account is also how you get
a duplicate session — on a live account that is duplicate size.

```powershell
Get-ScheduledTask -TaskName "ORB*" | Select-Object TaskName, State
Get-ScheduledTask -TaskName "ORB*" | Disable-ScheduledTask
Get-ScheduledTask -TaskName "ORB*" | Select-Object TaskName, State   # Disabled
```

### 1d. Prove graceful shutdown with a position open

`RUN-ME-2026-09-20.md` §3. Untested code in the flatten path is the difference
between a clean stop and positions left open overnight. Must be done during
market hours with something open, or it proves nothing.

```bash
sudo systemctl stop orb-bot
journalctl -u orb-bot -n 40 --no-pager
sudo systemctl start orb-bot
```

Expect, in order: `SIGTERM received` → `SHUTDOWN requested` → `Flatten
verified: no open positions` → `Day done`. Then confirm zero open positions in
the Alpaca dashboard before trusting it.

---

## 2. Fixed today (observer-side only — bot code, config and state untouched)

| File | Change |
|---|---|
| `invariants.py` | Overnight-carry check no longer matches the flatten routine's own `still open … re-closing` retry lines. It was FAILing clean sessions; 08-05's real `Failed to close overnight AHCO` is still caught. |
| `invariants.py` | New check **`excursion within stop`** — a position's MAE cannot run past its bracket stop unless that stop fired. Catches both readings in §0, and reports the equity-derived bound as context. Nothing in the suite caught this before. |
| `make_trade_log.py` | `EXIT_TYPES` is now **derived** from `bookkeeping.py`'s `classify_exit`, so the spreadsheet and the trial totals cannot disagree. The literal dict survives as `EXIT_TYPES_OVERRIDE` for days whose log is not on this laptop. It was hand-typed before, and was three trades stale when picked up today. |

Known cosmetic limit: `(date, symbol)` collides when the same symbol is traded
twice in a day — `2026-08-05 SPCX` is the only case in 94 trades, both stops.

## 3. Deliberately NOT changed

**Trail arming.** It arms at ~+1.27% against a 1.25% width, locking
+0.00–0.04%. That looks useless but it is defensively fine — it swaps −1.5% of
risk for roughly breakeven, and the `TRAIL DEFERRED` guard correctly refuses to
arm at a negative lock. What it does not do is capture trend: INTC ran +25.10
MFE and realized +17.40. That is a tuning question, the config is frozen until
Friday, and tuning is the thing that has failed out-of-sample six times.
Post-week review, not now.

**WBD's 1-share flatten remnant.** Partial fill, handled correctly in three
passes. Observability nit at most.

---

## 4. The actual gate

| | |
|---|---|
| Paper trial | 94 trades, net **−25.64**, expectancy **−0.27/trade**, PF **0.96** |
| `VALIDATION-GO-NOGO.md` §1–4 | still the bar; "the bot does not clear it" (UPDATE-2026-09-20) |
| Track B | **NO-GO**, 4 of 5 criteria failed, closed |
| In-sample → out-of-sample sign reversals | three (Run 1, Run 5, Track B) |

09-18 and 09-21 together are **+54.47 on six trades, all winners**. That moved
the trial aggregate by more than a third of its total, which is a statement
about how thin 94 trades is, not evidence that something changed. Expectancy is
still negative.

The week's deliverable is five clean frozen-config sessions. One is in, and it
is not clean — §0 is unresolved. Money already sitting in the account is not a
deadline; you wrote that down before it landed, which was the right time to
write it.

**Nothing in §1–3 is a reason to fund. Closing every ops item gets you a
trustworthy measurement apparatus, which is what you need in order to find out
whether there is an edge — not a substitute for having found one.**
