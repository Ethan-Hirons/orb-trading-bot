# PREREG: BTC Donchian ensemble, forward paper test (E20), 2026-11-11

**Written and committed on 2026-11-11, before the first forward day.** Only days from
**2026-11-12 (UTC)** onward count as evidence. The rule comes from E19
(`PREREG-SURVEY-2026-11-11.md`, `ANALYSIS-2026-11-11-survey.md`), where it was the one candidate
of seven to clear the pre-registered bar. Nothing here places an order by being committed:
the paper runner only starts when Ethan creates its account and enables its timer.

## Why a forward test, and what E19 did and did not show

E19 scored this rule on 2016-01 to 2026-11 at 30 bp per side: alpha against buy-and-hold BTC
of +9.7% a year, t = 2.75, maximum drawdown −20%. It passed all five criteria. Three things
weaken that, and all three were visible when this was written:

1. **The edge is concentrated in the first half.** Alpha was +17.8% a year in 2016-01 to
   2021-06 (t = 3.1) and +1.4% in 2021-06 to 2026-11 (t = 0.35). By period it runs +20% (2016–18),
   +11% (2019–21), +7% (2022–24), then −4.5% in 2025–2026.
2. **Most of the scored period is inside the source paper's own sample.** Zarattini, Pagani
   and Barbon published in April 2025. Since then the rule is slightly negative.
3. **The underlying went up about 170-fold.** Any long-only rule on BTC in this decade made
   money. Alpha removes the average exposure, but one asset in one long bull market is still
   one path.

So the backtest earns the rule a forward test and nothing more.

## The rule, frozen

Exactly `survey.donchian_targets` and `survey.apply_threshold(..., band=0.05, sign_rule=False)`
as committed with this file:

- Daily closes are **UTC days** of Alpaca's BTC/USD bars (the bar stamped day D closes at
  00:00 UTC on D+1). History is rebuilt from **2021-01-01** on every run.
- Nine models with lookbacks 5, 10, 20, 30, 60, 90, 150, 250 and 360 days. A model goes long
  when the close is the highest close of its lookback. It exits when the close falls below
  its trailing stop: the running maximum, since entry, of the midpoint between the lookback's
  highest and lowest close.
- A model that is long holds min(1, 25% / annualised 90-day volatility) of equity. The target
  weight is the mean of the nine. Long-only, never above 100% of equity.
- Trade to the target only when the current weight differs from it by more than **0.05**.

**Data source change, stated up front:** E19 scored the rule on Yahoo's BTC-USD series. The
forward record uses Alpaca's, because that is the venue the paper account trades. On
2022-04 to 2026-11 the two sources' closes differ by a median of 3.5 bp, and the target
weights correlate 0.998; they differ by more than 0.05 on 31 of 1,680 days.

## Evidence: the shadow record, not the paper fills

`python btc_bot.py shadow` replays the frozen rule on Alpaca's daily closes from 2026-11-12
at **30 bp per side**, and regresses its daily returns on BTC's. That record is the evidence,
as E16's SIP shadow is for QQQ. It exists whether or not the paper runner is deployed.

**Looks, at fixed sample sizes (forward UTC days):**

| look | when | rule |
|---|---|---|
| 180 days | ~May 2027 | report only |
| 365 days | ~Nov 2027 | stop for futility if alpha ≤ 0 |
| 730 days | ~Nov 2028 | stop for futility if alpha ≤ 0 |
| 1,095 days | ~Nov 2029 | final |

**PASS at the final look needs all four:**
1. alpha t ≥ 1.645 against buy-and-hold BTC (Newey-West, 5 lags; one-sided 5%);
2. total net return > 0;
3. maximum drawdown no deeper than −25%;
4. the sum of daily returns stays positive without the best 10 days.

**Power, stated before the sample.** The rule's information ratio (alpha over residual
volatility) was 0.87 on E19's full period and 0.15 on its second half.
- If the full-period figure is the truth, the expected t after three years is about 1.5, and
  the chance of a PASS is roughly **40–45%**.
- If the second-half figure is the truth, the chance is about **8%**.
- If there is no edge, it is at most 5%.

**This test will probably not produce a PASS even if the long-run edge is real.** That is the
cost of a rule that makes one slow bet on one asset, and it is the honest reason E16 (many
independent intraday bets, a holdout Sharpe near 1.5) remains the project's main lead. E20's
quicker payoffs are Stage A and the futility looks.

## Stage A: mechanics, the first 30 daily runs of the paper runner

The runner (`btc_bot.py run`) exists to measure what the shadow cannot: fills, fees and
whether a daily job can hold the target. Stage A passes if all four hold:

1. a ledger row on at least 29 of 30 days, none of them `refused`;
2. the account's weight after the run is within 0.02 of the shadow's weight on at least 95%
   of days;
3. fees plus adverse slippage average no more than 30 bp per side, measured from the
   account's own fills against the last trade price read just before each order;
4. the ORB and E16 accounts are untouched.

If criterion 3 fails, the shadow is re-scored at the measured cost from day one, and that
re-scored record is the evidence. That is the only change Stage A can make.

## What a PASS would and would not mean

- It would support "a Donchian trend ensemble adds risk-adjusted return over holding BTC".
  It would not show the rule works on other coins, with leverage, or short.
- It would reach the promotion gate in `EXPERIMENTS.md`. Live money is not in scope here and
  would be Ethan's decision under that gate.

## What will not be done

No change to lookbacks, the volatility target, the cap, the band or the history start. No
adding ETH or other coins to this record: a second coin needs its own PREREG and its own
clock. No restarting the clock after a bad stretch. Mechanics fixes to `btc_bot.py` are
logged below with their date and must leave `survey.donchian_targets` untouched.

---

## Forward record (appended, never edited)

