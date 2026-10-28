# E16 PREREG: QQQ noise-area momentum, forward test (paper)

**Written 2026-10-27, after E15 and before any forward data exists.** Nothing below changes
once the first forward session (2026-10-28) is in. Results are appended at the end only.

**Status: DRAFT for Ethan's approval.** No account, config or bot is touched by this document.
`config.yaml`, `config-live.yaml` and both ORB instances stay as they are.

## The question

Does the noise-area momentum rule on QQQ, frozen exactly as E15 ran it, keep making money
on data that didn't exist when it was chosen?

## Why it earned a forward test, and why that's all it earned

From E15 (`PREREG-INDEX-2026-10-27.md`), net of 0.5 bp per side:

| | discovery 2016–21 | holdout 2022–26 | post-publication 2024-05-10+ |
|---|---|---|---|
| per active session | +4.88 [−0.70, +10.44] | **+11.79 [+5.18, +18.81]** | +8.71 [−1.09, +19.11] |
| unlevered Sharpe, per trading day | 0.73 | 1.53 | — |

Positive every year 2017–2026, both legs positive, +8.7 bp at 2 bp per side, and +7.2 bp
without its best 10 sessions.

**Against it:**
- QQQ was chosen after seeing four ETFs. The scored SPY test failed.
- The holdout overlaps what the paper's authors could see.
- IWM runs the opposite way.

**Only data from 2026-10-28 on is clean.** That is what this test uses.

## Mechanism (promotion gate criterion 5, stated without the backtest)

A move beyond QQQ's usual range for that time of day signals one-sided order flow, and three
forces keep that flow going through the session:
1. The Nasdaq-100 has the largest leveraged and inverse ETF complex (TQQQ, SQQQ, QLD…).
   Those funds must buy after up days and sell after down days, in proportion to the move,
   and that flow is known in advance.
2. Option dealers who are short gamma hedge in the direction of the move.
3. Large institutional orders worked over the day (VWAP/TWAP) keep pushing one way.

Small caps have less of (1) and (2) and more liquidity-providing reversal, which is what E14
and E15's IWM show. **Counter-evidence, recorded so it isn't forgotten:** E15's H1
(last-half-hour momentum) failed on QQQ, and that is where force (1) should be strongest.

## Frozen rules: identical to E15 H3, `index_scan._h3_day`, as of this commit

- **Instrument:** QQQ. Signal and trade on the same symbol.
- **σ(m):** mean of `|P(m)/Open − 1|` over the previous **14 full sessions**, skipping
  half-days and feed-gap days. Checkpoints m = 10:00, 10:30, …, 15:30.
- **Bands:** `UB = max(Open, prevClose) × (1 + σ)` and `LB = min(Open, prevClose) × (1 − σ)`.
  Open is the 09:30 SIP open. prevClose is the previous session's last regular-hours bar close.
- **Decision at each checkpoint:** above UB, go long or flip from short. Below LB, go short or
  flip from long. Otherwise hold.
- **Never** a VWAP stop, volatility targeting, leverage, or any position held overnight.
  Half-days are not traded.
- **Reference implementation:** `index_scan.h3_rows(..., cost, vwap_stop=False)`. A code
  change after 2026-10-28 is allowed only to make the code match these written rules, and
  must be logged below with the reason.

## Two records, kept apart on purpose

1. **Shadow record (the evidence).** Each evening, `python -u index_scan.py fetch` then
   `forward` replays the frozen rule on SIP bars for every session ≥ 2026-10-28. It needs no
   orders, so **the clock starts tomorrow** whether or not the paper bot exists yet.
2. **Paper execution (the costs).** A separate runner trades the same decisions on a
   **second Alpaca paper account**. It must be separate: ORB's `_flatten_all_verified` and
   `_close_overnight_leftovers` close every position in their account. This record measures
   what the shadow can't: live-feed decisions, real fill prices, and whether orders arrive.

**Primary metric:** shadow net return per active session at cost
`c* = max(0.5 bp, mean adverse slippage per side measured on the paper record)`.
The 2 bp stress figure is always reported next to it.

## Stage A: mechanics (first 20 sessions of paper execution)

All four must hold:
1. **Coverage:** decisions logged at all 12 checkpoints on ≥ 19 of 20 sessions, and the
   account verified flat after every close.
2. **Signal agreement:** the live decision, made on the IEX price, equals the SIP-shadow
   decision at **≥ 95%** of checkpoints.
3. **Cost:** mean adverse slippage against the shadow price **≤ 2 bp per side**, the level at
   which E15's holdout stayed significantly positive. Exits fill at the close.
4. **Isolation:** zero orders or positions in either ORB account, and ORB logs unchanged.

**Fail:** fix the mechanics and rerun Stage A. Rules are never changed to pass Stage A. The
shadow record keeps running regardless, because it doesn't depend on execution.

## Stage B: evidence (shadow record, active sessions counted from 2026-10-28)

Looks happen at fixed sample sizes, on the **first n** active sessions, never on a moving
window:

| look | active sessions | ≈ date | rule |
|---|---|---|---|
| 1 | 60 | ~Mar 2027 | report only |
| 2 | 120 | ~Aug 2027 | **stop for futility** if mean ≤ 0 |
| 3 | 180 | ~Jan 2028 | **stop for futility** if mean ≤ 0 |
| final | 250 | ~Jul 2028 | **PASS** needs all three below |

**PASS** at 250:
1. One-sided t ≥ 1.645 on the forward record alone.
2. The long-only version (what the live account can trade) has mean > 0.
3. The mean stays > 0 without the best 5 sessions.

A PASS goes to the promotion gate in EXPERIMENTS.md, with criterion 5 answered above.
Nothing goes live from this document.

**What these rules can and can't detect** (forward SD ≈ 94 bp per active session, from the holdout):

| true edge | P(stopped at 120) | P(PASS at 250) |
|---|---|---|
| +11.8 bp (holdout size) | 8% | ~63% |
| +8.7 bp (post-publication size) | 15% | ~43% |
| +6 bp (half the holdout) | 24% | ~26% |
| 0 (no edge) | 50% | ≤ 5% |

This is slow, and that's stated up front. ~59% of sessions are active, so 250 active
sessions is about 1.7 years. No honest design does much better: the edge is small next to the
daily noise. Futility stops only lower the false-pass rate, so there are no early-success
looks.

`index_scan.py forward` prints the current status line, computed by `e16_verdict` (tested in
`tests/test_index_scan.py`).

## Build list before Stage A (not started)

**Ethan:** create a second Alpaca **paper** account and put its keys in a separate env file
on the VPS. Account creation isn't something I do.

**Code, in its own commits, each with tests:**
1. `noise_bot.py`, a standalone runner that never imports `orb_bot/runner.py`. Each session:
   - 09:20: compute σ from 14 sessions of SIP history.
   - 09:46: read the SIP 09:30 open (by then past the free feed's 15-minute delay).
   - Each checkpoint: decide on the IEX price, using the **same decision function** as
     `index_scan._h3_day` (refactored into a shared pure function, so the traded rule cannot
     drift from the tested one).
   - Market orders sized at a fixed $10,000 notional.
   - 15:30: send a market-on-close exit, with a 15:58 market-order fallback if the MOC is
     rejected. Verify flat at 16:05.
2. A systemd service and timer on the VPS, `/opt/orb-noise`, with its own logs and ntfy alerts.
3. A nightly reconciliation that writes paper fills against shadow prices into a CSV, which
   feeds Stage A criteria 2 and 3 and `c*`.

## Guardrails

- Live stays frozen. The ORB live and paper instances, and the E12 plan, are unaffected.
- No tuning. The VWAP-stop variant, other ETFs and other lookbacks are **descriptive only** in
  the forward record. If one looks better, that is a new PREREG starting on new data.
- If the shadow and paper records disagree in sign over Stage B, that has to be explained
  before any promotion. Fills may be eating the edge.

---

## Forward record

*(appended at each look; deviations and code fixes logged here with dates)*

**2026-10-27, after 7baf8ec, before the first forward session. Rule unchanged.**
- `index_scan.py`: the H3 decision is split into the pure functions `sigma_at`, `bands` and
  `target`, which `_h3_day` and `noise_bot.py` both call, so the traded rule can't drift from
  the tested one. `index_scan_out.txt` and `index_scan_posthoc.txt` reproduce **byte for
  byte** after the change.
- Half-days for the forward period: 2026-11-27 and 2026-12-24 were added to `EARLY_CLOSES`.
  `fetch` now also saves Alpaca's exchange calendar (`.index_cache/calendar.json`), which
  matches the hard-coded list exactly for 2016–2026 (21 days) and lists the half-days ahead.
- `noise_bot.py` (the paper runner), its tests (`tests/test_noise_bot.py`), the systemd units
  and `deploy/setup-noise.sh` were built to the build list above. Stage A starts on the
  runner's first session.
