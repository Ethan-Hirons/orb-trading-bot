# ORB Bot — Roadmap & Status

_Last updated: 2026-07-17 (evening — v1.5 upgrades shipped)_

## Upgrades shipped 2026-07-17 (v1.5)

Prompted by 2026-07-16/17: INTC's EOD close was lost and the short carried
overnight (invisible to the stats), and on 07-17 five targets broke out
within 11 minutes but none confirmed in time — the only entry (ATAI, 1h
after the open) went nowhere and was held 5h19m for −0.3%.

1. **Verified flatten** — every flatten (EOD, circuit breaker, error path)
   now polls until positions are actually zero, re-closing stragglers, and
   logs an ERROR naming anything still open. No more silent overnight carries.
2. **Overnight-leftover sweep at the open** — if a position somehow survived
   to the next open anyway, it is closed immediately instead of drifting
   all day.
3. **Cross-day trade recovery** — backfill now pairs round trips across days
   (dated by the closing fill), so a carried position's trade still lands in
   `trade_history.csv` and the trial stats stay honest. Also runs at EOD, not
   just at startup. Run `python backfill.py` once to recover INTC 07-16/17.
4. **Faster breakout confirmation** — a confirming close in any of the last
   `confirm_lookback_bars` (3) now counts (was: the single latest bar), and
   the volume filter needs `volume_filter_ratio` (0.5) x avg OR volume
   (was 1.0, which mid-morning bars almost never beat).
5. **Time stop** — `exits.max_hold_minutes: 180`: a position that has hit
   neither TP nor SL by then is dead money and gets closed, freeing the
   slot and notional for the intraday re-screen's fresher candidates.

## Where things stand

- Bot is **built, tested (18/18 unit tests pass), and running live on paper**.
- Strategy: ORB entries on a **news + volatility screened** universe (up to 5
  targets/day), **volatility-scaled 3–5% take-profit / ~3% stop (stop-limit,
  slippage-capped)**, daily circuit breaker at **+6% / −5%**.
- **NEW (v1.1):** intraday news rescan every 60s — fresh headlines update
  target biases, and strongly adverse news closes an open position early.
- **NEW (v1.1):** end-of-day summary (win rate, expectancy, max drawdown) and
  persistent `state/trade_history.csv`; view anytime with `python report.py`.
- Alpaca paper account set up; API keys in `.env`; MFA enabled.

## Real plan

- Fund the live account after the paper trial proves out.
- Evaluate over **~30–50 completed trades (≈3–4 weeks)** before going live.
- The evaluation clock starts **once the paper balance is resized** (below).
- When going live: start with a **fraction** of the planned size, scale up only if it holds.
- Shorting needs a **margin** account; if funding a cash account, set
  `strategy.allow_shorts: false`.

## Upgrades shipped 2026-07-01 (v1.1)

1. ~~End-of-day summary log~~ ✅ — `orb_bot/report.py` reconstructs each day's
   round trips from Alpaca fills, appends to `state/trade_history.csv`, and
   logs running win rate / expectancy / max drawdown. `python report.py`
   prints the trial stats anytime.
2. ~~Quiet the logs~~ ✅ — "skipped (news bias)" now logs once per symbol per
   day; Pydantic serializer warning silenced.
3. ~~Screener refinements~~ ✅ — `screener.max_abs_gap_pct: 75` skips the
   +200% names; optional `news.min_articles` filter.
4. ~~Slippage handling~~ ✅ — stops are now **stop-limit** (`exits.stop_limit`,
   0.5% offset). Caps slippage like the SSTK fill; small no-fill risk is
   bounded because the bot always flattens before the close.
5. **NEW: Intraday news rescan** ✅ — `news.rescan_interval_seconds: 60`.
   Fresh headlines on targets/positions are scored with a weighted lexicon
   (strong words like bankruptcy/fraud/offering/merger count double):
   - untraded target: bias updates if |score| ≥ `min_sentiment_for_bias`
   - open position: closed early if fresh score is against it by ≥
     `news.adverse_exit_score` (0.5) — set `exit_on_adverse: false` to disable
6. **NEW: Crash resilience** ✅ — transient API errors no longer kill the
   session; the loop backs off and retries (flattens + stops after 10
   consecutive failures).

## Still to do (manual)

1. ~~Resize the paper account to the planned test size~~ ✅ **Done 2026-07-01**:
   new paper account "Paper test" created at the planned size, new API keys in `.env`.
   **The evaluation clock starts with the next session.** Verify on first run
   that the log shows the expected starting equity.

## Open decisions / ideas parked

- News bias is a **hard block** on entries (won't trade against it). Left
  strict by choice; intraday rescan now keeps the bias current all day.
- Possible later: move to a cloud VPS so it runs unattended; auto-arm each
  morning via a scheduler.
- Possible later: replace the keyword sentiment with an LLM/API scorer once
  the trial shows the news signal is worth the spend.

## Notes from day one (2026-07-01)

- Targets picked: TC (+200%), SDOT, JEM (+100%), SSTK (short), NBIZ — very
  speculative small-caps, which is the nature of chasing 3–5% intraday.
  (The new gap cap would have excluded TC and JEM.)
- Trades: SSTK short stopped (~−$32, slippage → prompted the stop-limit fix),
  SDOT long stopped (~−$24), DRAM long opened and left running. Two small
  losers = noise, not signal.
