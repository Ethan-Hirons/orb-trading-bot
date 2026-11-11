# ORB Trading Bot

An automated **Opening Range Breakout (ORB)** trading system for US equities and
ETFs, built on the [Alpaca](https://alpaca.markets) brokerage API. It screens for
movers before the open, measures each name's opening range, enters breakouts as
bracket orders with a server-side trailing stop, and halts itself on a daily
profit or loss limit. It never holds overnight and it will not trade on a day it
was not explicitly armed.

Alongside the bot, this repository is a research record. Every strategy change
was pre-registered before the data was pulled, and most of them failed. The
section below is the honest summary of what the research actually found, because
that is the more useful half of the project.

> Performance figures here are from paper trading and are not a track record.

> I used Claude to help with parts of this project, but not all of it. The strategy, the research and the decisions are mine.

---

## What the research found

**The entry signal has no drift.** In October 2026 I wrote `edge_scan.py`, which
turns the existing sweep cache into a drift test in about five minutes. Across
**7,831 signals over 278 sessions** (July 2025 to August 2026), long breakout
drift to 15:50 was **−0.206%** [−0.492, +0.077], and the +5 and +30 minute marks
were significantly negative. Fading the breakout instead is roughly +0.006% after
the 0.10% round trip. **Eleven pre-registered tests, zero passes** — five exit
rules, the reversed signal, seven selection filters, the source paper's own
opening relative-volume filter (which came out inverted), and a gap-reversal
family.

That result closed four months of exit tuning as a source of edge. Stops and
trails reshape the distribution of outcomes but cannot move its mean, and the
earlier samples were too small to show it. It also produced the most useful
change of the year: a **drift-first gate**. Any new entry idea is now added as a
signal row in `edge_scan.py` and tested for drift before any strategy code or
pre-registration is written. Ideas now die in five minutes instead of five weeks.

**Earlier out-of-sample work, same pattern.** The August 2026 candidate ruleset
was rejected **0 of 6** on its pre-committed criteria across 395 and 534 holdout
trades, with the holdout reversing the in-sample conclusion about entry windows.

**The simulator was flattering me.** Validated against live fills, the backtest
**overstated trade count by about 3.3x** on a matched window, which is why no
fine parameter question is adjudicated by the simulator alone. Separately, a
paired comparison of 31 to 37 matched paper and live trades found **no paper to
live fill penalty** (median difference +0.03 pp, 95% CI about ±0.09 pp),
disproving my own prior that paper fills are optimistic.

**Bar resolution limits what can be concluded.** Stop width, trail width and
breakeven all placed their optimum at the smallest grid cell tested, and the
optimum moved every time the grid was extended downward. Three families cannot
share an optimum by coincidence, so they share an artifact: one-minute bars miss
triggers inside the noise they average away. Replay grid cells below the live
settings are therefore not cited as evidence anywhere in this repo.

**Where it stands.** The live instance is approximately zero expectancy before
costs. It is kept running for the engineering and operations experience, not on
an expectation of profit. Current work moved to a different universe and signal
family rather than new parameters: a QQQ noise-area rule (E16) running a
pre-registered forward test with futility stops and a stated power calculation,
a mechanism test on six unseen ETFs that came back **not supported** at p = 0.056
against a 0.05 line, and a monthly trend-following family that **failed** its
pre-registered return bar despite cutting maximum drawdown from −37.5% to −11.7%.

The dated notes are the full record. `PREREG-` files were committed before the
data was fetched, `ANALYSIS-` files report what happened, and the git history
shows each pair in order.

| Note | What it covers |
|---|---|
| `ANALYSIS-2026-10-27.md` | The edge scan. The entry signal has no drift. |
| `PREREG-EDGE-SCAN-2026-10-27.md` | Its pre-registration |
| `ANALYSIS-2026-10-25.md` | Bar resolution artifacts in the replay grids |
| `ANALYSIS-2026-11-01-mechanism.md` | E17 mechanism test on unseen ETFs, not supported |
| `ANALYSIS-2026-11-01-trend.md` | E18 trend following, both variants failed |
| `RUNBOOK-2026-08-23.md` | The 0 of 6 out-of-sample rejection |
| `EXPERIMENTS.md` | Index of every experiment and its status |

---

## What it does

Each trading day, if it has been armed, the bot:

1. **Scans before the open.** Pulls Alpaca's most-active and top-mover lists,
   filters to a price band and a minimum gap, reads recent news per name, scores
   sentiment, and picks the day's 1 to 5 targets, each tagged with a directional
   bias.
2. Waits for the US open (9:30 ET).
3. Measures each target's **opening range** over the first N minutes (default 15).
4. Watches for a **breakout** past the range, entering only in the direction the
   news and gap point when there is a clear one.
5. Places a **bracket order** on entry, so every trade has defined exits up front.
   The stop is placed as a stop-limit so a thin name cannot fill far past it.
6. **Trails the stop server-side.** Once a position's peak clears breakeven plus
   the trail distance, the bot cancels the bracket legs and hands the exit to a
   native Alpaca trailing-stop order, which tracks the high-water mark tick by
   tick rather than on a poll interval.
7. **Re-reads the news intraday.** Fresh headlines update a target's bias before
   entry, and strongly adverse news on an open position closes it early.
8. **Halts on the daily limit.** Crossing the daily profit target or max loss
   stops new trades and flattens by default.
9. **Flattens before the close.** It never holds overnight.
10. Writes an end-of-day summary with win rate, expectancy and max drawdown.
    `python report.py` prints the running trial stats.

### On the news scan

The sentiment score is a transparent keyword heuristic, readable and editable in
`orb_bot/news.py`, not a language model. It flags clearly good and bad headlines
and steers direction, but it is a filter and tie-breaker rather than an edge.
Headline relevance across multiple tickers is a known open problem: an article
naming two companies can set a bias on the wrong one.

---

## Quick start

Requires Python 3.10 or later.

```bash
pip install -r requirements.txt
cp .env.example .env        # copy .env.example .env on Windows
```

Fill in paper keys from the Alpaca dashboard. Paper keys are separate from live
keys, and `.env` is already in `.gitignore`.

```
ALPACA_API_KEY=your_paper_key
ALPACA_SECRET_KEY=your_paper_secret
ALPACA_PAPER=true
```

Then each morning:

```bash
python arm.py        # grant permission for today
python run.py        # start the bot
```

Arming is per calendar day. Until `arm.py` runs, the bot refuses to open any
position, so it cannot silently keep trading on a day nobody approved.

```bash
python arm.py --status   # armed state, today's PnL basis, halt state
python arm.py --off      # revoke permission, bot idles
python report.py         # running trial stats
```

`run.py` logs to the console and to `logs/orb_<date>.log`, and exits on its own
when a breaker trips or near the close.

---

## Configuration (`config.yaml`)

Everything is tunable without touching code. Current values, which differ from
the original defaults because of the sweep work recorded above:

| Setting | Meaning | Current |
|---|---|---|
| `screener.enabled` | Run the pre-market movers scan | true |
| `screener.min_price` / `max_price` | Price band for candidates | 5 / 400 |
| `screener.min_abs_gap_pct` | Minimum move to qualify | 2.0 |
| `screener.max_abs_gap_pct` | Skip extreme gappers, 0 disables | 75.0 |
| `news.enabled` | Read and score recent headlines | true |
| `news.min_sentiment_for_bias` | Score magnitude that sets a bias | 0.20 |
| `news.rescan_interval_seconds` | Intraday news check interval, 0 disables | 60 |
| `news.exit_on_adverse` | Close on strongly adverse fresh news | true |
| `selection.max_targets` | Names traded per day | 5 |
| `strategy.opening_range_minutes` | Opening range length | 15 |
| `strategy.entry_window_minutes` | Window for new entries after the range | 90 |
| `strategy.allow_shorts` | Allow shorts. Requires a margin account live | false |
| `strategy.respect_news_bias` | Enter only in the news-implied direction | true |
| `strategy.max_trades_per_day` | Cap on new trades per day | 5 |
| `exits.stop_pct` | Stop distance from entry | 1.5 |
| `exits.trail_pct` | Server-side trailing stop distance below the peak | 1.25 |
| `exits.tp_min_pct` / `tp_max_pct` | Take-profit, effectively off | 25.0 / 25.0 |
| `exits.stop_limit` | Stop-limit stops to cap slippage | true |
| `exits.stop_limit_offset_pct` | Worst fill allowed beyond the stop | 0.5 |
| `exits.breakeven_trigger_pct` | Breakeven stop, off as redundant with the trail | 0 |
| `exits.max_hold_minutes` | Time stop, off | 0 |
| `sizing.risk_per_trade_pct` | Equity risked per trade, entry to stop | 1.0 |
| `sizing.max_position_notional` | Hard dollar cap on one position | 700 |
| `risk.daily_profit_target_pct` | Halt for the day at this gain | 6.0 |
| `risk.daily_max_loss_pct` | Halt for the day at this loss | 5.0 |
| `risk.flatten_on_breaker` | Flatten when a breaker trips | true |
| `runtime.data_feed` | Market data feed, free accounts use iex | iex |

The take-profit is set to an unreachable ceiling on purpose. Sweeps showed that
capping winners costs roughly a third of expectancy once the trail handles the
downside, so the trail or the end-of-day flatten is the real upside exit.

### Position sizing

Sizing is risk-based: share count is computed so that a stop-out loses about
`risk_per_trade_pct` of equity. Because the stop is tight at 1.5%, the notional
cap binds first on a small account, which puts effective risk nearer 0.5% of
equity per trade. The cap exists so no single position dominates a small account.

---

## Going live

The code is identical for paper and live. Only the keys and the `ALPACA_PAPER`
flag change, which is deliberate: what gets tested is what runs.

1. Fund the live account and generate live keys.
2. Set the live keys in `.env` and `ALPACA_PAPER=false`.
3. Lower risk for the first live sessions, for example `risk_per_trade_pct: 0.5`
   and `max_trades_per_day: 1`.
4. Shorting requires a margin account. `allow_shorts` is currently false, so the
   bot is long-only until that is changed deliberately.

---

## Tests

The strategy and risk math have a self-contained suite with no Alpaca calls and
no keys required:

```bash
python tests/test_logic.py
```

It covers opening-range calculation, long and short breakout detection, take
profit and stop placement including stop-limit prices, position sizing with the
notional cap and the too-small-to-trade case, the daily circuit breaker,
sentiment scoring, the adverse-news exit decision, screener and selection
filters, per-symbol news deduplication, and the trade pairing and statistics
behind the end-of-day report.

---

## Safety

- **Per-trade stop at the broker.** Every entry is a bracket order, so the stop
  triggers even if the bot or the machine goes offline. Stop-limit by default,
  capping the exit about 0.5% past the trigger.
- **Server-side trail.** Past breakeven plus the trail, exits move to a native
  Alpaca trailing-stop order rather than a polled client-side ratchet. The
  client-side version failed silently in v1.8 and was replaced in v1.11.
- **Adverse-news exit.** Strongly adverse fresh news closes a position early.
- **Crash resilience.** Transient API errors back off and retry. Persistent
  errors flatten and stop.
- **Daily breakers.** Equity is checked every cycle against the daily profit and
  loss limits, halting and flattening by default.
- **No overnight risk.** Everything closes before the bell.
- **Permission gate.** No trading on any day that was not explicitly armed.

---

## Known open issues

- `_reanchor_exits` re-prices the bracket only when drift is at least 0.001, so
  98 of 158 trades never re-anchored and their stops sat on the pre-trade
  reference. Realized stops span −1.405% to −1.590% against −1.500% configured.
  This is a correctness and reproducibility problem, not a P&L explanation.
- Buyout targets pinned at a deal price produce a near-zero opening range, and
  boilerplate law-firm press releases score as strongly negative. Two of 3,871
  signals, so hygiene rather than edge.
- Rejected-breakout outcomes are logged as events but discarded as outcomes, so
  the counterfactual on 170 rejections across 64 sessions is unavailable.

---

## Risk disclaimer

This is software for its operator's own automated trading decisions, not
financial advice. Trading involves real risk of loss. Breakouts fail, and small
accounts feel costs and slippage more. Test on paper, start live only with money
you can afford to lose, and review the logs. The account holder is responsible
for every trade placed under their keys.
