# ANALYSIS 2026-11-01: monthly trend-following across asset-class ETFs (E18)

## TL;DR

- **Verdict: both variants FAIL** the pre-registered bar, so neither gets an implementation
  section.
- **The drawdown goal was met easily.** Over 2008–2026, trend-following cut the worst fall from
  **−37.5%** (equal-weight buy-and-hold of the 8 ETFs) to **−11.7%** (T1, Faber's 10-month
  average) and **−10.6%** (T2, 12-month momentum).
- **The return goal wasn't.**
  - **T1 failed outright:** it gave up 1.67 percentage points of CAGR a year against a 1.5 pp
    limit, and failed in both halves.
  - **T2 passed the full period by 0.03 pp** (4.57% vs a 4.54% floor). It then failed the
    2017–2026 half on CAGR (5.52% vs 6.33%) and Sharpe (0.47 vs 0.54).
- **Costs barely matter:** at 20 bp per side instead of 5, CAGR drops by about 0.4–0.6 pp.
- **Under the pre-registration, trend-following on this universe is closed.** There'll be no
  re-run with other lookbacks, weights or universes. That would be a new idea with a new
  PREREG, not a rescue.

## 1. What was tested

PREREG-TREND-2026-11-01.md, together with `trend.py` and its tests, was committed as `a4b0fd6`
**before any price data was fetched** (`.trend_cache/` didn't exist yet). The data was fetched
once and the backtest run once.

- **Universe:** SPY, EFA, EEM, IEF, TLT, GLD, DBC, VNQ, each a 1/8 slot. Cash is BIL, with
  ^IRX before BIL existed.
- **T1 (Faber 2007):** hold the ETF if its month-end close is above the 10-month SMA, else cash.
- **T2 (Moskowitz, Ooi & Pedersen 2012, long-only):** hold the ETF if its 12-month total return
  beats cash's, else cash.
- **Execution:** at the close of the day after month-end. 5 bp per side is scored and 20 bp is
  the stress run.
- **Scored period:** 2008-01-02 → 2026-10-30. The halves split at 2017-06-01.
- **Benchmarks:** equal-weight (EW), the scored comparison, and 60/40 SPY/IEF, reported only.
  Both are rebalanced monthly on the same schedule and at the same cost.

## 2. Data

**Source:** Yahoo Finance's daily chart API (the endpoint yfinance wraps), downloaded
**2026-11-01 21:23:30 UTC**. The series is `adjclose`, adjusted for splits and dividends.
Stooq, the fallback, wasn't needed. The full check output is in `trend_check.txt`.

| check | result |
|---|---|
| coverage | every ETF covers 2006-02 → 2026-10-30. DBC is the youngest (first price 2006-02-06), so no substitutions were needed. |
| splits | EFA 3:1 (2005), EEM 3:1 (2005, 2008), BIL 1:2 reverse (2017). **None shows as a jump** in `adjclose` (or in Yahoo's raw close, which is already split-adjusted). |
| daily moves > 15% | 9 in all, every one a real market day: EFA and EEM on 2008-10-13/15/28; VNQ on 2008-10-28, 11-24, 12-01, 2009-03-23 and 2020-03-16. |
| dividends | adjusted − raw return, 2008 → now, per year: SPY 1.8%, EFA 3.0%, EEM 2.1%, IEF 2.5%, TLT 3.1%, GLD 0.0%, DBC 1.0%, VNQ 4.2%, BIL 1.3%. Each is about the fund's yield. |

## 3. Results (full output: `trend_out.txt`)

**Scored, 5 bp per side:**

| | CAGR | vol | Sharpe | max DD | worst 12 months |
|---|---|---|---|---|---|
| **T1** | 4.37% | 7.1% | 0.45 | **−11.7%** | −8.3% |
| **T2** | 4.57% | 7.1% | 0.48 | **−10.6%** | −9.3% |
| EW (benchmark) | 6.04% | 12.3% | 0.43 | −37.5% | −33.5% |
| 60/40 (reported) | 8.23% | 11.1% | 0.65 | −31.4% | −27.9% |

**By half, 5 bp:**

| | 2008-01 → 2017-06 | | | 2017-06 → 2026-10 | | |
|---|---|---|---|---|---|---|
| | CAGR | Sharpe | max DD | CAGR | Sharpe | max DD |
| T1 | 2.55% | 0.35 | −11.7% | 6.22% | 0.58 | −7.0% |
| T2 | 3.63% | 0.49 | −9.8% | 5.52% | 0.47 | −10.6% |
| EW | 4.28% | 0.35 | −37.5% | 7.83% | 0.54 | −20.6% |
| 60/40 | 7.21% | 0.65 | −31.4% | 9.25% | 0.64 | −21.2% |

**Stress, 20 bp per side, full period:** T1 3.81% CAGR (Sharpe 0.38, max DD −12.0%); T2 4.16%
(0.43, −10.7%); EW 5.98%; 60/40 8.19%.

**Year by year, 5 bp** (2026 runs to 10-30):

| year | T1 | T2 | EW | 60/40 |
|---|---|---|---|---|
| 2008 | −2.0% | +0.9% | −19.1% | −17.5% |
| 2009 | +12.7% | +3.0% | +20.6% | +13.2% |
| 2010 | +6.2% | +13.1% | +16.9% | +13.5% |
| 2011 | +2.2% | +1.8% | +5.0% | +7.9% |
| 2012 | −1.5% | +5.4% | +11.6% | +11.3% |
| 2013 | +2.3% | +3.2% | −1.3% | +15.6% |
| 2014 | +2.7% | +5.1% | +4.0% | +11.9% |
| 2015 | −4.7% | −1.6% | −6.3% | +1.7% |
| 2016 | +2.7% | −1.2% | +8.2% | +7.7% |
| 2017 | +10.3% | +11.3% | +14.5% | +13.7% |
| 2018 | −2.5% | −6.5% | −6.3% | −2.0% |
| 2019 | +7.7% | +10.0% | +19.4% | +21.8% |
| 2020 | +10.3% | +1.6% | +12.0% | +16.6% |
| 2021 | +11.2% | +9.7% | +12.5% | +15.1% |
| 2022 | −4.4% | −3.2% | −13.7% | −16.6% |
| 2023 | +1.0% | +4.5% | +9.9% | +16.8% |
| 2024 | +6.3% | +4.9% | +7.4% | +14.2% |
| 2025 | +16.7% | +18.4% | +20.3% | +13.9% |
| 2026 | +8.2% | +8.8% | +9.6% | +6.4% |

**Trading:** over 226 monthly rebalances, T1 switched a slot between ETF and cash 289 times and
T2 200 times, roughly one switch a month. That's why costs hardly matter.

**Descriptive only, 2007-03 → 2007-12:** T1 +14.0%, T2 +13.1%, EW +12.4%, 60/40 +7.1%.

## 4. The pre-registered criteria (vs EW, 5 bp)

| | (a) max DD ≥ 30% smaller | (b) CAGR ≥ EW − 1.5 pp | (c) Sharpe ≥ EW | (d) all three in both halves | **verdict** |
|---|---|---|---|---|---|
| **T1** | −11.7% vs limit −26.2% ✅ | 4.37% vs floor 4.54% ❌ | 0.45 vs 0.43 ✅ | 1st half (b) ❌ (c) ❌; 2nd half (b) ❌ | **FAIL** |
| **T2** | −10.6% vs limit −26.2% ✅ | 4.57% vs floor 4.54% ✅ | 0.48 vs 0.43 ✅ | 1st half all ✅; **2nd half (b) ❌ (c) ❌** | **FAIL** |

**What it means, as fixed in advance:** neither variant passes, so trend-following on this
universe doesn't clear the bar after 2008. It's closed, with no re-run on other lookbacks,
universes or weights. **No implementation section, and no bot.**

## 5. Observations from the table above (descriptive; no extra runs were made)

1. **The drawdown protection is real and large.**
   - In the four years EW fell more than 5% (2008 −19.1%, 2015 −6.3%, 2018 −6.3%, 2022
     −13.7%), T1 lost less every time, usually much less.
   - T2 lost less in three of the four. 2018 is the exception: −6.5% against −6.3%.
   - The protection is the published effect, and it held out of sample.
2. **The price was the rally years.**
   - The worst lags came in years the market climbed after a scare: 2012, 2016, 2019 and 2023.
     Each was 5–13 pp behind EW for one or both variants.
   - T2 also missed most of the 2009 and 2020 rebounds (+3.0% and +1.6% against EW's +20.6%
     and +12.0%).
   - After 2017 EW's own drawdowns were milder (−20.6%), so the insurance cost more than it
     returned. The second half is where both variants fail.
3. **Per unit of risk, T2 roughly matches EW:** a Sharpe of 0.48 vs 0.43 overall, with about
   60% of EW's volatility. The bar, though, was matching EW's *return* within 1.5 pp, and it
   didn't do that in 2017–2026.
4. **60/40 beat everything on CAGR and Sharpe.** That's hindsight about one of the best periods
   ever for US stocks and bonds together. It was a reported comparison, not the target, and
   it doesn't change the verdict.

## 6. Verdict

**FAIL, for both T1 and T2.**
- Trend-following on these eight ETFs delivered the drawdown cut the literature promises
  (−11% instead of −37.5%).
- It did not keep enough of the return to meet the pre-registered bar, specifically in the
  2017–2026 half.
- Under the PREREG it's closed. There's no implementation section, and nothing should be built
  for trading.

## What I'm unsure about

- **The bar sits on a knife edge.** T2's full-period CAGR passed by 0.03 pp. A different
  data vendor, a slightly different cash series, or one more month of data could flip
  criterion (b) for the full period either way. It wouldn't flip (d): the second-half gap is
  0.8 pp of CAGR and 0.07 of Sharpe.
- **Yahoo's adjustments are a vendor's.** The checks (no split jumps, dividend gaps matching
  yields) passed, but I didn't cross-check against a second source, because Stooq wasn't
  needed. A mis-adjusted dividend in one ETF would move results by tenths of a percent a year.
- **The halves split is calendar-based** (2017-06-01), as pre-registered. A different split
  point would give different half-period numbers. That's exactly why it was fixed in advance.
- **Cash is BIL,** which carries about a 0.14% fee. Raw T-bills would have helped T1 and T2
  slightly, because they sit in cash part of the time, and likely by less than T1's 0.17 pp
  shortfall. I didn't rerun it: it isn't the pre-registered cash series, and running it would
  be a rescue attempt.
- **One fixed universe, 2008–2026:** a few big episodes (2008, 2020, 2022) decide most of the
  drawdown result. That cuts in favour of the strategy, but the return result, which is the
  part that failed, comes from the many ordinary years in between.
