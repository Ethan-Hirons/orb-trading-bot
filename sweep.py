"""Parameter sweep: replay a date range once, then grade stop-loss %,
news-bias threshold, trailing-stop and time-stop combinations against each
other.

Fetches historical data ONCE (universe, daily bars, per-day candidates, news,
minute bars), then re-runs the pure simulation for every parameter combo, so a
9-combo sweep costs barely more than one backtest.

Usage (from the project root, uses config.yaml + .env; run on your machine —
needs Alpaca access):
  python sweep.py --start 2026-05-01 --end 2026-07-17
  python sweep.py --start ... --end ... --stops 2.0,2.5,3.0 --bias 0.3,0.5,off
  python sweep.py --start ... --end ... --symbols NOK,INTC,SOUN   # quick test
  # exit-tuning sweep for the Aug 1-2 session (trail % off peak, hold minutes):
  python sweep.py --start 2026-06-01 --end 2026-07-31 \
      --stops 3.0 --bias 0.5 --trail off,1.0,1.5,2.0 --hold 120,180,240,off
  # Aug 1-2 asymmetry grid (see TUNING-ANALYSIS-2026-07-29.md): trail arming
  # semantics (entry = old sim, breakeven = live v1.8), stop %, TP on/off:
  python sweep.py --start 2026-06-01 --end 2026-07-31 --no-shorts \
      --stops 1.5,2.0,2.5,3.0 --bias 0.5 --trail 0.75,1.0,1.25 \
      --arm entry,breakeven --tp cfg,off --hold off,180

Every combo shares backtest.py's honest limitations (approximate universe,
thin IEX bars, stop-first assumption, no spread/halt/news-exit simulation) —
they cancel out when COMPARING combos, but treat absolute PnL as directional.
Results: printed table + sweep_results.csv.
"""

from __future__ import annotations

import os

os.environ.setdefault("ORB_NO_FILE_LOG", "1")  # never write into the live day log

import argparse
import copy
import csv
import hashlib
import pickle
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from backtest import (
    ET,
    MARKET_CLOSE,
    MARKET_OPEN,
    build_universe,
    compute_atr_pct,
    daily_returns_from_bars,
    day_candidates,
    fetch_daily_bars,
    pick_day_trades,
    score_news_for_day,
    simulate_symbol_day,
)
from orb_bot.config import load_config
from orb_bot.report import compute_stats
from orb_bot.selection import build_targets

OUT_FILE = "sweep_results.csv"
START_EQUITY = 2050.0  # nominal starting equity for the sweep
CACHE_DIR = Path(".sweep_cache")
# Bumped whenever the cached tuple's SHAPE changes, so an older cache is never
# silently misread. v2 (2026-08-25) appended per-day daily-return history for
# the v1.14 correlated-theme cap.
CACHE_VERSION = 2


@dataclass
class _Bar:
    """Lightweight stand-in for an Alpaca bar: same attribute names, far
    cheaper to pickle (an alpaca Bar is a pydantic model, and a 43-day x
    40-symbol minute cache is ~670k bars)."""
    timestamp: object
    open: float
    high: float
    low: float
    close: float
    volume: float


def _lighten(minute: dict) -> dict:
    return {
        sym: [_Bar(b.timestamp, float(b.open), float(b.high), float(b.low),
                   float(b.close), float(b.volume)) for b in bars]
        for sym, bars in minute.items()
    }


def _cache_key(cfg, args) -> str:
    """Identify a prefetch by everything that changes its CONTENT.

    Deliberately EXCLUDES every sweep-grid dimension (stops, bias, trail, arm,
    tp, or-minutes, relvol, atr, entry-window, first-candle-dir, hold,
    no-shorts): those are applied later, in run_combo, against the same cached
    data. That is the whole point — Runs 1-5 of the runbook share one prefetch.
    """
    scr, news = cfg.screener, cfg.news
    parts = [
        args.start, args.end, args.symbols, str(args.min_prev_volume),
        str(bool(args.no_news)), str(cfg.runtime.data_feed),
        str(scr.min_price), str(scr.max_price), str(scr.min_abs_gap_pct),
        str(scr.max_abs_gap_pct), str(scr.max_candidates),
        str(news.enabled), str(news.lookback_hours),
        f"v{CACHE_VERSION}",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def prefetch(cfg, args):
    """Cached wrapper around the expensive one-pass data fetch.

    The prefetch (full active-equity universe scan + a news lookup per
    candidate per day, ~15 min) is by far the costly part, and every run
    sharing a date range replays the identical data. --no-cache skips reading
    and writing; --refresh-cache forces a refetch (use it when the range now
    covers days whose bars were still forming when the cache was written).
    """
    key = _cache_key(cfg, args)
    cache_file = CACHE_DIR / f"prefetch_{key}.pkl"
    if not args.no_cache and not args.refresh_cache and cache_file.exists():
        try:
            cached = pickle.loads(cache_file.read_bytes())
            print(f"Prefetch cache HIT ({cache_file}, {len(cached)} days) — "
                  f"skipping universe scan. --refresh-cache to refetch.")
            return cached
        except Exception as e:  # noqa: BLE001
            print(f"cache unreadable ({e}); refetching")
    result = _prefetch_uncached(cfg, args)
    if not args.no_cache and result:
        try:
            CACHE_DIR.mkdir(exist_ok=True)
            cache_file.write_bytes(pickle.dumps(result))
            mb = cache_file.stat().st_size / 1e6
            print(f"Prefetch cached -> {cache_file} ({mb:.0f} MB). "
                  f"Later sweeps on this date range reuse it.")
        except Exception as e:  # noqa: BLE001
            print(f"could not write cache ({e}); continuing")
    return result


def _prefetch_uncached(cfg, args):
    """One pass over the date range: everything the sim needs, per day."""
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
    daily = fetch_daily_bars(data, cfg, universe, start - timedelta(days=45), end)
    days = sorted({
        b.timestamp.date()
        for bars in daily.values() for b in bars
        if start.date() <= b.timestamp.date() <= end.date()
    })

    print(f"Prefetching {len(days)} days (candidates, news, minute bars)...")
    cache = []  # (day, candidates, news_signals, minute_bars_by_symbol)
    for day in days:
        cands = day_candidates(daily, day, cfg, args.min_prev_volume)
        if not cands:
            continue
        news = score_news_for_day(news_client, cfg, [c.symbol for c in cands], day)
        day_start = datetime.combine(day, MARKET_OPEN, tzinfo=ET)
        day_end = datetime.combine(day, MARKET_CLOSE, tzinfo=ET)
        try:
            req = StockBarsRequest(
                symbol_or_symbols=[c.symbol for c in cands],
                timeframe=TimeFrame(1, TimeFrameUnit.Minute),
                start=day_start, end=day_end, feed=cfg.runtime.data_feed,
            )
            minute = data.get_stock_bars(req).data
        except Exception as e:  # noqa: BLE001
            print(f"{day}: minute bars failed: {e}")
            continue
        atr = compute_atr_pct(daily, day)
        rets = daily_returns_from_bars(
            daily, day, cfg.selection.correlation_lookback_days
        )
        cache.append((day, cands, news, _lighten(minute), atr, rets))
        print(f"  {day}: {len(cands)} candidates", end="\r")
    print()
    return cache


def run_combo(base_cfg, cache, stop_pct: float, bias_setting,
              trail_setting="off", hold_setting=None,
              arm_setting="entry", tp_setting="cfg",
              or_minutes=None, relvol_min=0.0, atr_k=None,
              entry_window=None, first_candle=False) -> dict:
    """Re-simulate the cached days under one parameter combo.

    bias_setting: a float (min |sentiment| to set a directional bias) or the
    string "off" (news never blocks a breakout; entries in both directions).
    trail_setting: trailing-stop % off the favorable peak, or "off".
    hold_setting: time-stop minutes, "off" (no time stop), or None (= live
    config value, cfg.exits.max_hold_minutes).
    arm_setting: "entry" (trail active from the first bar — old sim behavior)
    or "breakeven" (trail only once past entry — live v1.8 semantics).
    tp_setting: "cfg" (live volatility-scaled 3-5% TP) or "off" (no TP).

    v1.12 dimensions:
    or_minutes: opening-range length (None = live config, currently 15). The
    US-stocks ORB paper found 5 min materially beat 15/30/60 on identical
    rules (1,637% vs 272% vs 21% vs 39%).
    relvol_min: drop candidates whose relative volume is below this. NOTE the
    sim's rel_volume is a DAILY proxy (previous-day volume / 20-day average,
    from day_candidates) — the live v1.12 filter measures the finer
    opening-window ratio. Correlated, not identical; treat a sweep result here
    as evidence the CONCEPT works, not as a calibrated threshold.
    atr_k: stop = atr_k x (14-day ATR as % of price), per symbol per day,
    replacing the flat stop_pct. None = keep the flat stop.

    v1.13 dimensions:
    entry_window: minutes after the open past which no new entry is taken
    (None = live config, currently 180). This is the largest single effect in
    the live trial — entries at >= 60 min were 12 trades for -122.50, with no
    winner above +4.04 — and until now it had no sweep dimension at all, so the
    hypothesis could not be tested against anything but the 56 trades that
    generated it. Confirm here, out of sample, before touching config.
    first_candle: use the paper's direction rule (opening-range candle body
    picks the side) instead of breakout-side + news bias.
    """
    cfg = copy.deepcopy(base_cfg)
    cfg.exits.stop_pct = stop_pct
    if or_minutes:
        cfg.strategy.opening_range_minutes = int(or_minutes)
    if bias_setting == "off":
        cfg.strategy.respect_news_bias = False
    else:
        cfg.news.min_sentiment_for_bias = float(bias_setting)
    trail = None if trail_setting == "off" else float(trail_setting)
    if hold_setting is None:
        hold = None
    else:
        hold = 0 if hold_setting == "off" else int(hold_setting)

    equity = START_EQUITY
    trades = []
    skipped_relvol = 0
    for entry in cache:
        # Tolerate a v1 (5-tuple) cache: no return history means the
        # correlated cap is simply inactive for that run, never a crash.
        day, cands, news, minute, atr = entry[:5]
        rets = entry[5] if len(entry) > 5 else {}
        if relvol_min > 0:
            before = len(cands)
            cands = [c for c in cands if (c.rel_volume or 0) >= relvol_min]
            skipped_relvol += before - len(cands)
            if not cands:
                continue
        targets = build_targets(cfg, cands, news, daily_returns=rets)
        day_trades = []
        for t in targets:
            stop_override = None
            if atr_k:
                a = atr.get(t.symbol)
                if a is None:
                    continue  # cannot size the stop without ATR; sit it out
                stop_override = atr_k * a
            trade = simulate_symbol_day(
                t.symbol, minute.get(t.symbol, []), cfg, t.bias, equity,
                gap_pct=t.gap_pct, sentiment=t.sentiment,
                trail_pct=trail, max_hold_minutes=hold,
                trail_arm=arm_setting, tp_off=(tp_setting == "off"),
                stop_pct_override=stop_override,
                first_candle_dir=first_candle,
                entry_window_override=entry_window,
            )
            if trade:
                day_trades.append(trade)
        kept = pick_day_trades(day_trades, cfg, equity)
        equity += sum(t.pnl for t in kept)
        trades.extend(kept)

    row = {
        "stop_pct": stop_pct if not atr_k else f"atr x{atr_k}",
        "bias": bias_setting,
        "trail": trail_setting, "arm": arm_setting, "tp": tp_setting,
        "or_min": or_minutes or base_cfg.strategy.opening_range_minutes,
        "entry_win": (entry_window if entry_window is not None
                      else base_cfg.strategy.entry_window_minutes),
        "dir_rule": "or-candle" if first_candle else "breakout",
        "relvol": relvol_min or "off",
        "hold": "cfg" if hold_setting is None else hold_setting,
        "trades": len(trades), "win_rate": 0.0, "net_pnl": 0.0,
        "expectancy": 0.0, "max_drawdown": 0.0,
        "avg_win": 0.0, "avg_loss": 0.0, "final_equity": round(equity, 2),
        "exits": "",
    }
    if trades:
        s = compute_stats([t.pnl for t in trades], [t.pnl_pct for t in trades])
        reasons: dict[str, int] = {}
        for t in trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        row.update(
            win_rate=round(s.win_rate, 1), net_pnl=round(s.total_pnl, 2),
            expectancy=round(s.expectancy, 2),
            max_drawdown=round(s.max_drawdown, 2),
            avg_win=round(s.avg_win, 2), avg_loss=round(s.avg_loss, 2),
            exits="/".join(f"{k}:{v}" for k, v in sorted(reasons.items())),
        )
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--symbols", default="",
                    help="comma-separated; skips the universe scan")
    ap.add_argument("--no-news", action="store_true",
                    help="skip news scoring (bias sweep values then equal 'off')")
    ap.add_argument("--min-prev-volume", type=float, default=500_000,
                    help="previous-day share volume floor (most-actives proxy)")
    ap.add_argument("--stops", default="2.0,2.5,3.0",
                    help="stop-loss %% values to test")
    ap.add_argument("--bias", default="0.3,0.5,off",
                    help="min_sentiment_for_bias values to test; 'off' = ignore bias")
    ap.add_argument("--no-shorts", action="store_true",
                    help="disable short entries (mirror the funded cash account)")
    ap.add_argument("--trail", default="off",
                    help="trailing-stop %% values to test; 'off' = none (live behavior)")
    ap.add_argument("--hold", default="",
                    help="time-stop minutes to test; 'off' = no time stop; "
                         "empty = live config value (max_hold_minutes)")
    ap.add_argument("--arm", default="entry",
                    help="trail arming to test: 'entry' (trail active from the "
                         "first bar — old sim) and/or 'breakeven' (only past "
                         "entry — live v1.8 semantics)")
    ap.add_argument("--tp", default="cfg",
                    help="take-profit to test: 'cfg' (live 3-5%% scaled) "
                         "and/or 'off' (no TP; trail/stop/time/EOD only)")
    ap.add_argument("--or-minutes", default="",
                    help="v1.12: opening-range lengths to test, e.g. 5,15. "
                         "Empty = live config. The US-stocks ORB paper found "
                         "5 min beat 15/30/60 substantially.")
    ap.add_argument("--relvol", default="",
                    help="v1.12: relative-volume floors to test, e.g. "
                         "off,1.0,1.5,2.0. Drops candidates below the floor. "
                         "Sim uses a DAILY proxy (prev-day vol / 20d avg); "
                         "the live filter measures the opening window.")
    ap.add_argument("--entry-window", default="",
                    help="v1.13: entry cutoff in minutes after the open, e.g. "
                         "30,60,180. Empty = live config (180). Live trial: "
                         "entries >=60min were 12 trades for -122.50.")
    ap.add_argument("--first-candle-dir", default="off",
                    help="v1.13: direction rule to test — 'off' (live: breakout "
                         "side, filtered by news bias), 'on' (the ORB paper: "
                         "the opening-range candle body picks the side, doji "
                         "trades nothing), or 'off,on' to compare both.")
    ap.add_argument("--atr-stop", default="",
                    help="v1.12: ATR multipliers for a volatility-scaled stop, "
                         "e.g. 0.5,1.0,1.5. Replaces the flat --stops width "
                         "with k x (14-day ATR %% of price) per symbol.")
    ap.add_argument("--no-cache", action="store_true",
                    help="do not read or write the on-disk prefetch cache")
    ap.add_argument("--refresh-cache", action="store_true",
                    help="ignore any cached prefetch and refetch from Alpaca "
                         "(use when the range now covers days that were still "
                         "forming when the cache was written)")
    args = ap.parse_args()

    stops = [float(s) for s in args.stops.split(",") if s.strip()]
    biases = [b.strip() if b.strip() == "off" else float(b)
              for b in args.bias.split(",") if b.strip()]
    trails = [t.strip() if t.strip() == "off" else float(t)
              for t in args.trail.split(",") if t.strip()]
    holds = ([h.strip() if h.strip() == "off" else int(h)
              for h in args.hold.split(",") if h.strip()] or [None])
    arms = [a.strip() for a in args.arm.split(",") if a.strip()]
    bad = [a for a in arms if a not in ("entry", "breakeven")]
    if bad:
        ap.error(f"--arm values must be entry/breakeven, got: {','.join(bad)}")
    tps = [t.strip() for t in args.tp.split(",") if t.strip()]
    bad = [t for t in tps if t not in ("cfg", "off")]
    if bad:
        ap.error(f"--tp values must be cfg/off, got: {','.join(bad)}")
    or_mins = ([int(o) for o in args.or_minutes.split(",") if o.strip()]
               or [None])
    relvols = ([0.0 if r.strip() == "off" else float(r)
                for r in args.relvol.split(",") if r.strip()] or [0.0])
    atr_ks = ([None if a.strip() == "off" else float(a)
               for a in args.atr_stop.split(",") if a.strip()] or [None])
    entry_wins = ([int(w) for w in args.entry_window.split(",") if w.strip()]
                  or [None])
    fc_vals = [v.strip() for v in args.first_candle_dir.split(",") if v.strip()]
    bad = [v for v in fc_vals if v not in ("on", "off")]
    if bad:
        ap.error(f"--first-candle-dir values must be on/off, got: {','.join(bad)}")
    first_candles = [v == "on" for v in fc_vals] or [False]

    cfg = load_config()
    if args.no_shorts:
        cfg.strategy.allow_shorts = False
        print("Shorts disabled (cash-account mode).")
    cache = prefetch(cfg, args)
    if not cache:
        print("No days with candidates in range; widen the dates.")
        return

    n_combos = (len(stops) * len(biases) * len(trails) * len(holds)
                * len(arms) * len(tps) * len(or_mins) * len(relvols)
                * len(atr_ks) * len(entry_wins) * len(first_candles))
    print(f"\nSimulating {n_combos} combos over {len(cache)} day(s)...\n")
    rows = [run_combo(cfg, cache, sp, b, tr, hd, am, tpv, om, rv, ak, ew, fc)
            for sp in stops for b in biases for tr in trails
            for hd in holds for am in arms for tpv in tps
            for om in or_mins for rv in relvols for ak in atr_ks
            for ew in entry_wins for fc in first_candles]
    rows.sort(key=lambda r: r["expectancy"], reverse=True)

    hdr = (f"{'STOP%':>9} {'BIAS':>5} {'TRAIL':>6} {'ARM':>10} {'TP':>4} "
           f"{'OR':>3} {'ENTWIN':>6} {'DIR':>9} {'RELVOL':>6} {'HOLD':>5} "
           f"{'TRADES':>7} "
           f"{'WIN%':>6} {'NET':>9} {'EXP/TR':>8} {'MAXDD':>8} {'AVG W':>8} "
           f"{'AVG L':>8}  EXITS")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{str(r['stop_pct']):>9} {str(r['bias']):>5} {str(r['trail']):>6} "
              f"{r['arm']:>10} {r['tp']:>4} {str(r['or_min']):>3} "
              f"{str(r['entry_win']):>6} {r['dir_rule']:>9} "
              f"{str(r['relvol']):>6} {str(r['hold']):>5} {r['trades']:>7} "
              f"{r['win_rate']:>6} {r['net_pnl']:>+9.2f} {r['expectancy']:>+8.2f} "
              f"{r['max_drawdown']:>8.2f} {r['avg_win']:>+8.2f} "
              f"{r['avg_loss']:>+8.2f}  {r['exits']}")
    mark = " (current live config)" if any(
        r["stop_pct"] == 3.0 and r["bias"] == 0.5 for r in rows) else ""
    print(f"\nSorted by expectancy/trade. stop=3.0 bias=0.5 trail=off hold=cfg{mark}.")
    print("Compare combos against each other; absolute PnL is directional only")
    print("(approximate universe, thin IEX bars, no spreads/halts/news-exits).")

    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"Results written to {OUT_FILE}")


if __name__ == "__main__":
    main()
