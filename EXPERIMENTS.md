# Experiment queue — paper is the lab, live is frozen

_Opened 2026-09-27._

## The design

Two instances, two accounts, one codebase:

- **LIVE (`/opt/orb-bot-live`)** — frozen. No strategy parameter changes, ever,
  except by promotion through the gate below. Its only job is to tell you what
  the validated config does with real fills.
- **PAPER (`/opt/orb-bot`)** — the lab. One change at a time, pre-registered,
  measured against the 100-trade baseline.

**The cost of this design, stated up front:** the moment paper carries a change
live does not, paper stops predicting live. That is an acceptable trade only
because live is frozen and the gate is strict. If both drift, you have two
uncontrolled experiments and no baseline.

## The promotion gate

A change moves paper → live only when ALL of these hold:

1. **n ≥ 50** completed paper trades on the change, uninterrupted, no config
   edits mid-sample.
2. The improvement is **stated before the sample starts**, as a number, in
   this file.
3. The result clears that number.
4. The result does not depend on the top 2 trades — drop them and the sign
   must survive.
5. A second reviewer (me, or you a week later) can restate why the change
   should work **mechanically**, without referring to the backtest.

Criterion 5 is the one that matters. This project is 0-for-7 on parameter
searches surviving out of sample (six ORB sweeps, plus Track B, whose
in-sample +92.2% / +0.091R came back −17.0% / −0.184R with the sign reversed).
Every one of those had a number that cleared a bar. None had a mechanism.

## Baseline — the number everything is measured against

**100 trades, total −22.60, win rate 44.0%, expectancy −0.23/trade,
max drawdown 237.59, avg win +14.27, avg loss −11.62.**

t ≈ −0.18. Indistinguishable from zero in either direction. At this variance,
separating a real +0.50/trade edge from noise takes thousands of trades.

---

## E1 — Entry slippage band  ★ highest measured value, no curve-fitting

**The finding.** Entry slippage across 98 matched trades: mean +0.046%,
**$0.31 per trade, $30.60 in total.** Total trial P&L is −$22.60.

> Gross of entry slippage, the trial is roughly **+$8**. Net of it, −$22.60.
> The entire gap between this bot losing money and breaking even is what it
> pays to get in.

That is not a parameter to tune, it is a cost to cut, which is why it goes
first. The tail is where it lives:

| entry slippage | n | total | per trade | win rate |
|---|---|---|---|---|
| favourable (<0%) | 25 | +96.68 | +3.87 | 52.0% |
| 0.00 – 0.10% | 36 | −144.05 | −4.00 | 30.6% |
| 0.10 – 0.30% | 15 | +38.04 | +2.54 | 46.7% |
| **≥ 0.30%** | **7** | **−86.30** | **−12.33** | **14.3%** |

**Mechanism** (criterion 5): the entry is a marketable limit capped ~0.5% above
the reference. A fill needing the full band means price ran away between signal
and fill — you bought the top of the breakout impulse, which is the worst place
to buy a mean-reverting failure. 1 in 7 of those won.

**Honesty check:** the 0.00–0.10% bucket is the worst of the middle three, so
this is NOT monotonic and the 7-trade tail is 7 trades. The mechanism is the
reason to test it; the table is not proof.

**Change:** entry limit band 0.5% → 0.20%.
**Pre-registered criterion:** over ≥50 paper trades — mean entry slippage
**< 0.02%**, expectancy improvement **≥ +0.25/trade** vs baseline, and fewer
than 25% of signals lost to no-fill. Miss any one → revert, do not re-tune.

## E2 — Opening relative-volume floor  ★ the one thing the source paper insists on

`strategy.min_opening_rel_volume` is **0.0** — the filter is off. Zarattini,
Barbon & Aziz (2024), the paper this bot is built from, found it did almost all
the work: unfiltered ORB across ~7,000 stocks returned 29% (Sharpe 0.48); the
same strategy restricted to unusually active names returned 1,637%
(Sharpe 2.81). Their gradient: below 100% relative volume −0.02R per trade,
above 100% +0.08R, above 3000% +0.38R.

45 of the trial's trades have a logged RELVOL:

| opening rel. volume | n | total | per trade |
|---|---|---|---|
| < 1.0x | 10 | −14.84 | −1.48 |
| ≥ 1.0x | 35 | −12.82 | −0.37 |

Same direction as the paper. Both still negative, and n=10 in the interesting
bucket, so this is corroboration, not evidence. Median rel-vol is already
1.86x — the screener mostly picks active names on its own, which is why the
expected effect is modest.

**Change:** `min_opening_rel_volume: 1.0`.
**Pre-registered criterion:** ≥50 paper trades, expectancy improvement
**≥ +0.30/trade**, skip rate < 30%.

## E3 — Paper-vs-live fill penalty  ★ the real prize of running both

Alpaca's paper engine fills optimistically. **Every number in the baseline is
a paper number**, so the honest prior is that live performs *worse* than paper
on identical signals — before any edge question. Live also pays SEC and FINRA
TAF fees on sells that paper does not simulate (pennies per trade here, but
real).

Running both instances measures this for the first time. On any day both
trade the same symbol, log reference price, fill price and slippage from each
and compare.

**Not a change — a measurement.** After ~20 overlapping trades you will know
the size of the paper→live haircut, which tells you how much of the paper
baseline to believe. This is worth more than E1 and E2 combined, and it costs
nothing but patience.

## E4 — Time-of-day entry window  ⛔ NOT recommended

The data is tempting: entries ≥40 min after the open ran +1.31/trade (n=20)
against −1.54 for the first 20 minutes (n=39).

Do not touch it. `entry_window` has already been swept twice, recommended once
and **withdrawn** when the holdout said 60 beats 180. There is no mechanism
for "later is better" in an opening-range-breakout strategy — the premise is
that the opening range carries the information. This is the shape of every
result this project has already been burned by: a clean-looking split in ~60
trades, no mechanism, sign reverses out of sample.

Listed here so that when it looks appealing again in three weeks, it is already
written down why it was passed over.

---

## Log

| date | change | instance | n | result | decision |
|---|---|---|---|---|---|
| 2026-09-27 | (baseline) | paper | 100 | −0.23/trade | — |
