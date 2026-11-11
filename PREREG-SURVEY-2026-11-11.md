# PREREG: survey of published daily strategies for the next paper bot (E19), 2026-11-11

**Written and committed before any price data was fetched and before any backtest ran.**
Results are appended below the line at the end. Nothing above the line changes after the run.

## What this is

Ethan asked for a second bot to paper trade while E16 (QQQ noise-area momentum) runs its
two-year forward test. This is a survey: seven tests of six published or widely-run strategy
families, each frozen at its source's parameters with no tuning, scored on one common period
against one common bar. The output is a ranking and one candidate for a forward paper test.

It shares no code with ORB or E16, places no orders and touches no account. `survey.py` is the
only new code; the cache is `.survey_cache/` (gitignored).

**What was deliberately left out, and why:**
- Anything E14, E15 or E18 already closed: ORB entries, last-half-hour momentum, overnight
  drift, turn of the month, the Faber and 12-month trend rules and their other lookbacks.
- Intraday index momentum: that is E16, and it only gets tested on forward data.
- Earnings drift: no earnings-date or surprise data is available here.
- Pairs trading and long-short reversal: both need shorting many names, and the literature
  (Do & Faff 2010; Blitz et al. 2022) puts them at about zero net of costs.

## The candidates, fixed now

All signals use daily bars. "Close" execution means the signal is computed from the day's
closing values and filled at that same close (the bot would read the price a few minutes before
the bell). Every candidate is also run with the fill delayed to the **next open** as a stress.

| # | Candidate | Source | Instrument | Rule (frozen) |
|---|---|---|---|---|
| C1a | RSI(2) pullback | Connors & Alvarez 2008 | SPY | Buy at the close when RSI(2) < 10 and close > 200-day SMA. Sell at the close when close > 5-day SMA. |
| C1b | RSI(2) pullback | same | QQQ | Same rule on QQQ. |
| C2 | IBS band reversion | Quantitativo 2024 (after Pagonidis 2013) | QQQ | Buy at the close when close < (10-day highest high − 2.5 × 25-day mean of high−low) and IBS < 0.3, where IBS = (close−low)/(high−low). Sell at the close when close > the previous day's high. Base rule only: the article's 300-day SMA variants were tuned and are reported as descriptive. |
| C3 | Dual-signal VIX premium | Zarattini, Mele & Aziz 2025 | VIXY | eRV = stdev of the last 10 daily SPY returns × √252 × 100. eVRP = VIX − eRV. Target VIXY weight: −VIX/100 if eVRP > 0 and VIX < VIX3M; −0.5 × VIX/100 if eVRP < 0 and VIX < VIX3M; +VIX/100 if eVRP < 0 and VIX > VIX3M; otherwise 0. Rest in cash (no SPY overlay). Rebalance at the close when the sign changes or the weight is off target by more than 0.02. |
| C4 | Leveraged 200-day trend | Gayed & Bilello 2016 | SSO / cash | If SPY's close is above its 200-day SMA, hold SSO (2x S&P 500) from the next close; otherwise cash. |
| C5 | Crypto Donchian ensemble | Zarattini, Pagani & Barbon 2025 | BTC-USD | Nine models, lookbacks 5, 10, 20, 30, 60, 90, 150, 250, 360 days. Each goes long when the close is the highest close of its lookback, and exits when the close falls below a trailing stop: the running maximum, since entry, of the midpoint of the lookback's highest and lowest close. A model that is long holds min(1, 25% / annualised 90-day volatility); the target is the mean across the nine. Long-only, no leverage. Rebalance at the daily (UTC) close when the weight is off target by more than 0.05. |
| C6 | RSI(2) pullback, large-cap stocks | Connors & Alvarez 2008, portfolio form | top-200 US stocks | Each day, among universe stocks with close > 200-day SMA and RSI(2) < 10, buy at the close the ones with the lowest RSI(2), 10% of equity each, at most 10 positions. Sell at the close when close > 5-day SMA, or after 10 trading days. |

RSI uses Wilder's smoothing. SMAs use adjusted closes. C1, C2 and C6 are fully invested in
the single position (C1, C2) or 10% per slot (C6), and otherwise in cash.

C4 is a relative of E18's trend rules. It is in the survey because it is one of the most
widely run public rules, and it differs in kind (one asset, daily signal, leverage). **It
cannot rescue E18**, and E18's verdict stands whatever happens here.

## Data, fixed now

- **ETFs, indices, BTC:** Yahoo's daily chart API (the one `trend.py` uses), OHLC plus
  `adjclose`. Open, high and low are scaled by adjclose/close, so returns are total returns.
  Tickers: SPY, QQQ, SSO, VIXY, BIL, ^VIX, ^VIX3M, ^IRX, BTC-USD.
- **C6 universe, built point-in-time to limit survivorship bias:** Alpaca's asset list,
  active **and inactive**, NYSE and NASDAQ listings only, with fund-like names removed by
  keyword (ETF, ETN, Fund, Trust, Shares, ProShares, Direxion and similar). Monthly bars give
  each name's dollar volume. The universe for a month is the 200 names with the highest
  dollar volume over the previous three complete months and a last price of at least $5.
  Daily bars (`adjustment=all`) are fetched for every name that is ever in the universe.
  - Known gaps, reported and not fixed: Alpaca's history starts in 2016; names delisted
    before Alpaca listed them are missing; the keyword filter will misclassify a few names.
  - If this build fails or looks wrong, C6 is reported as **not run**, not patched.
- **Checks reported before scoring:** date coverage per series; any daily |return| > 25% listed
  (20% for ETFs); row counts; the download time.

## Periods

- **Scored: 2016-01-01 to the latest close**, common to all candidates. C6 is scored from
  2017-01-01, because Alpaca's data starts in 2016 and the 200-day SMA needs a year.
- **Halves:** the scored period split at its calendar midpoint.
- **Descriptive only:** everything before 2016 (most sources were fitted there), and the
  window after each source's publication date.

**How independent the scored period is, per candidate, stated before looking:** C1, C6
(2008) and C4 (2016) were published before or at the start of it, so it is out of sample
for them. C2 (2024), C3 (2025) and C5 (2025) were published near its end, so most of it
is inside their authors' samples. A pass for those three carries less weight, and their
post-publication windows are too short to settle anything.

## Costs and cash

| | scored, per side | stress, per side |
|---|---|---|
| SPY, QQQ, SSO | 2 bp | 5 bp |
| VIXY | 5 bp, plus 3%/yr borrow on short notional | 10 bp, 6%/yr borrow |
| Large-cap stocks | 5 bp | 10 bp |
| BTC | 30 bp | 60 bp |

E16's measured adverse slippage on QQQ is about 1 bp per side, so 2 bp is conservative.

Returns are scored as **excess returns of the positions held**: each day's return is
Σ weight × (asset return − cash return) − costs, where cash is BIL (^IRX/252 before BIL has
a price; BIL's calendar-day return for BTC weekends is taken as zero). Idle cash is not
credited. CAGR on a "cash earns nothing" basis is reported alongside.

## The bar: one definition for all seven tests

The thing being tested is **timing skill, not exposure.** A long-only rule that holds QQQ a
fifth of the time earns a fifth of QQQ's premium for nothing, which is how E15's overnight
rule looked profitable and was market beta. So each candidate is regressed on its own
underlying, daily, over the scored period:

    strategy excess return = alpha + beta × benchmark excess return + error

Benchmarks: SPY for C1a, C3, C4 and C6; QQQ for C1b and C2; BTC-USD for C5.

A candidate **PASSES** only if all five hold on the scored period, at scored costs:

1. **Alpha t-statistic ≥ 2.45** (Newey-West, 5 lags). That is one-sided p = 0.05 with a
   Bonferroni correction for seven tests.
2. **Alpha > 0 in both halves.**
3. **Net mean excess return > 0 at stress costs, and > 0 with fills at the next open.**
4. **Not carried by a few trades:** the total net return stays positive with the best 5% of
   trades removed (for C3, C4 and C5, which hold continuously: with the best 10 days removed).
5. **Maximum drawdown no deeper than −25%.**

Reported for every candidate but not part of pass/fail: CAGR, volatility, Sharpe, time in the
market, trades per year, win rate, mean return per trade, the yearly table, and correlation
with SPY.

## How the candidate for the bot is chosen

- If one or more pass: the one with the highest alpha t-statistic, **provided it makes at least
  20 trades a year.** A rule that trades five times a year cannot be judged on paper before
  2030, so it is reported as a pass and not built.
- If none pass: say so. The best-ranked candidate may be named a **lead**, as E15's QQQ result
  was, and a lead only earns a forward paper test with its own PREREG. It is not a result.
- Whatever is chosen gets a forward test in the E16 style: frozen rule, a stated sample size,
  futility looks, and a power calculation. No live money is in scope.

## Things that will not be done after seeing results

No changes to thresholds, lookbacks, instruments or the scored period. No dropping a candidate's
bad years. No swapping the benchmark. Variants not listed above (other RSI levels, an IBS-only
rule, TQQQ for SSO, ETH for BTC, a trend filter on C2) are reported, if at all, under a
"post hoc" heading and cannot produce a pass.

---

## Results (appended after the run)

**Run 2026-11-11.** Full report: `ANALYSIS-2026-11-11-survey.md`. Raw output: `survey_out.txt`.
Yahoo data downloaded 2026-11-11 17:00 UTC; scored through the 2026-11-10 close.

| # | Rule | Alpha / yr | Alpha t | Halves | Max DD | 1 | 2 | 3 | 4 | 5 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C1a | RSI(2), SPY | +2.93% | +1.82 | +0.81% / +4.99% | −11.3% | n | Y | Y | Y | Y | FAIL |
| C1b | RSI(2), QQQ | +1.62% | +0.82 | −2.26% / +5.32% | −9.7% | n | n | Y | Y | Y | FAIL |
| C2 | IBS band, QQQ | +3.36% | +1.08 | +1.32% / +5.29% | −16.4% | n | Y | Y | Y | Y | FAIL |
| C3 | VIX premium, VIXY | +9.75% | +1.45 | +17.26% / +3.32% | −29.7% | n | Y | Y | Y | n | FAIL |
| C4 | 200-day trend, SSO | +4.63% | +0.83 | +8.94% / +0.42% | −43.1% | n | Y | Y | Y | n | FAIL |
| C5 | Donchian ensemble, BTC | +9.74% | +2.75 | +17.81% / +1.44% | −20.2% | Y | Y | Y | Y | Y | **PASS** |
| C6 | RSI(2), top-200 stocks | −4.33% | −0.82 | −7.55% / −0.98% | −42.0% | n | n | Y | n | n | FAIL |

**One of seven passes: C5.** It trades about 63 times a year, so by the selection rule above
it is the candidate for the forward paper test, which is E20
(`PREREG-BTC-TREND-PAPER-2026-11-11.md`).

As this file said before the run, a pass for C5 carries less weight: its source was published
in April 2025, so most of the scored period is inside the authors' sample. The second-half
alpha is +1.44% a year at t = 0.35, and the rule is slightly negative since publication
(alpha −3.3% a year, t = −0.53, 19 months).

**Interpretations made while coding, before any result was seen:**
- Criterion 5's drawdown is measured on the "cash earns nothing" equity curve.
- C5's "next open" stress is a one-day delay, since BTC has no open distinct from the prior close.
- "Latest close" is 2026-11-10: the 2026-11-11 bar was still forming when the data was fetched.
- C6's keyword filter, as written above, also removes real companies listed as "... Shares" or
  "... Trust" (many ADRs and REITs). It was left as pre-registered.

**C6 data build:** 33,560 Alpaca assets, 4,929 kept (773 inactive), 572 names ever in the
top 200, 51 of them since delisted. 0.12% of daily returns exceed 25% in size; the worst
trades were checked and are real events.

**Post hoc, labelled as such** (`survey_posthoc.txt`): vol-targeted buy and hold BTC t = 1.07;
the same ensemble on ETH from 2019 t = 1.47; C2's tuned variants and C2 on SPY and IWM all
weaker than C2 on QQQ; monthly momentum in C6's universe shows no alpha. None of these changes
a verdict.
