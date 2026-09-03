# ORB Trading Bot

An automated **Opening Range Breakout (ORB)** trading bot for US stocks/ETFs,
built on the [Alpaca](https://alpaca.markets) brokerage API. It trades on its
own once you arm it for the day, and **stops automatically** when it hits your
daily profit target or your daily max loss.

Start on **paper money** (free, fake funds). Only switch to real money once
you're happy with how it performs.

> **About this repository.** This is a research and engineering project run on a
> small paper-trading account. The dated notes in this repo (`REVIEW-`, `UPDATE-`,
> `TUNING-ANALYSIS-`, `VALIDATION-GO-NOGO`) are the working record: what was
> changed, why, what the evidence actually supported, and where the results were
> too thin to conclude anything. Performance figures are from paper trading and
> are not a track record.

---

## What it does

Each trading day, **if you have armed it**, the bot:

1. **Scans before the open.** It pulls Alpaca's most-active and top-mover lists
   to find **volatile stocks that are actually moving**, filters them to your
   price band, reads each one's **recent news**, scores the sentiment, and picks
   the day's **1–5 best targets** — each tagged with a long or short bias.
2. Waits for the US market open (9:30 AM ET).
3. For each target, measures the **opening range** — the high and low over the
   first N minutes (default 15).
4. Watches for a **breakout** past the range, and only enters **in the direction
   the news/gap points** (when there's a clear one).
5. On entry it places a **bracket order** — entry plus an automatic
   **take-profit and stop-loss** — so every trade has defined exits up front.
   The take-profit is **scaled 3–5% by the stock's volatility**; the stop is
   **~3%**, placed as a **stop-limit** so a thin stock can't fill you far past
   your stop (slippage is capped at ~0.5%).
6. **Keeps reading the news all day.** Every 60 seconds (configurable) it
   checks for *fresh* headlines on the day's targets and any open positions.
   Fresh news updates a target's long/short bias before entry, and if strong
   news breaks **against a position you're holding**, the bot closes it early
   instead of waiting for the stop.
7. Tracks your **daily profit/loss** all day and **stops trading for the day**
   the moment you hit the profit target or max loss (and by default closes
   everything).
8. **Flattens all positions before the close.** It never holds overnight.
9. After the close it writes an **end-of-day summary** — each completed trade
   plus your running **win rate, expectancy, and max drawdown** — to the log
   and to `state/trade_history.csv`. Run `python report.py` anytime to see the
   trial stats.

### An honest word on the news scan

The sentiment score is a transparent keyword heuristic (you can read and edit
the word lists in `orb_bot/news.py`), not an AI analyst. It reliably flags
clearly good/bad headlines and steers direction, but it is a **filter and
tie-breaker, not a profit guarantee**. Likewise, the screener and news feeds may
require a paid Alpaca data plan — if they're unavailable, the bot automatically
falls back to your `fallback_symbols` watchlist and trades those with no bias.

## The daily permission switch

You asked to grant permission each morning and then let it trade without asking.
That's exactly the model:

```
python arm.py        # each morning: "yes, trade today"
python run.py        # start the bot; it trades on its own all day
```

Until you run `arm.py`, the bot will **refuse to open any trades**. Arming is
per-calendar-day, so it can never silently keep trading on a day you didn't
approve.

---

## One-time setup

### 1. Create an Alpaca account and get PAPER keys

- Sign up at <https://alpaca.markets> (as a US-based student this is the right
  fit; you can use it from the EU over the summer too).
- In the dashboard, switch to **Paper Trading**, open **API Keys**, and
  generate a key + secret. Paper keys are separate from live keys.

### 2. Install Python and dependencies

You need Python 3.10+ (for `zoneinfo`). From this project folder:

```bash
pip install -r requirements.txt
```

### 3. Add your keys

Copy the template and fill it in:

```bash
copy .env.example .env      # Windows
# cp .env.example .env      # macOS/Linux
```

Edit `.env`:

```
ALPACA_API_KEY=your_paper_key
ALPACA_SECRET_KEY=your_paper_secret
ALPACA_PAPER=true
```

Keep `.env` private. It's already in `.gitignore`.

---

## Daily use

Run these each morning (any time before or around the US open):

```bash
python arm.py        # grant permission for today
python run.py        # start trading
```

Leave `run.py` running through the session. It logs to the console and to
`logs/orb_<date>.log`. It exits on its own when a circuit breaker trips or near
the close.

Useful commands:

```bash
python arm.py --status   # show whether it's armed, today's PnL basis, halt state
python arm.py --off      # revoke permission (bot will idle, no new trades)
python report.py         # print the running trial stats (win rate, expectancy...)
```

Because the US open is 3:30 PM in EU summer time, you can simply run both
commands in the early afternoon and let it work.

---

## Configuration (`config.yaml`)

Everything is tunable without touching code. The most important settings:

| Setting | Meaning | Default |
|---|---|---|
| `fallback_symbols` | Watchlist used only if the screener feed is unavailable | SPY, QQQ, AAPL, NVDA, TSLA |
| `screener.enabled` | Run the pre-market volatile-movers scan | true |
| `screener.min_price` / `max_price` | Price band for candidates (keeps them affordable) | 5 / 400 |
| `screener.min_abs_gap_pct` | Only keep names already moving at least this % | 2.0 |
| `screener.max_abs_gap_pct` | Skip extreme gappers (+200% names); 0 = off | 75.0 |
| `news.enabled` | Read recent headlines and score sentiment | true |
| `news.min_sentiment_for_bias` | \|score\| above this sets a long/short bias | 0.20 |
| `news.require_news` | Only trade names that have recent news | false |
| `news.min_articles` | Require at least N recent articles per target; 0 = off | 0 |
| `news.rescan_interval_seconds` | How often to check for fresh news intraday; 0 = off | 60 |
| `news.exit_on_adverse` | Close a position when fresh news turns strongly against it | true |
| `news.adverse_exit_score` | \|fresh score\| against the position needed to exit | 0.5 |
| `selection.max_targets` | How many names to trade per day (your 1–5) | 5 |
| `strategy.opening_range_minutes` | Length of the opening range | 15 |
| `strategy.entry_window_minutes` | How long after the OR new entries are allowed | 90 |
| `strategy.allow_shorts` | Allow short trades (needs a margin account live) | true |
| `strategy.respect_news_bias` | Only enter in the news-implied direction | true |
| `strategy.max_trades_per_day` | Cap on new trades opened per day | 5 |
| `exits.mode` | `percent` (3–5%/3%) or `range` (classic ORB brackets) | percent |
| `exits.stop_pct` | Stop-loss distance from entry | 3.0 |
| `exits.tp_min_pct` / `tp_max_pct` | Take-profit range, scaled by volatility | 3.0 / 5.0 |
| `exits.stop_limit` | Use stop-limit stops (cap slippage) instead of stop-market | true |
| `exits.stop_limit_offset_pct` | Worst fill allowed beyond the stop price | 0.5 |
| `sizing.risk_per_trade_pct` | % of equity risked per trade (entry→stop) | 1.0 |
| `sizing.max_position_notional` | Hard $ cap on one position | 700 |
| `risk.daily_profit_target_pct` | Stop for the day at this gain | 6.0 |
| `risk.daily_max_loss_pct` | Stop for the day at this loss | 5.0 |
| `risk.flatten_on_breaker` | Close all positions when a breaker trips | true |
| `runtime.data_feed` | Market data feed (free accounts use `iex`) | iex |

### Position sizing, in plain terms

Size is **risk-based**. With `risk_per_trade_pct: 1.0` on a small cash account, each
trade risks about €19 between entry and its 3% stop — the number of shares is
computed so that if the stop is hit, you lose roughly that amount. The
`max_position_notional` cap keeps any single trade from dominating a small
account, and with volatile movers priced under $400 you'll typically get a real
multi-share position rather than just 1 share.

### Why the daily limits changed

The daily breaker is now **+6% / −5%** (was 3%/2%). With up to 5 trades a day
each risking ~1% into a 3% stop, the old −2% would have halted the day after two
losers. If you'd rather trade less and cap the day tighter, lower
`risk.daily_max_loss_pct` and `selection.max_targets`.

---

## Going live (only after the paper trial)

When you've watched it for a week or two and you're satisfied:

1. In the Alpaca dashboard, fund your **live** account and generate
   **live** API keys.
2. Put the live keys in `.env` and set `ALPACA_PAPER=false`.
3. Consider lowering risk for the first live days (e.g.
   `risk_per_trade_pct: 0.5`, `max_trades_per_day: 1`).
4. Note: **shorting requires a margin account.** A small cash account can't go
   short — set `allow_shorts: false` if you're cash-only, and the bot will be
   long-only.

The code is identical for paper and live — only the keys and the `ALPACA_PAPER`
flag change. That's deliberate: what you test is what you run.

---

## Running unattended (later)

For the paper trial, running on your laptop during market hours is fine. Once
proven, you can move it to a cheap cloud VPS so your computer doesn't need to be
on. You'd also automate the morning `arm.py` with a scheduler (cron). I can set
that up for you when you're ready.

---

## Testing the logic

The strategy and risk math have a self-contained test suite (no Alpaca calls,
no keys needed):

```bash
python tests/test_logic.py
```

It checks opening-range calculation, long/short breakout detection,
take-profit/stop-loss placement (including stop-limit prices), position sizing
(including the notional cap and the "too small to trade" case), the profit/loss
circuit breaker, sentiment scoring and the adverse-news exit decision, screener
and selection filters, and the trade-history pairing and stats math behind the
end-of-day report.

---

## How the safety stops work

- **Per-trade stop-loss:** every entry is a bracket order with a stop attached
  at the broker, so it triggers even if the bot or your computer goes offline.
  It's a stop-limit by default: your exit price is capped ~0.5% past the stop,
  so a thin stock can't fill you far below your trigger. (The rare no-fill case
  is bounded — the bot force-flattens everything before the close.)
- **Adverse-news exit:** if strong fresh news breaks against a position, the
  bot closes it immediately rather than riding it to the stop.
- **Crash resilience:** transient API/network errors don't kill the session;
  the bot backs off and retries, and if errors persist it flattens and stops.
- **Daily max loss:** total equity is checked every cycle; crossing
  `daily_max_loss_pct` halts new trades and (by default) flattens everything.
- **Daily profit target:** same mechanism on the upside — lock in a good day.
- **No overnight risk:** all positions are closed before the market close.
- **Permission gate:** no trading on any day you didn't explicitly arm.

---

## ⚠️ Risk disclaimer

This is software for **your own** automated trading decisions, not financial
advice. Trading involves real risk of loss, and ORB is no exception — breakouts
fail, and small accounts feel costs and slippage more. Test thoroughly on paper,
start live with money you can afford to lose, and review the logs regularly. You
are responsible for every trade the bot places under your keys.
