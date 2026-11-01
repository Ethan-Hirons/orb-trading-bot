# ANALYSIS 2026-11-01: does E16's mechanism hold on ETFs it has never seen? (E17)

## TL;DR

- **Verdict: NOT SUPPORTED**, under the rule written before the data. It missed narrowly.
- **What was tested:** E16's frozen QQQ rule, run unchanged on six ETFs it had never been run
  on. Three have large leveraged-ETF complexes (SMH, XLK, XLF) and three small ones (XLV, XLU,
  XLP).
- **Two of three criteria passed:**
  - **(b)** the high-flow group beat the low-flow group by **+8.3 bp** [+1.8, +14.7].
  - **(c)** flow size and result rank together (**+0.64** across all ten ETFs).
- **The deciding criterion failed.** **(a)** the high-flow group on its own wasn't clearly
  profitable: **+4.7 bp, one-sided p = 0.056** against a 0.05 line.
- **One ETF decided it:**
  - **SMH (+12.5 bp) and XLK (+10.5 bp) work,** each clearly above zero in the holdout.
  - **XLF loses (−7.6 bp, significantly).** That is the opposite of what the mechanism
    predicts for a fund with a large leveraged complex.
- **After the fact:** the pattern reads more like **"works on large-cap tech and
  semiconductors"** than "works where leveraged flow is big".
  - XLK's day-by-day results move 0.87 with QQQ's, so it is nearly the same bet.
  - The low-flow controls all lose.
- **For E16, as fixed in advance:** no change to its rules or schedule. E16 runs to its own
  gate. The prior that it passes drops, and no ETFs are added on this mechanism.

## 1. What was tested

PREREG-MECHANISM-2026-11-01.md was committed as `4444b1d` **before any price data for these six
ETFs was fetched**. The git history shows the order: PREREG commit, then the data, then one
run.

- **The rule:** `index_scan.h3_rows` exactly as frozen for E16. That is σ over 14 full
  sessions, bands from max/min(open, previous close), checkpoints 10:00–15:30, enter or flip on
  a breach, flat at the close, and no VWAP stop. There was no per-ETF tuning.
- **The predictor:** R = Σ AUM × (L² − L) ÷ the ETF's average daily dollar volume, from
  issuer-level AUM and Yahoo volume (sources in the PREREG).
- **Data:** Alpaca SIP 1-min bars for 2016-01-04 → 2026-10-30. Each new ETF has 2,723 sessions
  on exactly SPY's calendar, with 21 half-days and the same single feed gap (2019-08-12).
- **Holdout:** 2022-01-03 → 2026-10-30. Full output is in `mechanism_out.txt`.

## 2. Per-ETF results (net bp per active session, 95% CI)

| ETF | R | role | discovery 2016–21 | **holdout 2022–26** | post-pub 2024-05-10+ | holdout at 2 bp |
|---|---|---|---|---|---|---|
| SMH | 50.8 | treatment | +7.32 [+0.24, +14.13] | **+12.46 [+3.30, +21.77]** | +12.77 [−1.54, +26.84] | +9.41 |
| XLK | 28.6 | treatment | +5.20 [−0.42, +10.90] | **+10.47 [+2.35, +18.49]** | +7.13 [−4.71, +20.27] | +7.39 |
| QQQ | 11.3 | E15, seen | +4.88 [−0.70, +10.44] | **+11.84 [+5.26, +18.41]** | +8.83 [−0.44, +19.02] | +8.78 |
| XLF | 8.53 | treatment | −0.15 [−5.35, +5.11] | **−7.59 [−13.30, −2.15]** | −5.89 [−12.95, +0.95] | −10.67 |
| DIA | 4.92 | E15, seen | +0.79 [−4.08, +5.39] | −2.55 [−7.52, +2.41] | −1.53 | −5.70 |
| SPY | 3.41 | E15, seen | +1.54 [−2.97, +5.82] | +3.57 [−1.62, +9.01] | +1.31 | +0.43 |
| IWM | 2.38 | E15, seen | +2.00 [−3.90, +8.28] | **−8.77 [−15.49, −2.30]** | −8.40 | −11.90 |
| XLV | 0.90 | control | +1.64 [−2.75, +6.01] | **−4.79 [−9.44, −0.29]** | −2.22 | −7.91 |
| XLU | 0.40 | control | +0.43 [−4.81, +5.76] | −3.35 [−8.82, +2.17] | −4.48 | −6.43 |
| XLP | 0.03 | control | +0.58 [−3.41, +4.54] | −2.64 [−6.94, +1.77] | −0.63 | −5.72 |

QQQ, SPY, DIA and IWM match E15. Their holdout runs three sessions longer (to 10-30), so the
numbers move by at most 0.1 bp.

**Year by year** (holdout years 2022–2026):

| ETF | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SMH | +9.4 | +4.4 | +9.2 | −0.0 | −2.0 | +22.7 | +20.0 | +6.7 | +12.3 | +7.7 | +15.2 |
| XLK | +0.3 | +1.5 | +16.5 | −2.5 | +3.8 | +10.5 | +15.8 | +11.5 | +14.3 | +5.0 | +3.0 |
| XLF | −2.0 | −2.9 | +1.7 | −11.5 | +26.1 | −10.2 | −18.3 | −0.1 | −2.8 | −6.9 | −9.6 |
| XLV | +0.5 | −0.3 | +7.2 | −3.3 | +12.6 | −7.0 | −7.3 | −6.2 | −7.4 | +9.1 | −12.4 |
| XLU | +3.1 | +2.9 | −12.0 | −2.1 | +11.8 | +1.0 | +1.1 | −6.7 | +3.8 | −12.2 | −2.6 |
| XLP | +2.7 | −4.3 | +7.1 | +1.4 | −4.2 | +1.0 | −4.0 | −6.8 | −5.0 | +1.7 | +2.4 |

SMH was positive in 9 of 11 years and XLK in 10 of 11. XLF was negative in 9 of 11.

## 3. The pre-registered criteria (holdout, 0.5 bp)

| criterion | result | |
|---|---|---|
| (a) pooled treatment > 0, one-sided day-clustered bootstrap p < 0.05 | +4.72 bp [−1.24, +10.77], **p = 0.0561** (n = 2,154 sessions) | **FAIL** |
| (b) treatment − control > 0 | **+8.31 bp** [+1.84, +14.68] (pooled control −3.59 [−6.90, −0.19]) | PASS |
| (c) Spearman(R, holdout mean), 10 ETFs | **+0.636** | PASS |
| **verdict** | (a) failed, so the rule says **NOT SUPPORTED** | |

**What it means for E16, as written in advance:**
- The rule didn't hold up on the unseen high-flow ETFs taken as a group, so the QQQ lead looks
  more like a selection effect than a mechanism.
- E16 runs to its own gate, with no change to its rules or schedule.
- The prior that it passes is lower.
- No ETFs are added on this mechanism.

I'm not softening that. p = 0.056 is a miss under a rule written before the data, and two
passing criteria out of three don't change the deciding one.

## 4. Post-hoc observations (chosen after seeing §2–3; not scored)

From `mechanism_posthoc.txt`:

1. **The split is tech and semis versus everything else, not big flow versus small.**
   - SMH and XLK work. XLF, whose leveraged complex is the same order of size relative to its
     volume (R 8.5 against QQQ's 11.3), loses significantly.
   - A pure leveraged-flow mechanism predicts XLF should work. It doesn't, and that's the
     specific evidence against the mechanism.
2. **XLK isn't independent of QQQ.** Their same-day session returns correlate at **+0.87**, and
   SMH's at +0.67. XLK's "out-of-sample" success is mostly QQQ's success again.
   - Among the six unseen ETFs alone, the rank correlation is +0.43.
   - The three ETFs where the rule works are, in effect, one large-cap growth/semiconductor bet.
3. **The rule loses on every low-flow ETF,** gross of costs too: XLV −3.8, XLU −2.3 and XLP −1.6
   bp at 0 bp cost. Together with IWM (and E14's small caps), intraday breakouts reverse more
   often than they continue in most of the market. Only large-cap tech and semis show
   continuation.
4. **Possible explanations I can't separate with this data:**
   - **Leveraged flow:** SOXL/TQQQ/TECL are the three biggest 3x complexes. Its weak point is
     that XLF/FAS has a comparable complex and shows no continuation.
   - **Retail and options attention concentrated in mega-cap tech and semis:** 0DTE and
     single-stock leveraged ETFs on NVDA and others aren't in R.
   - **Plain sector-trend persistence** in 2022–2026, a period dominated by an AI/semiconductor
     cycle.

## 5. What would test this properly (not done; needs its own PREREG)

The clean evidence is **forward data**, as for E16.
- The nightly shadow could replay the frozen rule on SMH and XLK alongside QQQ, at no cost
  (data only, no orders).
- That would be a new pre-registration stating, before any forward data, whether "tech/semis
  basket" or "leveraged flow" is the hypothesis. That choice is now post-hoc, and the
  pre-registration should say so.
- **It must not be used to rescue E17:** E17's verdict stands.

## 6. Verdict

**NOT SUPPORTED.** E16's leveraged-ETF mechanism didn't predict, on unseen ETFs, where the rule
makes money:
- It worked on two of three high-flow ETFs (SMH and XLK, the second nearly a copy of QQQ).
- It failed on the third (XLF).
- It lost on all three low-flow controls.

E16 continues unchanged to its own gate. Its promotion-gate criterion 5 (a mechanism) now has
evidence *against* the leveraged-flow story specifically. Any future E16 PASS would need a
different mechanism argument.

## What I'm unsure about

- **R is a single 2026 snapshot.** XLF's leveraged complex may have been relatively smaller
  during much of 2022–2026, and SOXL's much smaller in 2022. Historical AUM wasn't available
  for free. If XLF's complex was negligible for most of the holdout, XLF is a weaker
  counterexample than it looks. I can't check that here.
- **The denominator** is the ETF's own volume, as the task specified. Measured against the
  constituents' combined volume, SMH and XLK would rank lower relative to QQQ and SPY. The
  verdict turned on (a), which doesn't depend on R, so this doesn't change the outcome.
- **XLF's real costs** are above 0.5 bp per side (a $57 ETF with a 1-cent spread), so its true
  holdout is worse, not better. It doesn't affect (a)'s direction.
- **The bootstrap p of 0.056** comes from 10,000 resamples with seed 7, and its Monte Carlo
  error is about ±0.002. A different seed would not move it below 0.05 by itself. I did not
  rerun with other seeds: that would be fishing.
- **The four E15 ETFs were already seen,** so criterion (c) includes known values. The clean
  part, the six unseen ETFs, ranks only +0.43.
