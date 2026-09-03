"""Backtest the ORB strategy over historical Alpaca data.

Replays past trading days through the SAME strategy code the live bot uses
(opening range, bar-close confirmation, volume filter, news bias, TP/SL,
breakeven, EOD flatten), so config changes can be evaluated in minutes
instead of weeks.

Usage (from the project root, uses config.yaml + .env):
  python backtest.py --start 2026-05-01 --end 2026-07-11
  python backtest.py --start ... --end ... --symbols NOK,INTC,SOUN  # skip universe scan
  python backtest.py --start ... --end ... --no-news               # all biases "any"

Honest limitations (also printed with results):
  * The live pre-market screener uses most-actives/movers snapshots that
    don't exist historically. The backtest approximates the universe with
    daily-bar gaps + a previous-day volume floor. Overlap is good, not exact.
  * IEX minute bars (free feed) are thin; fills at bar open/stop prices are
    optimistic for illiquid names. Spread filtering is NOT simulated.
  * If a bar touches both stop and target, the STOP is assumed first
    (conservative). Intraday adverse-news exits are not simulated.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, replace as dc_replace
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from orb_bot.config import Config, load_config
from orb_bot.news import NewsSignal, score_text
from orb_bot.report import compute_stats
from orb_bot.screener import Candidate
from orb_bot.selection import build_targets
from orb_bot.strategy import (
    build_trade_plan,
    compute_opening_range,
    confirm_breakout,
    passes_volume_filter,
)

ET = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
OUT_FILE = "backtest_trades.csv"


# --------------------------------------------------------------------------
# Pure simulation (unit-testable, no API calls)
# --------------------------------------------------------------------------

@dataclass
class SimTrade:
    date: str
    symbol: str
    side: str
    qty: int
    entry_price: float
    exit_price: float
    exit_reason: str  # "tp" | "stop" | "breakeven" | "trail" | "time" | "eod"
    entry_time: str
    exit_time: str
    pnl: float
    pnl_pct: float
    bias: str
    or_pct: float
    gap_pct: float
    sentiment: float


def simulate_symbol_day(
    symbol: str,
    bars: list,
    cfg: Config,
    bias: str,
    equity: float,
    gap_pct: float = 0.0,
    sentiment: float = 0.0,
    trail_pct: float | None = None,
    max_hold_minutes: int | None = None,
    trail_arm: str = "entry",
    tp_off: bool = False,
    stop_pct_override: float | None = None,
    first_candle_dir: bool = False,
    entry_window_override: int | None = None,
) -> SimTrade | None:
    """Run one symbol through one day of minute bars with the live rules.

    bars: minute bars with tz-aware .timestamp, .open/.high/.low/.close/.volume.
    Returns the completed trade, or None if no (confirmed) entry happened.

    trail_pct: optional trailing stop (% off the post-entry favorable peak);
    None/0 = off (live bot has none — sweep-only what-if). max_hold_minutes:
    time stop override; None = use cfg.exits.max_hold_minutes (live default),
    0 = disable.

    trail_arm: "entry" (trail applies from the first bar — can cut a loser at
    ~-trail% before the -stop_pct% stop; the pre-2026-07-29 sim behavior) or
    "breakeven" (trail only applies once the trailed level is BETTER than the
    entry price — matches the live v1.8 `_check_trailing`, where the initial
    stop governs until the trade is up > ~trail%). The two differ exactly on
    sub-trail%-MFE losers, which is where most of the "entry" sweep edge came
    from — sweep both before trusting either.

    tp_off: disable the take-profit entirely (trail/stop/time/EOD are the only
    exits — uncapped upside; with a trail on, the TP fired 1/191 in-sim).

    first_candle_dir: when True, the direction is fixed by the opening-range
    candle itself (Zarattini/Barbon/Aziz 2024 §2): a bullish OR (close > open)
    permits ONLY a long, a bearish OR only a short, and a doji trades nothing —
    regardless of which side price later breaks. This is the paper's actual
    rule and is NOT what the live bot does; the live bot takes either side and
    filters by news sentiment, a mechanism that appears nowhere in the ORB
    literature. Sweep it before believing either.

    entry_window_override: minutes after the open past which no new entry is
    taken (None = cfg.strategy.entry_window_minutes, currently 180). In the
    live trial, entries at >= 60 min were 12 trades for -122.50 with no real
    winner; the paper's breakout fires off a 5-minute range, i.e. early.
    """
    strat, exits, sizing = cfg.strategy, cfg.exits, cfg.sizing
    # v1.12: per-symbol stop width (ATR-scaled sweeps). The live bot and every
    # sweep to date used ONE fixed % for every name, so a 1.5% stop sat inside
    # the noise on a 4%-opening-range mover and outside it on a calm one. The
    # ORB literature scales the stop to each stock's own volatility (10% of
    # 14-day ATR); this lets the sweep test that axis.
    if stop_pct_override is not None and stop_pct_override > 0:
        exits = dc_replace(exits, stop_pct=stop_pct_override)
    if not bars:
        return None
    day = bars[0].timestamp.astimezone(ET).date()
    open_dt = datetime.combine(day, MARKET_OPEN, tzinfo=ET)
    or_end = open_dt + timedelta(minutes=strat.opening_range_minutes)
    entry_window = (entry_window_override if entry_window_override is not None
                    else strat.entry_window_minutes)
    cutoff = open_dt + timedelta(minutes=entry_window)
    flatten_at = datetime.combine(day, MARKET_CLOSE, tzinfo=ET) - timedelta(
        minutes=cfg.runtime.flatten_before_close_minutes
    )

    def et(b):
        return b.timestamp.astimezone(ET)

    or_bars = [b for b in bars if open_dt <= et(b) < or_end]
    rng = compute_opening_range(symbol, or_bars)
    if rng is None:
        return None

    session = [b for b in bars if or_end <= et(b) < flatten_at]

    # v1.13: the paper's direction rule. The opening-range candle's own
    # body picks the side; price breaking the other edge is ignored.
    or_side = None
    if first_candle_dir:
        or_open, or_close = float(or_bars[0].open), float(or_bars[-1].close)
        if or_close > or_open:
            or_side = "long"
        elif or_close < or_open:
            or_side = "short"
        else:
            return None  # doji: no order placed

    # ---- find a confirmed entry ----
    plan = None
    entry_idx = None
    for i, bar in enumerate(session[:-1]):  # need a next bar to fill on
        if et(bar) >= cutoff:
            break
        close = float(bar.close)
        side = None
        if close > rng.high:
            side = "long"
        elif close < rng.low and strat.allow_shorts:
            side = "short"
        if side is None:
            continue
        if or_side is not None and side != or_side:
            continue
        if strat.respect_news_bias and bias != "any" and side != bias:
            continue
        if not confirm_breakout(rng, side, [bar], strat):
            continue
        if not passes_volume_filter(bar, or_bars, strat):
            continue
        p = build_trade_plan(rng, side, close, equity, exits, sizing)
        if p is None:
            continue
        # Fill on the next bar's open, respecting the marketable entry limit.
        nxt_open = float(session[i + 1].open)
        if p.entry_limit is not None:
            if side == "long" and nxt_open > p.entry_limit:
                continue  # gap through the limit: no fill, keep watching
            if side == "short" and nxt_open < p.entry_limit:
                continue
        plan, entry_idx, fill = p, i + 1, nxt_open
        break
    if plan is None:
        return None

    # Re-anchor exits to the actual fill (mirrors _adjust_exits_to_fill).
    sign = 1 if plan.side == "long" else -1
    tp = fill * (1 + sign * plan.tp_pct / 100.0)
    sl = fill * (1 - sign * plan.stop_pct / 100.0)
    be_trigger = (
        fill * (1 + sign * exits.breakeven_trigger_pct / 100.0)
        if exits.breakeven_trigger_pct > 0 else None
    )

    # ---- manage the position bar by bar ----
    hold_cap = (max_hold_minutes if max_hold_minutes is not None
                else getattr(exits, "max_hold_minutes", 0) or 0)
    time_limit = (
        et(session[entry_idx]) + timedelta(minutes=hold_cap) if hold_cap else None
    )
    be_armed = False
    peak = fill  # most favorable price seen on PRIOR bars (conservative)
    exit_price, exit_reason, exit_bar = None, None, None
    for bar in session[entry_idx:]:
        hi, lo = float(bar.high), float(bar.low)
        # Effective stop this bar = tightest of SL / breakeven / trailing.
        # be/peak only update after each bar's checks, so both apply from the
        # bar AFTER their trigger (conservative, matches the old be logic).
        stop, stop_reason = sl, "stop"
        if be_armed and (fill - stop) * sign > 0:
            stop, stop_reason = fill, "breakeven"
        if trail_pct:
            t_stop = peak * (1 - sign * trail_pct / 100.0)
            # "breakeven" arming (live v1.8): trail only once it beats entry.
            armed = trail_arm != "breakeven" or (t_stop - fill) * sign > 0
            if armed and (t_stop - stop) * sign > 0:
                stop, stop_reason = t_stop, "trail"
        if plan.side == "long":
            if lo <= stop:  # stop first when both touched (conservative)
                exit_price, exit_reason = stop, stop_reason
            elif not tp_off and hi >= tp:
                exit_price, exit_reason = tp, "tp"
            elif be_trigger is not None and hi >= be_trigger:
                be_armed = True
        else:
            if hi >= stop:
                exit_price, exit_reason = stop, stop_reason
            elif not tp_off and lo <= tp:
                exit_price, exit_reason = tp, "tp"
            elif be_trigger is not None and lo <= be_trigger:
                be_armed = True
        if exit_price is None and time_limit is not None and et(bar) >= time_limit:
            exit_price, exit_reason = float(bar.close), "time"
        if exit_price is not None:
            exit_bar = bar
            break
        peak = max(peak, hi) if plan.side == "long" else min(peak, lo)
    if exit_price is None:  # EOD flatten
        exit_bar = session[-1]
        exit_price, exit_reason = float(exit_bar.close), "eod"

    pnl = (exit_price - fill) * plan.qty * sign
    pnl_pct = (exit_price - fill) / fill * 100.0 * sign
    return SimTrade(
        date=day.isoformat(), symbol=symbol, side=plan.side, qty=plan.qty,
        entry_price=round(fill, 4), exit_price=round(exit_price, 4),
        exit_reason=exit_reason,
        entry_time=et(session[entry_idx]).strftime("%H:%M"),
        exit_time=et(exit_bar).strftime("%H:%M"),
        pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 3),
        bias=bias, or_pct=round(rng.size / fill * 100.0, 3),
        gap_pct=round(gap_pct, 2), sentiment=round(sentiment, 3),
    )


def pick_day_trades(
    day_trades: list[SimTrade], cfg: Config, start_equity: float
) -> list[SimTrade]:
    """Apply per-day limits across a day's simulated trades: max trades/day
    (by entry time) and the realized-PnL circuit breaker (approximate: only
    counts trades already exited before the next entry)."""
    chosen: list[SimTrade] = []
    realized = 0.0
    # v1.13 (2026-08-14) BUGFIX: 0 DISABLES the profit breaker — mirror
    # RiskManager.assess, which has guarded this since v1.12. This function did
    # not, so once config set daily_profit_target_pct: 0 on 2026-08-13 to turn
    # the profit breaker off, `realized >= up_lim` was `0.0 >= 0.0` on the very
    # first trade of every day and the sweep silently returned ZERO trades for
    # every combo. Any sweep run after 2026-08-13 is void.
    target_pct = cfg.risk.daily_profit_target_pct
    up_lim = (start_equity * target_pct / 100.0) if target_pct > 0 else None
    dn_lim = -start_equity * abs(cfg.risk.daily_max_loss_pct) / 100.0
    for t in sorted(day_trades, key=lambda t: t.entry_time):
        if len(chosen) >= cfg.strategy.max_trades_per_day:
            break
        realized = sum(c.pnl for c in chosen if c.exit_time <= t.entry_time)
        if up_lim is not None and realized >= up_lim:
            break
        if realized <= dn_lim:
            break
        chosen.append(t)
    return chosen


# --------------------------------------------------------------------------
# Historical data plumbing
# --------------------------------------------------------------------------

def build_universe(cfg: Config) -> list[str]:
    trading = TradingClient(
        cfg.credentials.api_key, cfg.credentials.secret_key,
        paper=cfg.credentials.paper,
    )
    assets = trading.get_all_assets(
        GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
    )
    return sorted(
        a.symbol for a in assets
        if a.tradable and a.symbol.isalpha() and len(a.symbol) <= 5
    )


def fetch_daily_bars(
    data: StockHistoricalDataClient, cfg: Config,
    symbols: list[str], start: datetime, end: datetime,
) -> dict[str, list]:
    out: dict[str, list] = {}
    chunk = 200
    for i in range(0, len(symbols), chunk):
        batch = symbols[i:i + chunk]
        try:
            req = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame(1, TimeFrameUnit.Day),
                start=start, end=end, feed=cfg.runtime.data_feed,
            )
            bars = data.get_stock_bars(req)
            out.update(bars.data)
        except Exception as e:  # noqa: BLE001
            print(f"  daily bars failed for chunk {i // chunk}: {e}")
        print(f"  daily bars {min(i + chunk, len(symbols))}/{len(symbols)}",
              end="\r")
    print()
    return out


def day_candidates(
    daily: dict[str, list], day, cfg: Config, min_prev_volume: float
) -> list[Candidate]:
    """Approximate the live screener for one historical day: gap at the open
    vs previous close, price band, previous-day volume floor."""
    scr = cfg.screener
    cands: list[Candidate] = []
    for sym, bars in daily.items():
        prev = None
        cur = None
        vols = []
        for b in bars:
            d = b.timestamp.date()
            if d < day:
                prev = b
                vols.append(float(b.volume))
            elif d == day:
                cur = b
                break
        if prev is None or cur is None:
            continue
        prev_close = float(prev.close)
        day_open = float(cur.open)
        if not (scr.min_price <= day_open <= scr.max_price):
            continue
        if float(prev.volume) < min_prev_volume:
            continue
        gap = (day_open - prev_close) / prev_close * 100.0 if prev_close else 0.0
        if abs(gap) < scr.min_abs_gap_pct:
            continue
        if scr.max_abs_gap_pct and abs(gap) > scr.max_abs_gap_pct:
            continue
        avg20 = sum(vols[-20:]) / len(vols[-20:]) if vols else 0.0
        rel_vol = float(prev.volume) / avg20 if avg20 else 1.0
        cands.append(Candidate(
            symbol=sym, gap_pct=round(gap, 2), rel_volume=round(rel_vol, 2),
            price=day_open, prev_close=prev_close, source="backtest",
        ))
    cands.sort(key=lambda c: abs(c.gap_pct), reverse=True)
    return cands[: scr.max_candidates]


def daily_returns_from_bars(
    daily: dict[str, list], day, lookback: int = 30
) -> dict[str, dict[str, float]]:
    """Close-to-close returns per symbol strictly BEFORE `day`, keyed by ISO
    date — the sim-side twin of `Broker.get_daily_returns`, feeding the v1.14
    correlated-theme cap so the backtest selects the same targets the live bot
    would. Strictly-before matters: including `day` itself would leak the
    outcome into the selection that produced it.
    """
    out: dict[str, dict[str, float]] = {}
    for sym, bars in daily.items():
        prior = [b for b in bars if b.timestamp.date() < day][-(lookback + 1):]
        series: dict[str, float] = {}
        prev = None
        for b in prior:
            close = float(b.close or 0)
            if prev is not None and prev > 0 and close > 0:
                series[b.timestamp.date().isoformat()] = close / prev - 1.0
            prev = close if close > 0 else prev
        if series:
            out[sym] = series
    return out


def compute_atr_pct(
    daily: dict[str, list], day, lookback: int = 14
) -> dict[str, float]:
    """14-day ATR as a % of the previous close, per symbol, as of `day`.

    Used to sweep volatility-scaled stops against the fixed % the bot has
    always used. True range = max(high-low, |high-prevclose|, |low-prevclose|).
    """
    out: dict[str, float] = {}
    for sym, bars in daily.items():
        prior = [b for b in bars if b.timestamp.date() < day]
        if len(prior) < lookback + 1:
            continue
        trs = []
        for prev, cur in zip(prior[-(lookback + 1):-1], prior[-lookback:]):
            pc = float(prev.close)
            trs.append(max(
                float(cur.high) - float(cur.low),
                abs(float(cur.high) - pc),
                abs(float(cur.low) - pc),
            ))
        last_close = float(prior[-1].close)
        if trs and last_close > 0:
            out[sym] = (sum(trs) / len(trs)) / last_close * 100.0
    return out


def score_news_for_day(
    news_client: NewsClient | None, cfg: Config, symbols: list[str], day
) -> dict[str, NewsSignal]:
    out: dict[str, NewsSignal] = {}
    if news_client is None:
        return {s: NewsSignal(s, 0.0, 0, "") for s in symbols}
    open_dt = datetime.combine(day, MARKET_OPEN, tzinfo=ET)
    start = open_dt - timedelta(hours=cfg.news.lookback_hours)
    for s in symbols:
        try:
            res = news_client.get_news(
                NewsRequest(symbols=s, start=start, end=open_dt, limit=15)
            )
            raw = getattr(res, "data", None)
            arts = (raw or {}).get("news", []) if isinstance(raw, dict) else (
                getattr(res, "news", []) or []
            )
        except Exception:  # noqa: BLE001
            arts = []
        scores = [
            score_text(f"{getattr(a, 'headline', '')}. {getattr(a, 'summary', '')}")
            for a in arts
        ]
        avg = sum(scores) / len(scores) if scores else 0.0
        out[s] = NewsSignal(s, round(avg, 3), len(arts),
                            getattr(arts[0], "headline", "") if arts else "")
    return out


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--symbols", default="",
                    help="comma-separated; skips the universe scan")
    ap.add_argument("--no-news", action="store_true",
                    help="skip news scoring (all biases 'any'; much faster)")
    ap.add_argument("--min-prev-volume", type=float, default=500_000,
                    help="previous-day share volume floor (most-actives proxy)")
    args = ap.parse_args()

    cfg = load_config()
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(
        hour=23, minute=59, tzinfo=timezone.utc
    )
    data = StockHistoricalDataClient(
        cfg.credentials.api_key, cfg.credentials.secret_key
    )
    news_client = None
    if not args.no_news and cfg.news.enabled:
        try:
            news_client = NewsClient(
                cfg.credentials.api_key, cfg.credentials.secret_key
            )
        except Exception as e:  # noqa: BLE001
            print(f"news client unavailable ({e}); continuing without news")

    if args.symbols:
        universe = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        print("Building universe from active US equities...")
        universe = build_universe(cfg)
        print(f"  {len(universe)} symbols")

    print("Fetching daily bars (universe screen)...")
    daily = fetch_daily_bars(
        data, cfg, universe, start - timedelta(days=45), end
    )

    # Trading days present in the data, within range.
    days = sorted({
        b.timestamp.date()
        for bars in daily.values() for b in bars
        if start.date() <= b.timestamp.date() <= end.date()
    })
    print(f"Simulating {len(days)} trading days "
          f"({cfg.selection.max_targets} targets/day max)...")

    equity = 2050.0  # nominal starting equity for the simulation
    all_trades: list[SimTrade] = []
    for day in days:
        cands = day_candidates(daily, day, cfg, args.min_prev_volume)
        if not cands:
            continue
        news = score_news_for_day(
            news_client, cfg, [c.symbol for c in cands], day
        )
        targets = build_targets(
            cfg, cands, news,
            daily_returns=daily_returns_from_bars(
                daily, day, cfg.selection.correlation_lookback_days
            ),
        )
        if not targets:
            continue
        # Minute bars for the day's targets (one batched call).
        day_start = datetime.combine(day, MARKET_OPEN, tzinfo=ET)
        day_end = datetime.combine(day, MARKET_CLOSE, tzinfo=ET)
        try:
            req = StockBarsRequest(
                symbol_or_symbols=[t.symbol for t in targets],
                timeframe=TimeFrame(1, TimeFrameUnit.Minute),
                start=day_start, end=day_end, feed=cfg.runtime.data_feed,
            )
            minute = data.get_stock_bars(req).data
        except Exception as e:  # noqa: BLE001
            print(f"{day}: minute bars failed: {e}")
            continue

        day_trades = []
        for t in targets:
            trade = simulate_symbol_day(
                t.symbol, minute.get(t.symbol, []), cfg, t.bias, equity,
                gap_pct=t.gap_pct, sentiment=t.sentiment,
            )
            if trade:
                day_trades.append(trade)
        kept = pick_day_trades(day_trades, cfg, equity)
        day_pnl = sum(t.pnl for t in kept)
        equity += day_pnl
        all_trades.extend(kept)
        tag = ", ".join(f"{t.symbol} {t.pnl:+.2f}({t.exit_reason})" for t in kept)
        print(f"{day}  {len(kept)} trade(s)  {day_pnl:+8.2f}  eq {equity:8.2f}  {tag}")

    # ---- results ----
    if not all_trades:
        print("\nNo trades simulated. Loosen filters or widen the date range.")
        return
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(SimTrade.__dataclass_fields__))
        w.writeheader()
        for t in all_trades:
            w.writerow(t.__dict__)

    s = compute_stats([t.pnl for t in all_trades], [t.pnl_pct for t in all_trades])
    reasons: dict[str, int] = {}
    for t in all_trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    print("\n" + "=" * 64)
    print(f"BACKTEST {args.start} .. {args.end}   ({len(days)} days)")
    print(f"  trades {s.n_trades} | win rate {s.win_rate:.1f}% | "
          f"expectancy {s.expectancy:+.2f}/trade ({s.expectancy_pct:+.3f}%)")
    print(f"  total PnL {s.total_pnl:+.2f} | max drawdown {s.max_drawdown:.2f} | "
          f"final equity {equity:.2f}")
    print(f"  avg win {s.avg_win:+.2f} | avg loss {s.avg_loss:+.2f} | exits {reasons}")
    print(f"  trades written to {OUT_FILE}")
    print("\nCaveats: approximate universe (no historical most-actives), IEX")
    print("bars are thin, spreads/halts/news-exits not simulated, stop-first")
    print("assumption on ambiguous bars. Treat results as directional, not exact.")


if __name__ == "__main__":
    main()
