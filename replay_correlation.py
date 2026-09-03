"""Replay the v1.14 correlated-theme cap against days that already happened.

Answers the only question that matters before this goes live: on the sessions
that actually hurt, which names would the cap have dropped, and what would the
day's P&L have been?

This is the same validation v1.7's same-underlying cap got (replay showed
2026-07-23 would have been -35.21 instead of -77.92). It needs Alpaca history,
so run it on YOUR machine:

    python replay_correlation.py
    python replay_correlation.py --max-corr 0.85 --days 2026-08-21,2026-08-24

IMPORTANT — what this is and is not. It re-runs SELECTION only. It assumes a
name the bot actually traded would still have traded identically if it survived
the cap, which is true for the dropped names' peers but ignores second-order
effects (a freed target slot could have been filled by a different candidate
that then traded). So read it as "what the cap removes", not as a precise
counterfactual P&L. It is deliberately optimistic about nothing: dropped
WINNERS are counted against the cap just as dropped losers count for it.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("ORB_NO_FILE_LOG", "1")

from orb_bot.config import load_config  # noqa: E402
from orb_bot.selection import aligned_returns, pearson  # noqa: E402

HISTORY = Path("state/trade_history.csv")


def load_trades(days: list[str]) -> dict[str, list[tuple[str, float]]]:
    """{date: [(symbol, pnl), ...]} for the requested days, in file order."""
    out: dict[str, list[tuple[str, float]]] = defaultdict(list)
    with HISTORY.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["date"] in days:
                out[r["date"]].append((r["symbol"], float(r["pnl"])))
    return out


def fetch_returns(symbols: list[str], upto: str, lookback: int) -> dict:
    """Daily returns strictly BEFORE `upto` — the information the bot would
    have had that morning. Using the day itself would leak the outcome."""
    from datetime import date, datetime, timedelta, timezone

    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    cfg = load_config()
    client = StockHistoricalDataClient(
        cfg.credentials.api_key, cfg.credentials.secret_key
    )
    day = datetime.strptime(upto, "%Y-%m-%d").date()
    start = day - timedelta(days=int(lookback * 1.6) + 10)
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame(1, TimeFrameUnit.Day),
        start=datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
        end=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
        feed=cfg.runtime.data_feed,
    )
    bars = client.get_stock_bars(req)
    out: dict[str, dict[str, float]] = {}
    for s in symbols:
        series: dict[str, float] = {}
        prev = None
        for b in bars.data.get(s, []):
            if b.timestamp.date() >= day:
                break
            close = float(b.close or 0)
            if prev is not None and prev > 0 and close > 0:
                series[b.timestamp.date().isoformat()] = close / prev - 1.0
            prev = close if close > 0 else prev
        if series:
            out[s] = series
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", default="2026-07-23,2026-08-21,2026-08-24",
                    help="comma-separated dates to replay")
    ap.add_argument("--max-corr", type=float, default=None,
                    help="override config selection.max_correlation")
    ap.add_argument("--cap", type=int, default=None,
                    help="override config selection.max_per_cluster")
    ap.add_argument("--days-lookback", dest="lookback", type=int, default=None)
    args = ap.parse_args()

    cfg = load_config()
    max_corr = args.max_corr if args.max_corr is not None else cfg.selection.max_correlation
    cap = args.cap if args.cap is not None else cfg.selection.max_per_cluster
    lookback = args.lookback or cfg.selection.correlation_lookback_days

    days = [d.strip() for d in args.days.split(",") if d.strip()]
    trades = load_trades(days)
    if not trades:
        print(f"No trades in {HISTORY} for {days}")
        return

    print(f"Replaying max_correlation={max_corr} max_per_cluster={cap} "
          f"lookback={lookback}d\n")
    grand_actual = grand_capped = 0.0
    for day in days:
        rows = trades.get(day)
        if not rows:
            print(f"{day}: no trades\n")
            continue
        syms = [s for s, _ in rows]
        try:
            rets = fetch_returns(syms, day, lookback)
        except Exception as e:  # noqa: BLE001
            print(f"{day}: could not fetch returns ({e})")
            continue

        # Replay the greedy cap in the order the trades were actually opened,
        # which is the best available proxy for selection rank.
        kept: list[tuple[str, float]] = []
        dropped: list[tuple[str, float, str, float]] = []
        for sym, pnl in rows:
            mine = rets.get(sym)
            if not mine:
                kept.append((sym, pnl))
                continue
            peers = []
            for ksym, _ in kept:
                other = rets.get(ksym)
                if not other:
                    continue
                c = pearson(*aligned_returns(mine, other))
                if c is not None and c >= max_corr:
                    peers.append((ksym, c))
            if len(peers) >= cap:
                worst = max(peers, key=lambda p: p[1])
                dropped.append((sym, pnl, worst[0], worst[1]))
            else:
                kept.append((sym, pnl))

        actual = sum(p for _, p in rows)
        capped = sum(p for _, p in kept)
        grand_actual += actual
        grand_capped += capped
        missing = [s for s in syms if s not in rets]
        print(f"{day}: {len(rows)} trades, actual {actual:+.2f}  ->  "
              f"{len(kept)} trades, with cap {capped:+.2f}   "
              f"({capped - actual:+.2f})")
        for sym, pnl, peer, c in dropped:
            flag = "  <-- DROPPED A WINNER" if pnl > 0 else ""
            print(f"     drop {sym:<6} {pnl:>+8.2f}  (corr {c:.2f} with {peer}){flag}")
        if missing:
            print(f"     no history, never capped: {', '.join(missing)}")
        print()

    print("=" * 60)
    print(f"TOTAL   actual {grand_actual:+.2f}   with cap {grand_capped:+.2f}   "
          f"({grand_capped - grand_actual:+.2f})")
    print("\nSelection-only replay: assumes surviving trades are unchanged and")
    print("ignores that a freed slot might have been filled by another name.")
    print("Dropped winners are counted against the cap, not excused.")


if __name__ == "__main__":
    main()
