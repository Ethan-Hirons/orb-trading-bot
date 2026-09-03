"""Backtest the Zarattini/Aziz QQQ opening-range strategy, cash-account style.

THE RULE (Zarattini & Aziz, "Can Day Trading Really Be Profitable?", 2023):
at 9:35 ET the first 5-minute QQQ candle picks the day's direction — bullish
candle = long day, bearish = short day, doji = no trade. Enter at 9:35; stop
at the opposite extreme of that first candle; optional profit target at 10R;
otherwise exit at the end of the day. One instrument, one trade per day.

THIS ADAPTATION for a small CASH account (no margin, no shorts):
  * Direction still comes from QQQ's first candle, but the position is
    expressed as LONG TQQQ (up day) or LONG SQQQ (down day) — both sides are
    longs, so a cash account can trade both. The paper itself uses TQQQ as
    the leveraged vehicle.
  * Stop/target/R are computed on the TRADED ETF's own first candle (a 3x
    ETF moves ~3x QQQ, so its own range is the right yardstick).
  * Sizing: risk `--risk-pct` of equity per trade (entry->stop), capped at
    100% notional (no leverage), whole shares. Proceeds settle T+1, which is
    compatible with one morning entry per day.
  * Slippage `--slippage-bps` charged against you on BOTH fills (TQQQ/SQQQ
    spreads are ~1 tick, but stop fills in fast tape cost more; 2-5 bps is a
    fair haircut, 0 reproduces the paper's frictionless assumption).

DIFFERENCES vs the published result, so nobody is surprised later:
  * The paper backtests 2016-2023 with up to 4x leverage sized ON QQQ and
    reports ~1,484% vs QQQ's ~169% buy-and-hold; hit ratio ~24%. Long-only
    3x-ETF whole-share sizing on a ~$2k account is NOT that portfolio.
  * Hit ratio ~1 in 4 means LONG loss streaks are normal, not broken.
  * A 10R target is hit a few times a year; most profit comes from a handful
    of huge trend days. Miss those (bot down, data gap) and the year is red.

Usage (PowerShell, from the project root; keys read from .env):
  python qqq_orb.py --start 2024-01-01 --end 2026-02-14
  python qqq_orb.py --start 2024-01-01 --end 2026-02-14 --or-minutes 5,15 --target-r off,10
  python qqq_orb.py --start 2026-02-15 --end 2026-08-21 --or-minutes 5 --target-r 10   # holdout, run ONCE

Minute bars are cached on disk (.qqq_cache/) after the first fetch, so grid
re-runs are fast and offline. Default feed is SIP (full consolidated tape —
free Alpaca plans can query it historically); pass --feed iex if SIP 403s.

Not financial advice; a backtest is an argument, not a promise.
"""

from __future__ import annotations

import argparse
import csv
import os
import pickle
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
SIGNAL_SYMBOL = "QQQ"
LONG_ETF = "TQQQ"   # traded on an UP first candle
SHORT_ETF = "SQQQ"  # traded on a DOWN first candle (still held LONG)
CACHE_DIR = Path(".qqq_cache")
TRADES_FILE = "qqq_trades.csv"
RESULTS_FILE = "qqq_orb_results.csv"


@dataclass
class Bar:
    """Minimal minute bar (picklable, no alpaca dependency)."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class DayTrade:
    date: str
    signal: str        # "up" | "down"
    symbol: str        # TQQQ | SQQQ
    qty: int
    entry_price: float # slippage-adjusted fill
    stop_price: float
    target_price: float | None
    exit_price: float  # slippage-adjusted fill
    exit_reason: str   # "stop" | "target" | "eod"
    entry_time: str
    exit_time: str
    risk_dollars: float  # qty * (entry - stop): the actual R at stake
    pnl: float
    pnl_r: float


# --------------------------------------------------------------------------
# Pure simulation (no API, unit-testable)
# --------------------------------------------------------------------------

def or_window(bars: list[Bar], day: date, minutes: int) -> list[Bar]:
    open_dt = datetime.combine(day, MARKET_OPEN, tzinfo=ET)
    end = open_dt + timedelta(minutes=minutes)
    return [b for b in bars if open_dt <= b.timestamp.astimezone(ET) < end]


def signal_direction(qqq_bars: list[Bar], day: date, minutes: int) -> str | None:
    """'up' / 'down' from QQQ's first candle; None on doji or missing data."""
    w = or_window(qqq_bars, day, minutes)
    if not w:
        return None
    o, c = w[0].open, w[-1].close
    if c > o:
        return "up"
    if c < o:
        return "down"
    return None  # doji: no order placed


def simulate_day(
    day: date,
    qqq_bars: list[Bar],
    etf_bars_by_symbol: dict[str, list[Bar]],
    equity: float,
    or_minutes: int = 5,
    target_r: float | None = 10.0,
    risk_pct: float = 1.0,
    slippage_bps: float = 2.0,
    eod_minutes_before_close: int = 5,
) -> DayTrade | None:
    """One day, one trade. Conservative conventions match backtest.py:
    stop before target when a bar touches both; a bar OPENING beyond the stop
    fills at that open (gap-through), never at the stop price."""
    direction = signal_direction(qqq_bars, day, or_minutes)
    if direction is None:
        return None
    symbol = LONG_ETF if direction == "up" else SHORT_ETF
    bars = etf_bars_by_symbol.get(symbol) or []
    w = or_window(bars, day, or_minutes)
    if not w:
        return None

    open_dt = datetime.combine(day, MARKET_OPEN, tzinfo=ET)
    entry_after = open_dt + timedelta(minutes=or_minutes)
    eod_cutoff = (datetime.combine(day, MARKET_CLOSE, tzinfo=ET)
                  - timedelta(minutes=eod_minutes_before_close))
    session = [b for b in bars
               if entry_after <= b.timestamp.astimezone(ET) < eod_cutoff]
    if not session:
        return None

    slip = slippage_bps / 10_000.0
    raw_entry = session[0].open
    entry = raw_entry * (1 + slip)          # buy fills slip against you
    stop = min(b.low for b in w)            # ETF's own first-candle extreme
    if entry <= stop:                        # opened at/below the stop: no trade
        return None
    r = entry - stop
    target = entry + target_r * r if target_r else None

    # Cash-account sizing: risk_pct of equity per R, hard-capped at 1x notional.
    risk_budget = equity * risk_pct / 100.0
    qty = int(min(risk_budget / r, equity / entry))
    if qty < 1:
        return None

    exit_raw, reason, exit_bar = None, None, None
    for b in session:
        if b.open <= stop:                  # gapped through: fill at the open
            exit_raw, reason = b.open, "stop"
        elif b.low <= stop:                 # stop first on ambiguous bars
            exit_raw, reason = stop, "stop"
        elif target is not None and b.open >= target:
            exit_raw, reason = b.open, "target"   # favorable gap
        elif target is not None and b.high >= target:
            exit_raw, reason = target, "target"
        if exit_raw is not None:
            exit_bar = b
            break
    if exit_raw is None:
        exit_bar = session[-1]
        exit_raw, reason = exit_bar.close, "eod"

    exit_px = exit_raw * (1 - slip)         # sell fills slip against you
    pnl = (exit_px - entry) * qty
    risk_dollars = qty * r
    return DayTrade(
        date=day.isoformat(), signal=direction, symbol=symbol, qty=qty,
        entry_price=round(entry, 4), stop_price=round(stop, 4),
        target_price=round(target, 4) if target else None,
        exit_price=round(exit_px, 4), exit_reason=reason,
        entry_time=session[0].timestamp.astimezone(ET).strftime("%H:%M"),
        exit_time=exit_bar.timestamp.astimezone(ET).strftime("%H:%M"),
        risk_dollars=round(risk_dollars, 2),
        pnl=round(pnl, 2),
        pnl_r=round(pnl / risk_dollars, 3) if risk_dollars else 0.0,
    )


def run_combo(
    days: list[date],
    bars_by_day: dict[str, dict[date, list[Bar]]],
    start_equity: float,
    **kwargs,
) -> tuple[list[DayTrade], list[float], float]:
    """Simulate every day, compounding equity. Returns (trades, daily
    end-of-day equity marks, final equity)."""
    equity = start_equity
    trades: list[DayTrade] = []
    curve: list[float] = []
    for day in days:
        t = simulate_day(
            day,
            bars_by_day[SIGNAL_SYMBOL].get(day, []),
            {s: bars_by_day[s].get(day, []) for s in (LONG_ETF, SHORT_ETF)},
            equity,
            **kwargs,
        )
        if t:
            equity += t.pnl
            trades.append(t)
        curve.append(equity)
    return trades, curve, equity


def summarize(trades: list[DayTrade], curve: list[float], start_equity: float) -> dict:
    if not trades:
        return {"trades": 0}
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    peak, max_dd = start_equity, 0.0
    prev = start_equity
    daily_rets = []
    for eq in curve:
        if prev > 0:
            daily_rets.append((eq - prev) / prev)
        prev = eq
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    sharpe = 0.0
    if len(daily_rets) > 2 and statistics.pstdev(daily_rets) > 0:
        sharpe = (statistics.mean(daily_rets) / statistics.pstdev(daily_rets)
                  * (252 ** 0.5))
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "expectancy": round(sum(pnls) / len(pnls), 2),
        "expectancy_r": round(sum(t.pnl_r for t in trades) / len(trades), 3),
        "total_pnl": round(sum(pnls), 2),
        "final_equity": round(curve[-1], 2),
        "return_pct": round((curve[-1] / start_equity - 1) * 100, 2),
        "max_dd": round(max_dd, 2),
        "max_dd_pct": round(max_dd / peak * 100, 2) if peak else 0.0,
        "sharpe": round(sharpe, 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "exits": reasons,
    }


# --------------------------------------------------------------------------
# Data plumbing (lazy alpaca import; monthly on-disk cache)
# --------------------------------------------------------------------------

def _load_credentials() -> tuple[str, str]:
    from dotenv import load_dotenv
    load_dotenv()
    key = os.environ.get("ALPACA_API_KEY", "")
    sec = os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not sec:
        raise SystemExit("ALPACA_API_KEY / ALPACA_SECRET_KEY not set (.env)")
    return key, sec


def _month_starts(start: date, end: date) -> list[date]:
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        out.append(cur)
        cur = (date(cur.year + 1, 1, 1) if cur.month == 12
               else date(cur.year, cur.month + 1, 1))
    return out


def fetch_minute_bars(
    symbols: list[str], start: date, end: date, feed: str
) -> dict[str, list[Bar]]:
    """Minute bars per symbol, SPLIT-ADJUSTED (SQQQ reverse-splits regularly —
    raw bars would fake 4x overnight moves), cached per (symbol, month, feed)."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    key, sec = _load_credentials()
    client = StockHistoricalDataClient(key, sec)
    CACHE_DIR.mkdir(exist_ok=True)
    out: dict[str, list[Bar]] = {s: [] for s in symbols}
    months = _month_starts(start, end)
    for m in months:
        month_last = (date(m.year + 1, 1, 1) if m.month == 12
                      else date(m.year, m.month + 1, 1)) - timedelta(days=1)
        m_start, m_end = max(m, start), min(month_last, end)
        # Cache only complete, fully-in-the-past months; partial slices (the
        # range's edge months, or the current month) are always refetched.
        cacheable = (m_start == m and m_end == month_last
                     and month_last < date.today())
        for sym in symbols:
            cache = CACHE_DIR / f"{sym}_{m:%Y-%m}_{feed}.pkl"
            if cacheable and cache.exists():
                out[sym].extend(pickle.loads(cache.read_bytes()))
                print(f"  {sym} {m:%Y-%m}: cached")
                continue
            req = StockBarsRequest(
                symbol_or_symbols=sym,
                timeframe=TimeFrame(1, TimeFrameUnit.Minute),
                start=datetime.combine(m_start, dtime(0, 0), tzinfo=timezone.utc),
                end=datetime.combine(m_end, dtime(23, 59), tzinfo=timezone.utc),
                feed=feed,
                adjustment="all",
            )
            raw = client.get_stock_bars(req).data.get(sym, [])
            bars = [Bar(b.timestamp, float(b.open), float(b.high),
                        float(b.low), float(b.close), float(b.volume))
                    for b in raw]
            if cacheable:
                cache.write_bytes(pickle.dumps(bars))
            out[sym].extend(bars)
            print(f"  {sym} {m:%Y-%m}: {len(bars)} bars")
    return out


def group_by_day(bars: list[Bar]) -> dict[date, list[Bar]]:
    out: dict[date, list[Bar]] = {}
    for b in bars:
        out.setdefault(b.timestamp.astimezone(ET).date(), []).append(b)
    for v in out.values():
        v.sort(key=lambda b: b.timestamp)
    return out


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def _parse_floats_with_off(spec: str) -> list[float | None]:
    out: list[float | None] = []
    for tok in spec.split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        out.append(None if tok == "off" else float(tok))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--or-minutes", default="5",
                    help="comma list of opening-candle lengths (default 5)")
    ap.add_argument("--target-r", default="10",
                    help="comma list of profit targets in R; 'off' = EOD only")
    ap.add_argument("--risk-pct", type=float, default=1.0,
                    help="%% of equity risked entry->stop (default 1.0)")
    ap.add_argument("--slippage-bps", type=float, default=2.0,
                    help="slippage per side in basis points (default 2)")
    ap.add_argument("--equity", type=float, default=2050.0,
                    help="starting equity for the simulation")
    ap.add_argument("--feed", default="sip", choices=["sip", "iex"],
                    help="data feed (default sip; use iex if sip 403s)")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    or_list = [int(x) for x in args.or_minutes.split(",") if x.strip()]
    tr_list = _parse_floats_with_off(args.target_r)

    print(f"Fetching minute bars {start} .. {end} (feed={args.feed}; "
          f"first run is slow, cached after)...")
    bars_by_symbol = fetch_minute_bars(
        [SIGNAL_SYMBOL, LONG_ETF, SHORT_ETF], start, end, args.feed
    )
    bars_by_day = {s: group_by_day(b) for s, b in bars_by_symbol.items()}
    days = sorted(d for d in bars_by_day[SIGNAL_SYMBOL] if start <= d <= end)
    print(f"{len(days)} trading days with QQQ data.\n")

    combos = [(om, tr) for om in or_list for tr in tr_list]
    rows = []
    single = len(combos) == 1
    for om, tr in combos:
        trades, curve, _ = run_combo(
            days, bars_by_day, args.equity,
            or_minutes=om, target_r=tr, risk_pct=args.risk_pct,
            slippage_bps=args.slippage_bps,
        )
        s = summarize(trades, curve, args.equity)
        s |= {"or_minutes": om, "target_r": tr if tr is not None else "off"}
        rows.append(s)
        tag = f"OR {om}m / target {'off' if tr is None else f'{tr:g}R'}"
        if s["trades"] == 0:
            print(f"{tag}: no trades (check data range)")
            continue
        print(f"{tag}: {s['trades']} trades | win {s['win_rate']}% | "
              f"exp {s['expectancy']:+.2f} ({s['expectancy_r']:+.3f}R) | "
              f"total {s['total_pnl']:+.2f} ({s['return_pct']:+.1f}%) | "
              f"maxDD {s['max_dd']:.2f} ({s['max_dd_pct']:.1f}%) | "
              f"Sharpe {s['sharpe']:.2f} | exits {s['exits']}")
        if single:
            with open(TRADES_FILE, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(
                    f, fieldnames=list(DayTrade.__dataclass_fields__))
                w.writeheader()
                for t in trades:
                    w.writerow(t.__dict__)
            print(f"  per-trade log -> {TRADES_FILE}")
            years: dict[int, list[float]] = {}
            for t in trades:
                years.setdefault(int(t.date[:4]), []).append(t.pnl)
            print("  by year: " + " | ".join(
                f"{y}: {sum(p):+.2f} ({len(p)} trades)"
                for y, p in sorted(years.items())))

    with open(RESULTS_FILE, "w", newline="", encoding="utf-8") as f:
        keys = [k for k in rows[0] if k != "exits"] if rows else []
        w = csv.DictWriter(f, fieldnames=keys + ["exits"])
        w.writeheader()
        for r in rows:
            w.writerow({**{k: r.get(k) for k in keys}, "exits": str(r.get("exits"))})
    print(f"\nsummary -> {RESULTS_FILE}")
    print("\nCaveats: split-adjusted bars; stop-first on ambiguous bars;")
    print("no halts/spread widening modeled; ~24% hit ratio means long red")
    print("streaks are NORMAL for this strategy. Tune only on the tuning")
    print("window; touch the holdout ONCE. Not financial advice.")


if __name__ == "__main__":
    main()
