# PREREG: monthly trend-following across asset-class ETFs (E18), 2026-11-01

**Written and committed before any price data was fetched and before any backtest ran.**
Results are appended below the line at the end, and nothing above it changes after the run.

## What this is, and the bar it has to clear

This is a slow, long-only allocation that trades about once a month across eight asset-class
ETFs. **The goal is not to beat the stock market.** It is to roughly **match an equal-weight
buy-and-hold of the same eight ETFs on return, with much smaller drawdowns.** It shares no code
with ORB or E16, and no account trades it.

## Universe, fixed now

| ETF | asset class | inception |
|---|---|---|
| SPY | US large-cap equity | 1993-01 |
| EFA | developed ex-US equity | 2001-08 |
| EEM | emerging-market equity | 2003-04 |
| IEF | 7–10y US Treasuries | 2002-07 |
| TLT | 20+y US Treasuries | 2002-07 |
| GLD | gold | 2004-11 |
| DBC | broad commodities | 2006-02 |
| VNQ | US REITs | 2004-09 |

**No substitutions are needed.** The latest inception is DBC (2006-02-03), which gives 22 months
of history before the scored period and so covers T1's 10-month SMA and T2's 12-month lookback.

**Cash ("T-bills"):** BIL (SPDR 1–3 Month T-Bill) total return. It starts on 2007-05-30. On any
day before BIL has a previous close, the cash return is the previous day's 13-week T-bill
yield (^IRX, %) / 100 / 252, which is about ^IRX / 12 a month. That only touches T2's hurdle in
2007 and the descriptive window.

## Rules, fixed now: two variants, scored separately

Signals use **month-end adjusted closes** (the last trading day of each month).

- **T1 (Faber 2007, "A Quantitative Approach to Tactical Asset Allocation"):** each of the 8
  slots holds its ETF at 1/8 weight if the month-end close is **above its 10-month simple moving
  average**. The SMA is the mean of the last 10 month-end closes, the current one included.
  Otherwise that slot holds cash.
- **T2 (12-month time-series momentum, Moskowitz, Ooi & Pedersen 2012, long-only):** each slot
  holds its ETF at 1/8 if its **12-month total return** (month-end to month-end, adjusted)
  **exceeds the cash return over the same 12 months**. Otherwise it holds cash.

**Execution:**
- Signals are computed at the month-end close and **traded at the next trading day's close**.
- Every slot is reset to exactly 1/8 at each rebalance, held in its ETF or in cash, and weights
  drift with daily returns until the next rebalance.
- **Costs: 5 bp per side** on traded notional, Σ |target weight − drifted weight| across the
  eight ETFs and cash, scored. **20 bp per side** as a stress run, not scored.

**Benchmarks** use the same execution day and costs:
- **EW:** equal-weight buy-and-hold of the same 8 ETFs, rebalanced monthly to 1/8. This is the
  scored comparison.
- **60/40:** 60% SPY, 40% IEF, rebalanced monthly. Reported, not scored.

## Data, fixed now

- **Source:** Yahoo Finance's daily chart API (`query1.finance.yahoo.com/v8/finance/chart`, the
  endpoint yfinance wraps). It uses `adjclose`, which is adjusted for splits and dividends, so
  it's a total-return series.
  - If Yahoo refuses, the fallback is Stooq daily CSVs. The report says which source was used.
  - The download date is recorded.
- **Checks before scoring, reported:**
  - Every ETF has data covering 2006-02 → now.
  - The dividend and split events are listed per ETF. EFA's and EEM's share splits must not
    show as price jumps in `adjclose`.
  - Any daily |return| > 15% is listed and explained.
  - Raw-close total return and `adjclose` total return differ by roughly the dividend yield.

## Periods

- **Scored:** the first trade is on the first trading day of 2008, from the signals of
  2007-12-31, and the period runs to the latest available close. This is after Faber's
  publication.
- **Halves:** the scored period is split at its calendar midpoint, and each half is evaluated
  as if started fresh.
- **Descriptive only:** 2007-03 → 2007-12. This is the only earlier window where all eight ETFs
  have a full signal (DBC's history is short). Faber's own 1972–2005 results are the real
  pre-publication evidence, and this test doesn't repeat them.

## Metrics, fixed now

All are computed on daily portfolio values:
- **CAGR** = (end / start)^(365.25 / calendar days) − 1.
- **Volatility** = SD of daily returns × √252.
- **Sharpe** = mean(daily return − daily cash return) / SD(daily return − daily cash return)
  × √252.
- **Max drawdown** = the largest peak-to-trough fall in value.
- **Worst 12 months** = the lowest value(t) / value(t − 252 trading days) − 1.
- **Year-by-year returns.**

## Pass criteria, fixed now (each variant vs EW, scored period, 5 bp)

- **(a)** max drawdown at least **30% smaller**: |MDD_T| ≤ 0.70 × |MDD_EW|.
- **(b)** CAGR no more than **1.5 percentage points lower**: CAGR_T ≥ CAGR_EW − 1.5 pp.
- **(c)** **Sharpe not lower**: Sharpe_T ≥ Sharpe_EW.
- **(d)** (a), (b) and (c) **each still hold in both halves.**

**A variant PASSES only if all four hold.** T1 and T2 are scored independently. Whichever passes
earns an implementation section in the report. Neither one earns a trading bot yet.

## What it means

| result | meaning |
|---|---|
| Both pass | The published effect survives out of sample on liquid ETFs. Write the implementation section. |
| One passes | Same, for that variant only. The other is closed, with no re-cut. |
| Neither passes | Trend-following on this universe doesn't clear the bar after 2008. Closed. No re-run with other lookbacks, universes or weights: that would be a new PREREG on new reasoning, not a rescue. |

## Weaknesses, stated before the data

1. **The scored period holds only a few big trend episodes:** 2008, 2020 and 2022. The drawdown
   criterion is mostly decided there.
2. **The universe is fixed with hindsight about which ETFs exist and survived.** All eight are
   large, long-lived funds, which is mild survivorship.
3. **Yahoo's `adjclose` is a vendor adjustment.** The checks above test it but can't fully
   verify it.
4. **BIL charges about 0.14% a year,** which is in its total return. That is realistic, but it
   makes cash slightly worse than raw T-bills.

## Run discipline

- `trend.py` is a new standalone script. It doesn't import ORB or E16 code. It has its own
  tests in `tests/test_trend.py`.
- Fetch once, run once. Anything beyond the above is labelled **post-hoc**.

---

## Results

*(appended after the run)*
