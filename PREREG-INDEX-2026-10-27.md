# PREREG: index ETFs, four signal families (drift-first gate), 2026-10-27

**Written before any data for these tests was downloaded.** Results are appended below the
line at the end, unedited above it. Rules come from the published papers, not from our data.

## Why

E14 closed the gap-screened small-cap universe: the opening-range break has no drift there in
either direction. The two ways forward named in ANALYSIS-2026-10-27 §4 are a different
**universe** or a different **signal family**. This tests both at once: the most liquid US
index ETFs, with the three intraday/overnight effects that have the strongest published
evidence, plus the oldest calendar effect.

Track B (QQQ first-candle ORB, TRACK-B-GONOGO.md) is **not** retested. It's closed.

## Data

- Alpaca **SIP** 1-minute bars, `adjustment=all` (split and dividend adjusted), regular hours
  09:30–15:59 ET. **SPY, QQQ, IWM, DIA.**
- Sessions 2016-01-04 → 2026-10-27, everything Alpaca serves.
- **Discovery:** 2016-01-04 → 2021-12-31. **Holdout:** 2022-01-03 → 2026-10-27.
- Early-close sessions (last bar before 15:59) are excluded from H1 and H3.

*Data clarification, added after the download and `check`, before any test ran:* Alpaca
returns after-hours bars past 13:00 on half-days, so a clock-time filter labelled all 21
half-days in the sample as full sessions with an after-hours "close". Half-days now come
from the NYSE early-close calendar (`index_scan.EARLY_CLOSES`), and their bars are cut at
13:00. A session that ends before 15:55 with no half-day to explain it is a feed gap
(SPY: one day, 2019-08-12, which stops at 15:31). No return that uses such a session's close
is computed: it's dropped from H1–H4 rather than given a wrong close.

Conventions:
- `P(HH:MM)` = close of the 1-min bar labelled HH:MM−1, the price at HH:MM:00.
- `Open(t)` = open of the 09:30 bar. `Close(t)` = close of the last regular-hours bar
  (15:59 on a full day). This is a proxy for the closing auction.
- **Cost: 0.5 bp per side (0.005%)**, scored. SPY's spread is one cent on $200–$600 (≈0.2–0.5 bp),
  and the H3 paper measured ≈0.02 bp of slippage live. 1 bp per side is reported as a
  sensitivity check and is not scored. A pass that dies at 1 bp is fragile, and the write-up will say so.

**Scored on SPY only.** QQQ, IWM and DIA run with identical rules as descriptive replication.
Four tests on four symbols would be sixteen tests.

Statistics: one return per session. 95% CI from 2,000 bootstrap resamples of sessions.

## H1: market intraday momentum (last half hour)

Gao, Han, Li & Zhou (JFE 2018); Baltussen, Da, Lammers & Martens (JFE 2021): the return from
the previous close to 15:30 predicts the 15:30→close return, across 60+ futures markets. The
proposed mechanism is gamma hedging by option dealers and leveraged ETFs.

- `r = P(15:30) / Close(t−1) − 1`. Long if r > 0, short if r < 0, entered at P(15:30).
- Exit at Close(t). Net = sign(r) × (Close(t)/P(15:30) − 1) − 2c.

**PASS (all three):** mean net > 0 in discovery; > 0 in holdout; holdout 95% CI lower bound > 0.

Descriptive: long leg vs short leg; top tercile of |r| (cut from discovery); Gao's
first-half-hour predictor (Close(t−1) → P(10:00)); by year; 1 bp.

Prior: the whole holdout is post-publication for both papers. Expected size is a few bp per
day. The last half hour's SD is ≈25–30 bp, so the holdout SE is ≈0.8 bp and a pass needs
≳1.7 bp net. **My prior that it passes: ~25%.**

## H2: overnight drift

Cooper, Cliff & Gulen (2008); Kelly & Clark (2011); Lou, Polk & Skouras (2019): close-to-open
returns have historically carried nearly all of the index's gain.

- Long at Close(t), exit at Open(t+1). Net = Open(t+1)/Close(t) − 1 − 2c. Every
  consecutive session pair, including weekends and holidays.

**PASS (all three):** mean net > 0 in discovery; > 0 in holdout; holdout 95% CI lower bound > 0.

Descriptive: the intraday complement (Open→Close); by year; Friday→Monday vs the rest; 1 bp.

Prior: documented for decades, with one report of weakening in the most recent subperiod.
The overnight SD is ≈70 bp, so the holdout SE is ≈2 bp and a pass needs ≳4 bp net.
**Prior ~30%.**

## H3: noise-area intraday momentum

Zarattini, Aziz & Barbon, *Beat the Market: An Effective Intraday Momentum Strategy for
S&P500 ETF (SPY)* (SSRN, first version 2024-05-10). This is the paper's **base model**,
exactly as written (§3):

- `σ(HH:MM)` = mean over the previous 14 complete sessions of `|P_{t−i}(HH:MM)/Open(t−i) − 1|`.
- `UB = max(Open(t), Close(t−1)) × (1 + σ)`, `LB = min(Open(t), Close(t−1)) × (1 − σ)`.
- Decisions only at 10:00, 10:30, …, 15:30. Above UB: go long, or flip from short.
  Below LB: go short, or flip from long. Everything closes at Close(t).
- Unlevered, 100% notional. c is charged per side: an entry is 1 side, a flip is 2, the close is 1.
- Session return = sum of signed segment returns − costs. **Scored on active sessions**
  (at least one position).

**PASS (all four):** mean > 0 in discovery; > 0 in holdout; holdout 95% CI lower bound > 0;
and mean > 0 in the **post-publication** window 2024-05-10 → end (sign only).
The fourth condition is there because 2022 → April 2024 is inside the paper's own sample.

Descriptive: the paper's VWAP trailing stop (long stop = max(UB, VWAP), short stop =
min(LB, VWAP), checked at the same half-hour marks); long vs short leg; by year; 1 bp;
mean per trading day including flat days.

*Clarification added before the data run:* in the VWAP variant, **entries** use the same
VWAP-adjusted levels (long needs P > max(UB, VWAP)), which the paper names as its long-side
reference. Otherwise a stop and an immediate re-entry would fire at the same checkpoint.
The scored base model is unaffected.

Prior: the paper's base model makes 6.2%/yr unlevered 2007–2024 (Sharpe 0.61), but its own
table has 2016 and 2017 negative, both inside our discovery period. **Prior ~30%.**

## H4: turn of the month

Ariel (1987); Lakonishok & Smidt (1988); McConnell & Xu (2008): returns concentrate in the
last trading day of the month and the first three of the next.

- TOM sessions are trading days −1, +1, +2, +3, where −1 is the month's last session.
  Daily return = Close(t)/Close(t−1) − 1.
- Metric: mean TOM-day return − mean return on all other days.

**PASS (all three):** difference > 0 in discovery; > 0 in holdout; holdout 95% CI lower
bound > 0 (sessions resampled within each group).

Descriptive: the TOM window as one trade (buy Close of day −2, sell Close of day +3, net 2c).

Prior: low power. There are ≈230 TOM days in the holdout, so the SE of the difference is
≈8 bp/day and a pass needs ≳16 bp/day. **Prior ~15%.**

## What a result earns

- **Pass:** a paper-trial PREREG with its own gate, as for E12. Never straight to live.
- **Fail:** the family is closed on this universe. No re-cut, no new thresholds, and the
  holdout is not rerun with different parameters.
- Four scored tests, each needing the right sign in discovery and a one-sided 2.5% holdout
  test. Under the null, P(at least one false pass) ≈ 5%.

## Run discipline

Download, validate bar counts, then run H1–H4 **once each** with the rules above. Discovery
and holdout print together, and nothing is tuned between them. Anything not specified here
is labelled descriptive in the results.

---

## Results (run once, 2026-10-27; full output in `index_scan_out.txt`)

**Scored tests: 0 of 4 pass.** SPY, net bp per session, 95% CI:

| | discovery 2016–21 | holdout 2022–26 | verdict |
|---|---|---|---|
| H1 last-half-hour momentum | +0.17 [−1.52, +1.87] | **−1.39** [−2.80, +0.12] | **FAIL**: wrong sign in holdout |
| H2 overnight drift | +3.73 [−0.19, +7.75] | +1.94 [−1.67, +5.68] | **FAIL**: CI spans 0 |
| H3 noise-area momentum | +1.54 [−2.97, +5.82] | +3.54 [−1.81, +9.05]; post-pub +1.23 | **FAIL**: CI spans 0 |
| H4 turn of month (diff/day) | +1.40 [−12.3, +15.0] | +1.51 [−13.5, +16.9] | **FAIL**: CI spans 0 |

- **H1 is gone.** The holdout is negative on all four ETFs and significantly negative at 1 bp.
  After publication, the last-half-hour effect hasn't just weakened on SPY. It's gone.
- **H2 is positive everywhere and significant nowhere in the holdout.** Overnight was positive
  in both periods on all four ETFs, but so was the intraday complement (+1.2 bp on SPY). Over
  2016–2026 this is market beta split across two sessions, not an anomaly.
- **H4 has no power.** The point estimates are tiny compared with ±15 bp CIs.
- **H3 on SPY:** right sign in all three windows, never significant. It's also concentrated:
  without its 10 best holdout sessions the mean is −0.13 bp.

## Descriptive replication and post-hoc diagnostics (NOT scored)

H3 split sharply by ETF, which the pre-registration did not predict:

| H3, net bp / active session | discovery | holdout | post-pub (2024-05-10+) | unlevered Sharpe (disc / hold) |
|---|---|---|---|---|
| **QQQ** | +4.88 [−0.70, +10.44] | **+11.79 [+5.18, +18.81]** | +8.71 [−1.09, +19.11] | 0.73 / **1.53** |
| SPY | +1.54 | +3.54 | +1.23 | 0.27 / 0.58 |
| DIA | +0.79 | −2.61 | −1.65 | 0.14 / −0.47 |
| IWM | +2.00 | **−8.82 [−15.56, −2.28]** | −8.52 | 0.27 / −1.20 |

The QQQ diagnostics (`index_scan_posthoc.txt`) were chosen **after** seeing the table above:

- **Years:** positive in 10 of 11 (2016 −2.5). Every year 2017–2026 is positive, and 2023–2026
  runs +6.5 to +10.1.
- **Legs:** both make money in the holdout, long +7.5 [+2.7, +13.0] and short +5.3
  [+0.6, +10.2]. The short leg rules out "QQQ went up". The long leg is also significant in
  discovery.
- **VWAP-stop variant (the paper's refinement):** +6.55 [+2.17, +10.67] in discovery and
  +8.03 [+2.91, +13.88] in holdout. It would clear this PREREG's gate in both periods.
- **Costs:** at 2 bp per side, 4× the scored cost, the holdout is still +8.72 [+2.09, +15.75].
- **Concentration:** without its best 10 holdout sessions the mean is still +7.18 bp. The
  best 10 are 40% of the total, against 104% for SPY.
- **Long-only (a cash account can't short):** +5.79 [−0.37, +11.91] in discovery, +13.68
  [+3.62, +24.59] in holdout.

**How much weight it bears.** This is the strongest result the project has produced, and it
is **not a pass**. QQQ was picked after looking. Its holdout t ≈ 3.4 (p ≈ 0.0007) would
survive a Bonferroni correction across all 16 family × ETF pairs (p ≈ 0.01), but 2022 → mid-2024
overlaps what the paper's authors could see (their FAQ charts ETFs to Aug 2024). The clean
post-publication window is positive and not yet significant (n = 349). A plausible mechanism
exists: Nasdaq-100 carries the largest leveraged-ETF complex (TQQQ/SQQQ), whose end-of-day
rebalancing trades with the day's move. H1 on QQQ failed, though, so that mechanism isn't
confirmed here. The IWM reversal matches E14: on small caps, breakouts fade.

**The only uncontaminated test is forward data.** At the holdout's Sharpe of ~1.5, telling
the edge apart from zero at t = 2 takes about **1.8 years** of daily results. A few weeks of
paper trading can check mechanics and fills, not the edge. Any paper trial needs its own
PREREG, with these rules frozen exactly as run here.
