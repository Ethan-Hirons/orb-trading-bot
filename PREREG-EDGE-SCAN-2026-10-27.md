# PRE-REGISTRATION — edge scan on the cached universe (2026-10-27)

Written before `edge_scan.py` was run for the first time. Results go in a
separate section below, and nothing above it changes after that.

## Why this test, and why now

Every experiment since July has tried to fix expectancy from the **exit**
side: trail, breakeven, scale-out, VWAP exit, stop width. All of them were
judged on ≤160 real trades, where two trades decide the answer.

That approach has a ceiling, and it isn't about sample size. **If price after the
entry signal is a martingale (no drift), then no stop, trail or target can
produce positive expectancy.** This is the optional stopping theorem: exit rules
change the *shape* of the P&L distribution (win rate against payoff ratio)
but not its mean, which stays at zero minus costs. Exits only pay when there
is serial dependence after entry: momentum that a hold captures, or reversal
that a tight exit avoids.

So the first question has to be whether the entry signal has any drift. Nobody has
measured that directly. The data to do it is already on disk:
`.sweep_cache/` holds 1-minute bars for **~8,700 screened symbol-days**
(2025-07-01 → 2026-08-07), 50× the trade log.

## Signal (the bot's entry, simplified)

- OR = first 15 minutes (09:30–09:44).
- Long signal = first 1-min bar between 09:45 and 10:44 whose **close** is above
  the OR high, with bar volume ≥ 0.5 × mean OR bar volume.
- Entry = that bar's close + 0.05% (measured mean entry slippage).
- No news bias, no correlation cap, no ranking: every screened symbol-day is
  a candidate. This measures the **signal**, not the bot's portfolio.
- Symbol-days with < 8 OR bars (thin IEX coverage) or price < $5 are skipped.

## Q0 — does the signal have drift? (primary)

Mean return from entry to 15:50, net of a further 0.05% exit cost, with a
**day-clustered** bootstrap 95% CI (same-day trades are correlated through the
market, so resampling trades would understate the error).

Also reported: the drift curve at +5, +15, +30, +60 and +120 min. Where the drift
accrues tells you which exit family *can* work. Early drift that then reverses
favours a trail or time stop. Drift that keeps building favours holding.

## Conditional hypotheses — sign predicted in advance

| # | split (A vs B) | predicted | source |
|---|---|---|---|
| C1 | gap up vs gap down (long breakouts) | A > B | gap continuation; XP 10-27 was long on a −48% gap |
| C2 | first 5-min candle green vs red | A > B | Zarattini, Barbon & Aziz (2024) direction rule |
| C3 | entry above session VWAP vs below | A > B | Zarattini & Barbon VWAP intraday momentum |
| C4 | OR width / ATR14: bottom vs top tercile | A > B | Crabel (1990) narrow-range expansion |
| C5 | breakout at ≤ 10:00 vs > 10:15 | A > B | paper's early-entry design, E4 notwithstanding |
| C6 | daily rel-volume proxy: top vs bottom tercile | A > B | Zarattini et al. relvol gradient |
| C7 | breakout-bar volume ratio: top vs bottom tercile | A > B | confirmation-volume rationale |

Metric for every split: mean EOD return difference (A − B).

**PASS requires all three:**
1. predicted sign in **discovery** (2025-07-01 → 2025-12-31),
2. predicted sign in **holdout** (2026-01-02 → 2026-08-07),
3. holdout day-clustered 95% CI of (A − B) excludes zero.

Seven tests: at a one-sided 2.5% error rate, ~0.2 false passes are expected by
chance. A single pass is therefore worth a paper experiment and nothing more.

## Exit comparison (secondary, descriptive)

On the same signals: `stop 1.5 + trail 1.25` (live), `stop 1.5, hold to 15:50`,
and `stop = 1.0×ATR14%, hold`. **Only rules at or wider than the live stop**,
because ANALYSIS-2026-10-25 §4 showed 1-min bars flatter anything tighter.
Descriptive only. This is not a gate.

## What each outcome authorises

- Q0 drift ≈ 0 (CI spans 0): stop tuning exits. The remaining lever is
  selection, and only a passing C-split can say which selection.
- Q0 drift > 0: exits are worth studying, and the drift curve says which family.
- Q0 drift < 0 (CI below 0): the bot's entry signal loses money on this universe
  before any exit rule is applied. Report it plainly.
- Any C-split PASS: write a paper-trial PREREG with a 50-trade gate, as for E12.
- **No config change follows from this document directly.**

## Known limitations, stated in advance

- Feed is **IEX** (~4% of the tape). OR highs and bar volumes are partial.
  That biases levels, but both arms of every split share the bias.
- The universe is the sweep screener's gap-screened candidates, not the live
  screener's. It overlaps with the live universe but isn't identical.
- No spread model beyond the flat 0.10% round trip. Wide-spread names are
  flattered.

---

## ADDENDUM A — written after Q0 was read, BEFORE any downside-break data was computed

Q0 came back with **negative** drift on the long breakout (all signals, EOD
−0.206%, r5 and r30 CIs below zero). Negative drift after an upside break means
the move tends to revert, which predicts the mirror image: a **long entry on the
first 1-min close below the OR low** (same window, same volume rule, same costs)
should have **positive** drift. It is the only form of fading the bot can trade,
because the account is cash and shorting is off.

Supporting literature, cited before the result was seen: overnight-gap reversal
(Berkman, Koch, Tuttle & Zhang 2012) and the cross-sectional end-of-day reversal
in individual US stocks (Notre Dame working paper, 2025).

**H-R PASS requires all three:** mean EOD return > 0 in discovery, > 0 in holdout,
and the holdout day-clustered 95% CI excluding zero. That is one test with one
config: no window, threshold or stop variants get scored. The `stop 1.5 hold` and
`live` exit columns are reported as descriptive only.

The data for this test is untouched. The downside-break rows did not exist in
`edge_signals.csv` when this was written.

---

# RESULT (2026-10-27)

Everything above this line is unchanged since it was written. Full output: `python -u edge_scan.py report`
and `python -u edge_scan.py reversal` (both re-run from `edge_signals.csv` in seconds).

**7,831 signals over 278 sessions**: 3,871 upside breaks (the bot's entry) and 3,960
downside breaks. Discovery is 2025-07-01 → 2025-12-31, holdout 2026-01-02 → 2026-08-07.

## Q0: the entry signal has no positive drift. Its point estimate is negative.

| horizon | all (n=3,871) | discovery | holdout |
|---|---|---|---|
| +5 min | **−0.049** [−0.088, −0.006] | −0.027 | −0.061 [−0.109, −0.011] |
| +30 min | **−0.117** [−0.217, −0.012] | −0.274 | −0.027 |
| to 15:50 | −0.206 [−0.492, +0.077] | −0.434 | −0.074 [−0.440, +0.275] |

% per trade, net of 0.10% round trip, with day-clustered 95% CIs. The first five minutes after a
confirmed break are reliably negative. After that the drift is flat to negative.

## Exits: every rule lands within noise of zero, as optional stopping predicts

| rule | all | discovery | holdout |
|---|---|---|---|
| stop 1.5 + trail 1.25 (live) | −0.091 [−0.177, +0.000] | −0.118 | −0.076 |
| stop 1.5, hold | −0.054 [−0.198, +0.104] | −0.194 | +0.027 |
| stop 2.5, hold | −0.084 | −0.242 | +0.007 |
| stop max(1.5, ATR), hold | −0.168 | −0.362 | −0.056 |
| no stop, hold | −0.206 | −0.434 | −0.074 |

**Gross of the 0.10% cost, the live exit is +0.009% per trade: zero.** At the $700 cap that is
−$0.64 per trade net, against the paper book's real −$0.22 per trade. The two agree within
noise, so the sim is a fair proxy for the bot.

The live rule has the **narrowest CI** of any row. The trail does not create edge, but it does
cut variance, and that is a real reason to keep it, as ANALYSIS-2026-10-25 §7 concluded.

## H-R (buying the downside break): FAIL

| | discovery | holdout |
|---|---|---|
| EOD | −0.130 | −0.160 [−0.501, +0.198] |
| live exit | −0.042 | −0.091 |

Fading the move loses about the same as following it. **Both sides of the opening range lose
roughly the round-trip cost.** That is what a martingale looks like after costs.

## Conditional splits: 0 of 7 pass

| split | discovery A−B | holdout A−B [95% CI] | |
|---|---|---|---|
| C1 gap up vs down | −0.406 | +0.133 [−0.576, +0.868] | fail, sign flips |
| C2 first 5m green vs red | +0.445 | −0.136 [−0.594, +0.347] | fail, sign flips |
| C3 above vs below VWAP | n/a | n/a | **untestable**: a close above the OR high is always above VWAP |
| C4 OR/ATR narrow vs wide | −0.063 | +0.548 [−0.017, +1.140] | fail, discovery sign wrong |
| C5 early vs late break | +0.544 | +0.391 [−0.191, +0.923] | fail, CI spans 0 (closest call) |
| C6 relvol proxy top vs bottom | −0.411 | −0.310 | fail, **opposite** of prediction both times |
| C7 breakout-bar volume top vs bottom | −0.461 | +0.118 | fail, sign flips |

C6 is the notable one. The daily relative-volume proxy points the **wrong way** in both
periods. That fits RESEARCH-2026-08-14 §4.1: the proxy is not the paper's measure, and the
paper's measure can't be built from IEX or from this cache.

## What this establishes

1. **On this universe, with 1-min IEX bars, the ORB entry has no detectable edge in either
   direction.** The CI on the live rule is about ±0.09% per trade, so any real edge is
   smaller than the cost of a round trip.
2. **Exit tuning is finished as a research direction.** With no drift there's nothing for an
   exit to capture. E5, E6, E10 and E12 all failed or stalled for this reason, not because
   their samples were small. E12's paper trial can still run, since it costs nothing, but its
   prior is now low.
3. **The selection features available in the bot today (gap sign, first candle, OR width,
   timing, daily relvol, breakout volume) do not separate winners from losers out of
   sample.** That matches the 2026-09-30 table of seven dead selection claims, now on 25×
   the data.
4. The one input the source paper says carries the edge has never been measured here:
   **opening 5-minute relative volume on the consolidated tape**, ranked across the whole
   market. Every test in this project has used either IEX or a daily proxy. That is the
   only untested, literature-backed lever left. See `ANALYSIS-2026-10-27.md` §3.

**No config change. Nothing deployed.**

---

## ADDENDUM B, H-V: SIP opening relative volume. Written BEFORE any SIP volume was fetched.

The source paper's selection variable is the **opening 5-minute volume divided by the
average of the same 5 minutes over the prior 14 sessions, on the consolidated tape**. No
test in this project has ever used it. Every relvol number so far came from IEX (~4% of
volume) or from the daily proxy, and C6 above shows the proxy points the wrong way.
Historical SIP bars are available on the free plan.

**Test:** take the same 3,871 upside-break signals and compute SIP relvol from 09:30–09:34
bars. Split each period into terciles.

**PASS (all three):** top-tercile minus bottom-tercile mean EOD return > 0 in discovery,
> 0 in holdout, and the holdout day-clustered 95% CI excluding zero.

Descriptive only, not scored: the paper's absolute cut (relvol ≥ 1.0 vs < 1.0) and the
live-exit column.

If it passes, it earns a paper-trial PREREG and nothing more. Live use would need real-time
SIP (a paid feed) or proof that IEX relvol ranks the same names.

## RESULT, H-V (SIP opening relvol): FAIL, and it points the wrong way

SIP 09:30–09:34 volume over the prior 14 sessions, for 3,843 of the 3,871 upside signals
(`python -u relvol_sip.py report`).

| | discovery | holdout |
|---|---|---|
| top − bottom tercile, EOD | −0.408 [−1.088, +0.229] | −0.307 [−0.906, +0.266] |
| relvol ≥ 1 vs < 1, EOD (descriptive) | −0.261 | −0.086 |
| top − bottom tercile, live exit | +0.036 | −0.044 |

Gradient over all periods (EOD, % per trade):

| SIP relvol | n | EOD | live exit |
|---|---|---|---|
| < 1 | 1,348 | −0.100 | −0.100 |
| 1–2 | 1,502 | −0.071 | −0.083 |
| 2–4 | 662 | −0.446 | −0.167 |
| 4–8 | 242 | **−0.784** [−1.573, −0.067] | +0.016 |
| ≥ 8 | 89 | −0.643 | −0.016 |

**Inside a gap-screened universe, high opening relative volume marks exhaustion, not
continuation.** The busiest breakouts give back the most by the close. That doesn't
necessarily contradict the paper. The paper ranks across *all* ~7,000 stocks and trades
both sides with an ATR stop, and here every name has already gapped ≥ 2%, so the
selection the paper's filter does has already happened. Within that set, extra volume adds
nothing on the long side.

Under the live exit the gradient vanishes (+0.016 at 4–8×), because the trail cuts these
trades before the fade. So there's no actionable filter here either.

This direction was **not** pre-registered: the prediction was A > B. Don't promote "skip
high-relvol breakouts" off this table. It is a hypothesis for a future PREREG, not a
finding.

---

## ADDENDUM C, H-G: gap-down reversal. Written BEFORE any gap-entry data was computed.

A different signal family, not an ORB variant. It is the first idea to go through the
drift-first gate. Literature: large overnight gaps partly reverse intraday, more strongly in
harder-to-arbitrage names (Berkman, Koch, Tuttle & Zhang 2012). The long-only form is to
**buy the gap-down**.

**Signal:** every cached candidate with **gap ≤ −5%** (open vs previous close), price ≥ $5,
and ≥ 8 bars in the first 15 minutes. **Entry** at the close of the 09:30 bar + 0.05%.
**Exit** at the 15:50 bar open − 0.05%. No stop, so this measures drift.

**PASS (all three):** mean net return > 0 in discovery, > 0 in holdout, and the holdout
day-clustered 95% CI excluding zero.

Descriptive only, not scored: the same rule with entry at 09:45; gap ≤ −10%; and the mirror
(buying gap-UPs ≥ +5%), which the reversal story predicts is negative.

## RESULT, H-G (buy the gap-down): FAIL

`python -u edge_scan.py extract_gap && python -u edge_scan.py gap`, 2,529 candidate-days with |gap| ≥ 5%.

| rule | discovery | holdout |
|---|---|---|
| **gap ≤ −5%, buy 09:30 (scored)** | +0.148 [−0.902, +1.219] | **−0.323** [−1.182, +0.585] |
| gap ≤ −5%, buy 09:45 | −0.250 | −0.535 |
| gap ≤ −10%, buy 09:30 | −0.581 | −1.286 [−2.896, +0.294] |
| gap ≥ +5%, buy 09:30 (mirror) | −1.266 [−2.477, −0.111] | −0.077 |

The sign flips between periods, so it fails. The deepest gap-downs keep falling (−1.29% in
the holdout), which is **continuation, not reversal**. The gap-up mirror was strongly
negative in 2025 H2 and flat in 2026, so the regimes differ and nothing is stable. The
Berkman reversal does not show up as a tradeable long-only edge on this universe, net of a
0.10% round trip.

**Tally for the day: Q0, H-R, C1–C7, H-V and H-G. Eleven pre-registered tests, zero passes.**
