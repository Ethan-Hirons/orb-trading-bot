# Experiment queue — paper is the lab, live is frozen

_Opened 2026-09-27._

## This file does not replace `PREREG-2026-09-22.md`

You already pre-registered **H1** (require a news-confirmed direction) and
**H2** (stop at the opening-range low) there, with sim-first testing on an
untouched window and four pass criteria each. That document governs those two
hypotheses and its rules stand unchanged — sim first, once, no re-cuts.

What follows are **additions**, numbered E-something so they never get
confused with H1/H2, and they are deliberately a different kind of thing:
E1 and E3 are costs and measurements rather than strategy parameters, which
is why they can be tested on paper directly without a sim run.

`loss_anatomy.py` on the current 59-trade cut, for context — it is the
strongest support H1 has:

| bucket | n | stop rate | mean R | net $ |
|---|---|---|---|---|
| **stock / news-confirmed** | 17 | 41% | **+0.21** | **+31.48** |
| stock / no directional news | 13 | 69% | −0.79 | −105.58 |
| leveraged ETP / news-confirmed | 2 | 50% | −0.57 | −11.93 |
| **leveraged ETP / no directional news** | 18 | 61% | **−0.43** | **−79.07** |
| crypto ETF / news-confirmed | 2 | 50% | −0.09 | −1.97 |
| crypto ETF / no directional news | 7 | 29% | −0.19 | −13.01 |

One positive bucket out of six, and the losses concentrate in leveraged ETPs
taken with no directional news. Seventeen trades. That is the same size of
slice that produced every result this project has already had reversed on it,
which is exactly why H1 goes to the sim on an untouched window before it goes
anywhere near a config.

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

**102 trades, total −30.69, win rate 44.1%, expectancy −0.30/trade,
max drawdown 237.59.** _(Restated 2026-09-30 from the paper log. The previous
baseline — 100 trades, −22.60, −0.23/trade, 44.0%, avg win +14.27, avg loss
−11.62 — was current to 09-24. Two trades since then netted −8.09. Every
criterion below was written against the OLD number; restate each one against
−0.30 before its sample starts, never after seeing the result.)_

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

### 2026-10-26 — ENTRY TIMING, not fill quality, is what separates the two

The cleanest divergence of the trial, and it reframes what E3 is measuring.

**CRWV traded on both sides and the outcomes were opposite:**

| | entered | ref | fill | MFE | MAE | exit | result |
|---|---|---|---|---|---|---|---|
| LIVE | 09:48:00 | 52.97 | 52.9755 | **+1.45%** | −0.12% | trail | **+0.83 (+0.195%)** |
| PAPER | 09:49:12 | 53.45 | 53.49 | +0.48% | −1.25% | stop | **−11.31 (−1.626%)** |

**72 seconds apart, 0.9% apart on entry price.** Live's entry left room for
the stock to run +1.45%; the trail armed 09:50:14 (peak +1.35%, armed +1.38%)
and exited 09:52:32 for a small gain. Paper entered near the high, never got
far enough above water to arm — **zero TRAIL lines in the paper log all day** —
and took the full stop.

RHI diverged further: live 10:05:41, paper 10:18:18 — **12.5 minutes apart**,
on the same signal. Both stopped. On 09-29 the two instances fired 3 seconds
apart; this is three orders of magnitude larger and is not explained by fill
quality.

**Whole-day comparison, live vs paper on identical symbols:**

```
CRWV   live +0.195%   paper −1.626%     live better by 1.82pp
TSLG   live −1.467%   paper −1.648%     live better by 0.18pp
RHI    live −1.501%   paper −1.684%     live better by 0.18pp
```

Paper overshot its −1.5% stop on all four of its trades (−1.557% to −1.684%);
live landed at −1.467% and −1.501%. **This is the opposite of the stated
prior.** E3 was opened on the assumption that paper fills optimistically and
live would underperform. Two sessions now (09-29, 10-26) have gone the other
way, and today's gap is large.

**What this means for the E3 design.** The experiment assumed the two
instances see the same signal at the same moment, so any difference is fill
quality. That assumption is now falsified: they enter minutes apart. Entry
timing dominates, and a haircut measured across entries 12 minutes apart is
measuring two different trades, not two fills of one trade.

**Do not conclude "live is better than paper" from this.** n is tiny, and the
mechanism is almost certainly timing luck — on a day the names had run by
09:49, the later entry loses; on a day they run after, it wins. The finding
is the *variance*, not its sign.

**Next step (measurement, no config change):** log the breakout-detection
timestamp alongside the entry timestamp on both instances, and record the
per-session delta. Candidate causes worth separating: IEX bar arrival
differing between the two hosts, polling-loop phase offset, and the
confirmation rule resolving on different bars. Until that is known, the
paper→live haircut cannot be isolated.

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

## The number every experiment here is chasing

win rate 44.00% | avg win +14.27 | avg loss −11.62 | payoff ratio **1.229**

Breakeven at 44% needs a payoff ratio of **1.273**.

> **The bot is 3.6% of payoff ratio from breakeven — +$0.51 on the average
> winner.** The paper's hit ratio is 48.4% against this bot's 44.0%. Selection
> is not the gap. Payoff structure is.

## E8 — What happens to the breakouts we reject  ★ measurement, zero trading impact

**Opened 2026-09-30**, after a live session with one candidate and no trade.

`confirm_bar_close: true`, `confirm_lookback_bars: 3`,
`volume_filter_ratio: 0.5`. A breakout is taken only if a completed 1-min bar
CLOSES beyond the range within the last 3 bars, on volume ≥ 0.5× the average
opening-range bar.

**Across all 45 logged sessions:**

| | count |
|---|---|
| breakouts seen | **231** |
| taken | 109 |
| **rejected by the confirmation rule** | **122 (53%)** |

**The confirmation rule rejects more breakouts than the bot takes. By volume it
is the single largest gate in the system — and Runs 1–6 never touched it.**
Entry window, OR length, news bias, relvol and the combined levers were all
swept. Confirmation was not. It was set reactively in v1.5 after 2026-07-17,
when five targets broke out within 11 minutes and none confirmed — one bad day,
no holdout.

**The problem: the data to evaluate it is thrown away every session.** The bot
logs `broke out <side> but not confirmed (bar close/volume); waiting (further
waits logged silently)` and then never records what that symbol went on to do.

**The change (logging only, no decision logic touched):** in the rejection path
at `runner.py:1094`, record the symbol, side, time, range level and the price
at rejection; then at EOD emit one line per rejected breakout with its
subsequent high and low relative to the rejection price. Write it to a new
`state/rejected_breakouts.csv` on the same (date, symbol) key as
`trade_features.csv`.

**Pre-registered question, to be answered at n ≥ 100 rejections:** did the
rejected breakouts, on average, run further in the signal direction than they
retraced? If they did, the filter is costing entries. If they faded, it is
earning its keep. **No threshold is being proposed and no config changes on
this result** — it converts an untested assumption into a measured one, and
whatever it says goes to the sim before it goes anywhere near a config.

## E9 — "More targets" ⛔ REJECTED BY THE DATA — target count is not the constraint

Ethan's suggestion on 2026-09-30, after a no-trade day: widen the screen so the
bot takes at least 1–2 trades a day. **The logs say this would not work.**

**CORRECTED 2026-10-01.** The original figure counted raw `TARGET ` log lines,
which double-count — the intraday re-screen reprints the whole list. Actual
distinct targets per session is **6.1 (median 6)**, not 12.4, and the correlation
between a session's target count and its entry count is **r = +0.368**, not
+0.245. Still weak, and the conclusion below is unchanged, but the numbers were
wrong and the corrected ones are these.

Across 45 sessions: ~275 distinct targets, 109 entries, **r = +0.368**.

| | |
|---|---|
| most targets | 35→1 entry, 25→**0**, 24→2, 23→3, 20→2 |
| fewest targets | 1→0, 2→**1**, 3→**1**, 3→**1**, 5→0 |

Zero-entry sessions had 8, 9, 0, 2 and 4 distinct targets — including the
highest-target day in the sample. A 2-target day produced a trade; a 9-target
day produced none. `max_targets` is 10 and the pool routinely sits at 4/10 or
below, so **the cap is not binding and adding capacity would change nothing.**

Adding targets means loosening the screen (`min_prev_day_volume`, gap floors),
which is a strategy-parameter change in a project that is 0-for-7 on those, and
it lowers the average quality of what gets traded.

**And the arithmetic that settles it: expectancy is −0.30/trade. More trades at
negative expectancy is more loss, not more edge.** 102 × −0.30 = −30.69.
Doubling the rate doubles the bleed. Frequency is only worth buying once
expectancy is positive.

The legitimate worry underneath it is real: **at ~2.4 entries/session, the
50-trade promotion gate takes about a month of clean sessions.** That is slow.
But the answer is to test ideas in the sim and the replay, which generate
hundreds of trades in minutes, and keep live for measuring what only live can
measure (E3). Buying sample with real money at negative expectancy is the most
expensive way to learn anything.

If frequency is still the goal after all that, **E8 is where the leverage
actually is** — 122 rejected breakouts, 53% of all signals, one untested rule.
Not the screen.

## E10 — VWAP exit  ⛔ REFUTED 2026-10-01, twice over. Do not re-propose.

**Opened 2026-09-30.** Full working in `RESEARCH-2026-09-30.md`.

**REFUTED ON TWO INDEPENDENT GROUNDS:**
1. **Its own mechanism fails.** Median distance from entry to session VWAP is
   **1.75%**; **58% of trades sit more than 1.5% from it**. VWAP is a LOOSER
   stop than the existing 1.5%, so it cannot protect the dead band — which was
   the entire reason for proposing it.
2. **The validated harness agrees.** `stop 1.5 + VWAP` **−31.66** touch;
   `VWAP only` **−120.42**. Both far worse than live.

The reasoning below is kept because the dead-band observation that motivated it
is still correct — it is the proposed *fix* that failed.

---

**The observation.** Classified by how they ended, all 100 trades:

| exit | n | total | per trade | win rate |
|---|---|---|---|---|
| stop | 42 | −595.94 | −14.19 | 0% |
| eod flatten | 22 | +332.49 | +15.11 | 68.2% |
| **trail** | **18** | **+153.94** | **+8.55** | **94.4%** |
| time stop | 18 | +86.91 | +4.83 | 66.7% |

**The trail is the best exit the bot has — and it reaches 18 trades in 100.**
It cannot arm until the position is up ~1.25%, and 38% of trades peaked inside
that dead band and lost −251.53 between them.

**Mechanism (criterion 5).** An exit armed from the moment of entry can protect
a trade that peaks at +0.6%; the current trail structurally cannot. VWAP is the
form of it used in Zarattini & Barbon's intraday-momentum paper — *"if the
price crosses below VWAP (for a long position), close the position"* — and it
is volatility-adaptive with **no tunable parameter**, which matters in a
project that is 0-for-7 on tuned parameters.

**It is free.** Alpaca's bar model carries `vwap`; `broker.get_minute_bars`
already fetches it on every call and the repo has no reference to it anywhere.
No extra API call, no new data dependency.

**What must be established before anything ships**, in this order:
1. `replay.py --validate` reproduces booked P&L. If not, stop.
2. `vwap_dist_at_entry_pct` shows whether VWAP is tighter or looser than 1.5%
   on this universe. **Nobody knows yet** — on a +50% gapper it may be looser.
3. The replay grid shows VWAP-exit beating the live rules under **both** fill
   assumptions. A result that only works under `touch` is a wick artifact.

**Pre-registered criterion, stated before any sample:** over ≥50 paper trades,
expectancy improvement **≥ +0.30/trade** against the restated 102-trade
baseline of −0.30, AND the sign survives dropping the top 2 trades, AND max
drawdown does not exceed the baseline 237.59. Miss any one → revert, no
re-tuning.

**Do not read this as "remove the trail".** That was this session's first
hypothesis and the data refuted it: the trail gives back 1.25 pp against the
EOD flatten's 2.13 pp, so removing it pushes its 18 winners into a worse exit.

## E11 — Execution cost now exceeds the entire trial loss  ★ measurement

Stop fills on the 1.5% stop realise **−1.58% at the median**, 0.08 pp past the
stop. Excluding the known AHCO ops bug: **$18.73 across 28 stop-outs**. Robust —
dropping the two worst barely moves it.

With E1's entry slippage ($30.60 over 98 trades), total execution cost is
**$49.33 against a whole-trial P&L of −$22.60. Gross of execution the bot is
roughly +$27.**

**Honest limit:** median entry spread is 0.118% and the median stop overshoot
is 0.08 pp — *smaller than the spread*. Most of this is crossing the spread,
not a bug. What it establishes is that the real stop is **1.58%, not 1.50%**,
and that the payoff-ratio target below is measured on already-slipped numbers.

## Tested and rejected 2026-09-30 — do not re-propose without new data

| hypothesis | headline | what killed it |
|---|---|---|
| the bot's own score predicts P&L | +1.27 vs −1.61 | drop top 2 → −0.40 |
| gap 5–10% sweet spot | +5.68/trade (n=16) | drop top 2 → −1.82 |
| first-bar confirms beat retries | +1.89 vs −1.16 | drop top 2 → −3.13 |
| 4+ trades/day is the damage | −4.62/trade (n=42) | only 8 distinct days — nested subsets |
| Monday is different | +10.72, t=+1.97 | no mechanism; 5 comparisons |
| crypto / leveraged worse | crypto −1.79 | all die ex-top2 |
| volatility-scaled stop lifts P&L | tightest bucket −6.11 | split-half flips sign |

**The lesson, which is worth more than any of them:** every selection-side
finding is a *subset* finding — 100 trades cut into buckets of 15–25, where two
trades decide the answer. An exit rule is not a subset; it applies to all 100 at
once. That is why the give-back finding strengthened under drop-top-2 (+67 →
+118) while all seven of these collapsed. **This dataset can speak to exits. It
cannot speak to selection.**

## E12 — stop 1.0  ★ FIRST candidate ever to survive the robustness battery

**Opened 2026-10-01.** Full working in `RESEARCH-2026-09-30.md` and
`exit-structure` memory. **No config change is proposed yet.** Read the design
below — this is testable with nothing deployed at all.

### What survived

Validated harness (replay reproduces booked P&L to **+0.64 across 38 trades**;
27 of 38 per-trade within 0.1%). Deltas vs LIVE after dropping the best trades:

| candidate | mode | full | ex-top2 | ex-top3 | halves |
|---|---|---|---|---|---|
| NO TRAIL | touch | +63.73 | **−9.49** | **−36.47** | same sign |
| NO TRAIL | close | +79.81 | +6.58 | **−18.65** | same sign |
| stop 1.0, NO TRAIL | touch | +89.72 | +16.50 | **−10.48** | same sign |
| trail 0.75 | touch | +26.32 | +26.35 | +23.93 | **SIGN FLIPS** |
| **stop 1.0 + trail** | touch | **+17.80** | **+17.81** | **+17.80** | same sign |
| **stop 1.0 + trail** | close | **+20.63** | **+20.64** | **+20.63** | same sign |

**The mechanism (gate criterion 5).** Dropping the three best trades moves the
delta by **0.01**. The gain has nothing to do with winners — it comes entirely
from **losing less on the trades that fail**. A tighter stop cuts the loser
sooner; the winners never go near either stop, so they are untouched. That can
be restated without reference to the backtest, which is the test that matters.

### Why it is NOT a result yet

n=38. **In-sample with no holdout left** — those 38 are every current-regime
trade in existence. t = +0.27 (touch) / +0.67 (close), nowhere near
significance. One of five candidates, so multiple comparisons apply. And it
pulls against the volatility work: a 1.0% stop sits inside a *median 0.23* of
the opening range, tighter still, and the stop-out rate will rise.

### The design: test it with NO config change at all

The obvious move — set paper to stop 1.0 and leave live at 1.5 — gives a clean
concurrent control but **destroys E3**, which needs the two instances identical.
That trade-off is avoidable.

**The harness validates. So it can compute, for every NEW trade as it happens,
what a 1.0% stop would have done — without changing anything.** Paper and live
stay identical, E3 keeps accumulating, nothing is deployed, and the evidence
builds on data that did not exist when the hypothesis was formed. That is a
genuine out-of-sample test, which is the one thing this project has never had.

**2026-10-02 onward is the holdout.** It is untouched by construction. Run
`python -u replay.py --robust --since 2026-10-02` once enough trades exist.

### Pre-registered criteria — written 2026-10-01, BEFORE any holdout data

Over **≥ 30 trades dated 2026-10-02 or later**, all of:

1. `stop 1.0 + trail` beats `stop 1.5 + trail` in **touch** mode.
2. It also beats it in **close** mode (not a wick artifact).
3. The advantage survives **ex-top2 AND ex-top3**.
4. The per-trade delta is **≥ +0.25/trade** — deliberately far below the
   +0.47 (touch) / +0.54 (close) the in-sample window shows, because in-sample
   results shrink and this project's shrink to zero.
5. Sign does not flip between the first and second half of the holdout.

**Miss any one → rejected, no re-tuning, no re-running with a different
threshold.** Clearing all five earns a **paper** config change with its own
50-trade sample — not a live one.

**Honest note on timing:** at the current ~1–2 trades/session this needs
roughly 20–30 sessions. There is no way to make that faster that does not
involve lowering the bar.

## ⛔ NO TRAIL — REJECTED 2026-10-01. Argued for twice, killed twice.

The source paper uses no trailing stop and calls profit targets detrimental, so
removing the trail was proposed on 09-30, withdrawn the same day, and revived
by the validated harness at **+63.73**. It then **died on concentration**:
+63.73 → −9.49 → −36.47, reversing in both fill modes by ex-top3. SNXX 09-18
alone ran +11.6%.

**The 09-30 withdrawal was right for the WRONG reason.** It argued the trail
gives back 1.25pp vs the EOD flatten's 2.13pp — **selection bias**: the trail
only fires on trades already winning. Trail exits average MFE **+4.58%**, EOD
exits **+1.14%**. Different populations, conditioned on the outcome. The real
reason it fails is concentration, and only a validated harness could show it.

**Do not re-propose removing the trail.**

## Log

| date | change | instance | n | result | decision |
|---|---|---|---|---|---|
| 2026-09-27 | (baseline, superseded) | paper | 100 | −0.23/trade | restated 09-30 |
| | H1 news-confirmed bias | sim first | — | not yet run | see PREREG-2026-09-22 |
| | H2 opening-range stop | sim first | — | not yet run | see PREREG-2026-09-22 |
| 2026-09-29 | (none — E3 overlap begins) | both | 1 | SOXL both sides; live +0.03pp vs paper | keep paper == live |
| 2026-09-30 | (none) | live | 0 | no entry; QURE rejected on confirmation, relvol 2.19x | E8 opened |
| 2026-09-30 | (research only) | — | — | 7 selection hypotheses tested, 7 rejected | E10/E11 opened |
| 2026-10-01 | (none) | both | 0 | 2 targets, neither broke out; both clean | — |
| 2026-10-01 | replay.py VALIDATED | — | 38 | +0.64 vs actual; 27/38 within 0.1% | harness usable |
| 2026-10-01 | NO TRAIL | sim | 38 | +63.73 → −9.49 ex-top2 | **REJECTED (10th)** |
| 2026-10-01 | VWAP exit | sim | 38 | −31.66 touch | **REJECTED** |
| 2026-10-01 | stop 1.0 + trail | sim | 38 | +17.80 / +17.81 / +17.80 | **E12 — holdout opens 10-02** |
| 2026-10-12 | (none) | both | 3 / 3 | WFF halt-gap trail fill: paper +183.42, live +93.20; ex-top2 paper is −229.68 | see RESEARCH-2026-10-12 |
| 2026-10-12 | E3 interim | both | 12 | median live−paper +0.03pp; thin names diverge (SDEV −0.76, WFF −4.99) | continue to ~20 |
| 2026-10-12 | E12 holdout | paper | 22 | not evaluated (needs 30) | wait |
| 2026-10-12 | H1 require news | sim 2025-H2 | 177 vs 497 | exp +1.04 vs +0.65, stop 40.7% vs 43.5%, DD 121.66 vs 194.51 — but t +1.13 < 2.0 | **REJECTED** (criterion 2). No re-cut. |
| 2026-10-12 | H2 range stop | sim 2025-H2 | 492 vs 497 | exp −0.04 vs +0.65; stop rate halved (21.3% vs 43.5%) but each stop cost more | **REJECTED** (criteria 1, 2) |
| 2026-10-12 | E13 proposed | — | — | IEX blindness on thin/halted names, logging only | needs approval + deploy |
| 2026-10-25 | replay.py re-validated | — | 34 | ref-anchoring fix: +11.33 → +2.99 diff, exit type 34/34, median err 0.02% | harness trustworthy |
| 2026-10-25 | E5 scale-out | sim | 34 | worse than live at every trigger in `close` | **REJECTED** — do not build partial-exit path |
| 2026-10-25 | E6 BE at +0.75 | sim | 34 | passed every robustness gate, then BE +0.25 beat it in both modes | **ABORTED** on its own pre-committed rule |
| 2026-10-25 | stop/trail/BE grids | sim | 34 | every family's optimum sat at the smallest value tested | **ARTIFACT** — 1-min bar resolution; see ANALYSIS-2026-10-25 §4 |
| 2026-10-26 | (none) | paper | 4 | −45.00 (−2.17%), 0 winners, no TRAIL lines at all | — |
| 2026-10-26 | (none) | live | 3 | −12.01 (−0.80%), CRWV trail exit +0.83; SOXS correctly rejected on confirmation | — |
| 2026-10-26 | E3 — entry timing | both | 3 | CRWV 72s apart → +0.195% vs −1.626%; RHI 12.5 MIN apart; live better on all 3 | **E3 assumption falsified** — see E3 §2026-10-26 |
| 2026-10-26 | E12 graded (10-15 run, n=30) | sim | 30 | +51.93 both modes, unchanged ex-top2/3, halves +23.10/+28.83 | **PASSES 5/5** — but harness biased toward tight stops (ANALYSIS-2026-10-25 §4); earns PAPER trial only. Draft: E12-PAPER-TRIAL-DRAFT.md |
| 2026-10-26 | E3 closed | both | 37 | median live−paper +0.04pp, mean −0.07pp | paper freed for E12 trial |
| 2026-10-27 | (none) | paper | 1 | XP stop −10.92 (−1.57%); EAF rejected unconfirmed, +5.7% after | — |
| 2026-10-27 | (none) | live | 1 | XP stop −6.75 (−1.51%), entered 3 s from paper | — |
| 2026-10-27 | E14 edge scan | sim | 7,831 | 11 pre-registered tests, 0 pass; live exit gross ≈ 0 | **NO EDGE** in the entry: see E14 |
| 2026-10-27 | news attribution fix | code | — | shared `_seen_ids` sent IREN headlines to CRWV (paper) / WULF (live) | **FIXED** f26338d, deploy to both together |
| 2026-10-27 | E15 index ETFs | sim | 2,698 sessions | H1–H4 on SPY, 0 of 4 pass; H3 on QQQ +11.8 bp holdout, post-hoc | **0/4 scored**; QQQ lead needs forward data |
| 2026-10-27 | E16 drafted | shadow + paper | — | QQQ noise-area forward test; gate fixed before 2026-10-28 | **DRAFT**, awaiting Ethan's approval |


## E14 — Edge scan: does the entry signal have drift at all?  ⛔ NO. Read this before opening E15.

**Run 2026-10-27.** Pre-registered in `PREREG-EDGE-SCAN-2026-10-27.md`, summarised in
`ANALYSIS-2026-10-27.md`. 7,831 signals over 278 sessions from `.sweep_cache/`, with a
discovery period (2025 H2) and a holdout (2026).

| | result |
|---|---|
| long breakout, drift to 15:50 | −0.206% [−0.492, +0.077]; +5 min and +30 min significantly negative |
| live exit, net | −0.091% [−0.177, +0.000], **gross ≈ 0.009%** |
| buy the downside break (H-R) | FAIL, −0.15% |
| fade either break (shorts) | ≈ 0 after costs |
| 7 selection splits (C1–C7) | 0 pass |
| SIP opening relvol (H-V) | FAIL, inverted: high relvol fades |
| buy gap-downs at the open (H-G, gap reversal) | FAIL, sign flips; deep gap-downs keep falling |

**Standing conclusion:** on the gap-screened universe, at 1-min resolution, the opening-range
break carries no tradeable directional information in either direction. Exits can't add
edge that isn't in the entry (optional stopping), which is why E5/E6/E10/E12 never
converged.

**New rule, the drift-first gate:** any new ENTRY idea gets added as a `side` in
`edge_scan._signal_row` and must show positive drift in both periods before it earns a PREREG.
Five minutes of compute replaces five weeks of paper trading.


## E15: Index ETFs, four published signal families  ⛔ 0/4 scored, one post-hoc lead (QQQ)

**Run 2026-10-27.** Pre-registered in `PREREG-INDEX-2026-10-27.md`, code `index_scan.py`.
SIP 1-min bars for SPY/QQQ/IWM/DIA, 2016-01 → 2026-10 (2,698 full sessions). Discovery
2016–2021, holdout 2022–2026, scored on SPY at 0.5 bp per side.

| family | SPY holdout (net bp) | verdict |
|---|---|---|
| H1 last-half-hour momentum (Gao 2018, Baltussen 2021) | −1.39, negative on all 4 ETFs | FAIL, gone post-publication |
| H2 overnight drift | +1.94 [−1.67, +5.68] | FAIL, market beta not anomaly |
| H3 noise-area momentum (Zarattini/Aziz/Barbon 2024) | +3.54 [−1.81, +9.05] | FAIL |
| H4 turn of month | +1.51 per day, CI ±15 | FAIL, no power |

**The lead (post-hoc, not a pass):** H3 on **QQQ** is +11.79 bp per active session
[+5.18, +18.81] in the holdout, positive every year 2017–2026, both legs positive, robust to
2 bp per side, holdout Sharpe 1.5 unlevered. On **IWM** the same rule loses significantly
(−8.82), which matches E14: momentum persists in large-cap growth and fades in small caps.
Because QQQ was picked after looking, the only valid test is forward data, about 1.8 years
for statistical proof. A paper trial checks mechanics only and needs its own PREREG.


## E16: QQQ noise-area momentum, forward test  🟢 RUNNING, forward clock from 2026-10-28

Pre-registered in `PREREG-QQQ-NOISE-PAPER-2026-10-27.md`. Rules are frozen as E15 H3 on QQQ.
**Evidence** is the shadow record: the frozen rule replayed nightly on SIP bars
(`index_scan.py forward`). **Costs and mechanics** come from `noise_bot.py` on a second paper
account (`/opt/orb-noise`, installed by `deploy/setup-noise.sh`, never imports the ORB bot). Futility looks at 120 and 180 active sessions; the only
passing look is at 250 (~Jul 2028): t ≥ 1.645, long-only > 0, and positive without the top 5.
P(pass) ≈ 63% if the holdout-sized edge is real, ≤ 5% if there is none.
