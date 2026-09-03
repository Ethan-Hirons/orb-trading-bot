# ORB Bot — Deep Tuning Analysis

_Written 2026-07-29 evening, after the trial's 30th completed trade. Input to the Aug 1–2 tuning session and the Aug 3–7 validation week._

## 1. Where the bot actually stands

30 completed trades, net **+0.17% on equity**, win rate 46.7%, avg win +15.65 vs avg loss −13.48, max drawdown 131.16. Statistically this is indistinguishable from zero: the per-trade standard deviation is ~$15, so the 95% confidence interval on expectancy is roughly ±$5.4/trade around +$0.11. The trial has proven the *plumbing* (verified flatten, entry rollback on no-fill, dedupe, news rescan, crash resilience all worked in production) but not the *edge*. The edge argument currently rests on the sweep — which is why the finding in §2 matters more than anything else in this document.

The engineering is in good shape. Real bugs the trial surfaced (overnight carry, slow confirmation, correlated ETF twins) each got fixed and each fix demonstrably worked. MPLT (07-28) and HURN (07-29) both hit the 180s no-fill rollback cleanly — the entry-limit + rollback path works.

## 2. CRITICAL: the sweep tested a different trailing stop than the one v1.8 ships

This is the single most important thing to resolve before the Saturday flip.

**The backtest** (`backtest.py` line ~185) seeds `peak = fill` and applies the trail from the first bar. With `trail_pct 1.0`, the trail stop starts at `fill × 0.99` — tighter than the −3% stop — so the sim effectively runs a **−1% initial stop from entry** that then trails. That is where the sweep's numbers come from: `trail:183/191` exits, avg loss cut from −9.54 to −4.03, and the entire jump from −244…−375 (no-trail rows) to +79…+99.

**The live bot** (`runner._check_trailing`) has an explicit gate: `if desired <= entry: continue` — the trail only takes effect once the trade is up more than ~1.01% (past breakeven). Until then the full −3% stop governs. A trade that never reaches +1% MFE loses up to −3% live, but only −1% in the sim.

So the Saturday config (trail 1.0, stop 3.0, live arming) is **not** the config the sweep endorsed (effectively stop ~1.0 via trail-from-entry). The validation week would validate a rule the sweep never scored.

**However — the live semantics are independently supported by the live MFE data.** Replaying the 15 trades with excursion data (v1.6+) under the *live* arming rule, trail 1.0:

| Trade | MFE | Actual | With trail 1.0 |
|---|---|---|---|
| 07-23 SMCL | +3.53% | −21.36 | **+17.27** |
| 07-23 SMCX | +3.31% | −20.88 | **+15.85** |
| 07-23 SNXX | +3.21% | −21.35 | **+14.96** |
| 07-24 INTC | +0.43% | −6.84 | −6.84 |
| 07-24 TQQQ | +0.99% | −8.93 | −8.93 |
| 07-27 NOK | +2.89% | +11.84 | +12.62 |
| 07-27 SAFT | +0.10% | +0.48 | +0.48 |
| 07-28 BITO | +1.17% | +1.60 | +1.09 |
| 07-28 CAPR | +4.56% | +34.10 | +23.58 |
| 07-28 DRAM | +1.05% | −20.07 | **+0.26** |
| 07-28 FBRX | +0.07% | −0.24 | −0.24 |
| 07-28 INTC | +2.37% | −20.62 | **+9.18** |
| 07-28 NOK | +0.92% | −20.28 | −20.28 |
| 07-29 AAL | +0.07% | −7.65 | −7.65 |
| 07-29 REPL | +4.98% | +33.50 | +26.44 |
| **Total** | | **−66.70** | **+77.77** |

A +144 swing on 15 trades, giving back only ~17 on the two TP winners. Caveats: this assumes an exit exactly 1% off the peak (30s polling + slippage will do worse), and it's an upper bound when the path had an interim peak-then-dip before the final MFE. But the direction is robust — the dominant live failure mode is *winners that reached +2–3.5% and were held to a full −3% stop*, and the trail fixes exactly that.

**Recommendation:** do both of these before flipping anything on Saturday.

1. Add an `arm` dimension to the backtest trail (`from_entry` = current sim vs `past_breakeven` = current live) — a ~3-line change to `simulate_symbol_day` (seed `peak = fill` vs only trail once `t_stop > fill`). Re-run the sweep both ways.
2. Decide with eyes open. If `past_breakeven` still shows solid positive expectancy in-sim, ship live as-is (safest: fewer noise stops on thin names, and the live-MFE replay supports it). If all the sim edge lives in `from_entry`, that's really a "tighten the initial stop" result — see §3 — and shipping trail 1.0 with a 3% stop would be flying on hope.

## 3. The Aug 1–2 "exit asymmetry" session — concrete sweep grid

The existing sweep never varied `stop_pct` (all rows 3.0) or the take-profit. Both belong in the grid, because with the trail on, **TP fired 1/191 times in-sim** — the 3–5% TP is nearly vestigial, and capping the upside while the trail already caps the downside is the opposite of asymmetry.

Grid to run (shorts **on and off** — off is what validation/live will run, on is the reference):

- `stop_pct`: 1.5 / 2.0 / 2.5 / 3.0 — note stop_pct feeds sizing (risk 1% ⇒ notional = equity·1%/stop%), so tighter stops also triple position size until the notional cap binds; watch drawdown, not just net.
- `trail`: 0.75 / 1.0 / 1.25 × `arm`: from_entry / past_breakeven (§2).
- `tp`: current 3–5% scaled vs **off** (let the trail be the only upside exit — true asymmetry: uncapped winners, trailed losers).
- `hold`: off / 180. Sim says off beats 180 with the trail on (+105.96 vs +81.84, bias off), but if the trail arms `past_breakeven`, sub-1%-MFE dead trades (FBRX, SAFT, AAL) need *some* exit — keep 180 in that branch, or accept EOD flatten.
- `bias`: 0.5 vs off, under the final exit config only. The no-shorts sweep already hints bias off is better (+105.96 vs +79.05); worth confirming since with shorts disabled the bias mostly just deletes trades (29 bias-mismatch skips in the trial).
- `breakeven_trigger`: with a trail active it's largely redundant (the trail passes breakeven at MFE ≈ trail%); consider 0 to remove an interacting knob.

**Overfitting guard:** 191 sim trades and ~6 grid dimensions is a recipe for picking noise. Prefer plateaus (configs whose neighbors are also good) over the single best row; rank by expectancy *and* max drawdown; if the backtest range allows, hold out the last two weeks and check the winner survives out-of-sample. Change the live config once, not per-dimension.

## 4. Shorts-off prep (before the Aug 3 validation week)

- **Dead-slot fix:** with `allow_shorts: false`, a short-biased target can never trade (`respect_news_bias` blocks longs, shorts are off) — it just occupies one of 10 slots. Skip short-biased names at selection/re-screen time when shorts are off, freeing slots for tradeable candidates. Small change in `build_targets`/runner.
- **The awkward fact:** live shorts made +19.27 (5/11) while longs made −15.83 (9/19), and sim says long-only keeps only ~15% of the edge. Both samples are tiny, but going long-only is a real cost being paid for cash-account mechanics (no margin ⇒ no shorts; margin at this account size would trigger the PDT rule — max 3 day trades per 5 days under $25k — which kills the strategy outright, so cash is still the right call). Two honest options: accept the smaller long-only edge and validate it on its own merits, or keep shorts in the paper trial longer while going live long-only with a reduced expectation. Don't let the with-shorts sim numbers set expectations for a long-only live account.
- **Correlation beyond same-underlying:** 07-28 lost −60.97 to three simultaneous semiconductor shorts (DRAM, INTC, NOK) — same trade, three tickers. v1.7's dedupe catches leveraged twins but not sector clones. Cheap guard: cap concurrent positions in the same sector (Alpaca asset info or a small static map for common names) at 2, or cap total concurrent same-direction exposure. Worth adding before live; the 3-stop cluster days are what the −5% breaker is otherwise for.

## 5. Live-fidelity gaps (why sim numbers will shrink in production)

- **Trail granularity:** the sim trails off minute-bar highs; the live bot polls every 30s off IEX quotes and needs a `move_stop` API round-trip per ratchet. Live peaks lag sim peaks, and thin names gap through stops. Expect the live trail to capture less than the replay in §2. Later upgrade worth evaluating: Alpaca's native server-side trailing-stop orders — tick-level, no polling, survives a bot crash — at the cost of less control over the arming rule.
- **Fill optimism:** sim fills at next-bar open and assumes stop fills at the stop price; spreads aren't simulated. The trial's stop fills have been close to plan (stop-limit working), but measure it: add a slippage column to `bookkeeping.py` / the xlsx (entry_ref vs actual fill, planned stop vs actual exit). Cheap, and it turns "sim is optimistic" into a number you can subtract from sweep results.
- **Screener mismatch:** the backtest approximates the live screener universe (gaps + volume floor vs live most-actives snapshots). The sweep's *ranking* of configs is trustworthy; its absolute P&L is not.

## 6. Selection-quality ideas (hinted by live slices — test in sim first, n is tiny)

- **Very-wide opening ranges lose:** OR > 5% of price: 1/5 winners, −43.82 (SKYQ's OR was 14%!). These are the blow-off small caps where a 3% stop is inside the noise. A `max_or_pct` entry filter (or scale size down with OR%) is one line in the sim — add it to the sweep grid rather than guessing a threshold.
- **Late entries lose:** 60–180 min after open: 3/7, −39.85 (CLBK 117m, SNXX 153m, SAFT 163m). Sweep `entry_window_minutes` 60/90/120/180. The intraday re-screen's late adds (SKYQ −14.33, TQQQ −8.93, HURN no-fill) haven't earned their keep yet either.
- **News lexicon scores junk as strong signal:** HURN got sentiment +1.00 from "Ford, Garmin, GE HealthCare, Clean Harbors And Other Big Stocks Moving Higher" — a multi-ticker roundup, not HURN news. Cheap fix: ignore headlines mentioning >2–3 tickers, or require the symbol/company in the headline. This directly cleans the bias signal that gates entries. The parked LLM-scorer idea remains the real fix once the trial shows the news signal earns its keep.
- **Spread-aware selection:** HURN sat on a slot all day with a 19.8% spread that the entry-time cap (rightly) rejected. Check the spread once at selection/re-screen and drop hopeless names then, not at breakout time.

## 7. Go-live checklist additions (cash account)

- **T+1 settled funds:** in a cash account, proceeds settle next day. With up to 10 trades/day against the notional cap on a small account, the bot can easily try to reuse unsettled cash (good-faith violations). Before live: track settled vs unsettled cash in the broker layer and cap the day's aggregate buying at settled funds — or simply cap daily total notional at ~50% of equity. This never showed up on paper because paper doesn't enforce it.
- **Define validation success ex ante** (avoid grading it by feel on Aug 7): e.g. ≥12–15 completed trades, positive expectancy, exit-type mix roughly matching sim (mostly trail exits, few full stops), no repeat of a 3-correlated-stop day, drawdown < ~4% of equity. Decide *now* what result means "fund it", "extend the trial", or "back to the sweep".
- **Start size:** first live tranche at ⅓–½ of the planned size; scale only after ~30 live trades hold up. (Not financial advice — it's your capital and your call.)

## 8. Things that are fine — leave them alone

Risk 1%/trade with the notional cap; the ±6/−5% daily breaker (never hit in 18 sessions); verified flatten + overnight sweep; the 15-min opening range; confirm-bar + volume-ratio entry logic (post-v1.5 it stopped missing fast breakouts — 07-21's three entries within 46 min prove it); the watchdog/crash-resilience layer. One change-set at a time: exits on Saturday, selection filters only if the sweep clearly endorses them, everything else frozen through validation.

## 9. Priority order

1. ~~Backtest `arm` dimension + `--arm`/`--tp` sweep flags~~ ✅ **shipped 07-29 evening** (sim-only, live bot untouched; 25 + 8 tests green). Ethan runs the §3 grid on his machine Fri night/Sat, then: pick a plateau config, flip live config once on Saturday.
2. **(Sat)** Dead-slot fix for short-biased targets; decide bias 0.5 vs off from the same sweep.
3. **(Sat, small)** Slippage columns in bookkeeping; headline multi-ticker filter if trivial.
4. **(Validation week)** Watch trail-exit distribution vs sim daily; don't touch config.
5. **(Before live)** Settled-funds guard; sector-correlation cap; success criteria written down.
6. **(Later)** Server-side trailing stops; LLM sentiment; VPS; SIP feed if size ever justifies it.
