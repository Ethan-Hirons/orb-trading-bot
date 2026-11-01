# ANALYSIS 2026-11-01: E16 close-out fix

## TL;DR

- **What happened on 10-30:** E16's market-on-close order filled 9 of 14 shares and expired the
  rest. The 16:05 "flatten" then **queued a buy of 5 for Monday's open**.
- **Where the account stands:** it is short 5 QQQ right now, with that queued buy still
  working. Under the old code, Monday's startup would send a *second* buy of 5, and the
  account would end up **long 5**.
- **The fix:**
  - **No more market-on-close.** The runner closes with market orders from 15:58. Each attempt
    either fills or is cancelled and confirmed dead before the next is sent, so two closing
    orders can never both fill.
  - **Nothing is queued after hours.** At 16:05, a still-open position gets an urgent alert and
    is left for the next morning.
  - **Stray orders are cancelled first.** At startup the runner cancels every working order
    before it acts.
- **Rule and evidence are unchanged.** The SIP shadow record (the E16 evidence) still exits at
  the close.
- **One Stage A wording is amended.** Exit slippage is now measured against the same-minute
  SIP price instead of the close. The close is 10.6 bp of noise away from 15:58.
- **Stage A restarts** with the first fixed session.
- **Tests:** 5 new regression tests. All 5 fail on the old code and pass on the new one. 11
  tests in the file pass.
- **Verdict: fixed, not deployed.** Ethan pushes, then pulls on the VPS. **This must happen
  before Monday 09:15 ET, or the runner must be disarmed for Monday** (steps in §6).

## 1. What happened on 2026-10-30

Sources: `noise/logs/noise_2026-10-30.log`, `noise/state/noise_ledger.csv`, and the E16 paper
account's order list, read on the VPS on 2026-11-01 (read-only; nothing was placed or
cancelled).

| time (ET) | event | order |
|---|---|---|
| 10:30:05 | price 694.05 < LB 694.64: go short | sell 14 DAY, **filled 14 @ 693.98** |
| 15:30:03 | `_close_out` sends market-on-close | buy 14 CLS, accepted |
| 15:58 | fallback check: MOC status `accepted`, not terminal, **so no fallback** | — |
| 16:00–16:02 | closing auction | CLS **filled 9 @ 690.60, 5 expired** at 16:02:18 |
| 16:05:00 | account short 5: "NOT FLAT", `close_all_positions` | buy 5 DAY, **queued (accepted) for the next open** |
| 16:05:01 | session ends `flat=False`, exit 3 | — |
| now | short 5 QQQ, plus the queued buy 5 | — |

**What Monday 09:15 would have done on the old code:** the leftover path sees short 5 and sends
its own buy 5. Both buys fill at the open, and the account is **long 5**. That's the
double-cover the task describes. The `test_regression_monday_leftover_with_a_queued_order`
test reproduces it exactly: two fills of 5 at 09:31.

## 2. Three root causes

1. **A partial MOC fill is invisible until after the close.** The order sits `accepted` until
   the 16:00 auction. Then it fills some, all or none. No regular-hours step can repair a
   partial fill.
2. **The fallback trusted a pending MOC.** It only fired on a terminal status, which a working
   MOC never has at 15:58.
3. **The failure path queued an order silently**, and the morning path didn't look for one.
   `close_all_positions` after hours creates a market order for the next open. The morning
   leftover logic sent its own close without checking for working orders.

## 3. The design chosen, and why

| option | why not / why |
|---|---|
| Keep MOC; cancel it before the venue cutoff if unfilled | An MOC is *always* unfilled before the auction, so there's nothing to test. A partial fill still can't be seen until 16:00. |
| Keep MOC; repair leftovers after hours with limit orders | After-hours liquidity is thin, paper simulation of it is unknown, and the position is still held overnight if the repair fails. |
| **Drop MOC; market orders from 15:58, one at a time** ✅ | Every fill is visible and repairable while the market is open. |

**The new close-out** (`noise_bot._close_out`):
1. **15:58:00:** cancel any working QQQ order and confirm it's dead.
2. **Loop until 15:59:40:** read the position. If flat, stop. Otherwise send one market order
   for the exact quantity held and wait up to 20 s for a fill.
   - If it doesn't fill, cancel it and **wait until the broker confirms** `canceled`/`filled`.
   - If the cancel can't be confirmed, stop trying rather than risk a second order on top.
3. **16:05:** if not flat, cancel anything working, send an urgent alert, write `pos_after = "?"`
   and exit with code 3 (systemd doesn't restart it). **No order is queued after hours.** The
   leftover is closed by the next session's startup.

**Startup** (`noise_bot.run`):
- It cancels every working QQQ order before acting.
- Before the open, it closes any leftover with one order that fills at the open.
- If the account still isn't flat after the open, it refuses to trade that day (exit 2,
  urgent alert).

**Checkpoint orders:** an entry or flip order that doesn't fill within 30 s is now cancelled
and confirmed. It used to be left working.

### Against the PREREG: "exits fill at the close" and slippage against the shadow price

The shadow (the evidence) exits at the 15:59 bar close. The runner now exits about 2 minutes
earlier. From the 2016–2026 QQQ cache:

| measure | value |
|---|---|
| close − P(15:58), mean | **+0.13 bp** (holdout +0.30 bp) |
| close − P(15:58), standard deviation | **10.6 bp** (holdout 8.4 bp) |
| H3 holdout: exit at 15:58 minus exit at the close, per active session | **−0.32 bp** (SD 8.5, n = 713, t ≈ −1.0) |

- **The earlier exit doesn't change what's being tested:** −0.32 bp against a +11.8 bp edge,
  and indistinguishable from zero.
- **Measured against the close, exit slippage would be noise:** a 10.6 bp SD per fill against
  a 2 bp gate, over roughly 12 exits in 20 sessions.
- **So exit slippage is now measured against the SIP price in the minute the order was sent,**
  the same comparison entries already use. The 15:58→close drift is printed separately as
  "exit timing vs the shadow's close (not a cost)".
- **Stage A criterion 3 is amended to say so.** It was changed before any fixed-code session
  exists and is logged in the PREREG (§5).

## 4. Code changes

| file | change |
|---|---|
| `noise_bot.py` | MOC removed (`market()` has no `at_close`); `flatten()` removed. New `cancel`, `open_order_ids`, `filled`, `settle_cancel`, `cancel_open`. `wait_fill` uses the injected clock, so tests are deterministic. `_close_out` is rewritten as above. Startup cancels working orders. `reconcile` restarts Stage A at the first `"v": 2` close row and scores exits same-minute. |
| `tests/test_noise_bot.py` | The fake broker now models the 10-30 behaviour (auction partial fills, pre-open and after-hours queues, stalled orders). 5 regression tests added; old tests updated to the new exit. |
| `PREREG-QQQ-NOISE-PAPER-2026-10-27.md` | Dated change-log entry: mechanics fix, criterion 3 amended, Stage A restarts. |

Unchanged:
- `index_scan.py` and the E16 rule.
- The systemd units and timers.
- `pipeline/noise.py`: it reads only `pos_before`/`pos_after` from the close row, and its 22
  tests still pass.

## 5. Tests: fail on the old code, pass on the new

Each regression test was run once against the pre-fix `noise_bot.py` (from `HEAD`) with the
same fake broker:

| regression test | old code | new code |
|---|---|---|
| MOC pending, then expiring (partial auction fill) | **FAIL** (sends MOC; not flat; queues order) | pass |
| MOC filled | **FAIL** (flat only in the auction, not before 16:00) | pass |
| fallback filled (first closing order hangs) | **FAIL** (`rc 3`, still short 100) | pass |
| 16:05 not flat | **FAIL** (after-hours close left queued) | pass |
| Monday leftover + queued order | **FAIL** (two buys of 5 fill at the open) | pass |

The full file passes 11 of 11 on the new code. All 15 test scripts in the repo pass, including
`test_noise_brief.py` (26 checks) and `test_pipeline.py` (22), which read the E16 ledger.

## 6. Deploy steps (Ethan)

`/opt/orb-noise` was cloned from `/opt/orb-bot`, but `setup-noise.sh` re-pointed it at GitHub.
Checked on 2026-11-01: `origin = https://github.com/Ethan-Hirons/orb-trading-bot.git`, at
`f4f2e43`, no local changes. Run git as `orb`, because as root git refuses with "dubious
ownership".

**1. On the laptop, push** (this commit and the other parts of today's work):

```
git push origin main
```

**2. On the VPS, before Monday 09:15 ET:**

```
ssh root@157.180.31.101
sudo -u orb git -C /opt/orb-noise remote -v
sudo -u orb git -C /opt/orb-noise pull --ff-only
cd /opt/orb-noise && sudo -u orb .venv/bin/python tests/test_noise_bot.py | tail -1
```

The last line must say `All 11 tests passed.` The units didn't change, so there's no
`daemon-reload`. The ORB instances are retired and need no pull.

**What Monday will do with the fix:** the 09:15 startup cancels Friday's queued buy 5
(alert: "leftover order"). It then sends one buy 5, which fills at the open, so the account is
flat. The session then runs normally. Stage A starts counting from Monday.

**If the fix can't be deployed before 09:15 Monday:** disarm the runner for Monday instead.

```
sudo -u orb touch /opt/orb-noise/state/NOISE_DISARMED
```

The runner then does nothing, and Friday's queued buy 5 alone fills at the open and flattens
the short. After the open, deploy as above and re-arm for Tuesday:

```
sudo -u orb rm /opt/orb-noise/state/NOISE_DISARMED
```

## 7. Verdict

**Fixed and tested, not deployed.** The close-out can no longer:
- carry a position overnight by itself;
- double-trade;
- leave a silent after-hours order.

If a close genuinely fails, the result is an urgent alert and one position for the morning,
which the next startup closes cleanly. E16's rule, schedule and evidence are untouched;
Stage A restarts.

## What I'm unsure about

- **How Alpaca's paper engine fills a 15:58 market order.** Paper fills are simulated. I expect
  immediate fills for 14–20 QQQ shares, but the cancel-and-retry path has only been exercised
  against the fake broker, not against Alpaca.
- **Whether the partial MOC fill is a paper-simulator artefact.** Live closing auctions for
  QQQ are deep, and a 14-share MOC would normally fill completely. That doesn't change the
  design: an exit has to be safe on the account it actually runs on.
- **The 15:59:40 cut-off.** An order sent at 15:59:39 has 20 s to fill. If it doesn't, the
  cancel is confirmed before 16:00, so nothing is left working, but that day ends not flat.
  In paper, market orders fill in about a second, so I expect the first attempt at 15:58:00 to
  be the only one on nearly every day.
- **Same-minute slippage is approximate.** An exit sent at 15:58:00 is compared with the price
  at 15:58:00 (the 15:57 bar close). A retry at 15:58:25 is compared with the same minute's
  price. That's fine for a mean over 20 sessions, but it isn't tick-precise.
- **Coverage on refusal days.** A day the runner refuses (exit 2) writes no ledger rows, so
  `reconcile` doesn't count it. `pipeline/noise.py` does flag session logs that have no ledger
  rows, so it won't go unnoticed, but Stage A's coverage line won't show it.
