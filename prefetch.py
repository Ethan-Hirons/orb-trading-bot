#!/usr/bin/env python3
"""Bulk-fetch market data into the unified pipeline cache.

The point is sample size. 150 real trades cannot validate anything
(ANALYSIS-2026-10-25); the way out is simulating years of sessions, and that
is only practical if the bars are already local.

    python prefetch.py --coverage              # what is cached, fetch nothing
    python prefetch.py --migrate               # import .replay_cache, free
    python prefetch.py --from-trades           # every symbol-day actually traded
    python prefetch.py --from-targets --days 90   # every symbol the SCREENER saw
    python prefetch.py --symbols SOXL,INTC --start 2026-01-01 --end 2026-10-24

--from-targets is the valuable one: it covers names the bot screened but
never entered, which is the only way to simulate decisions it did not make
(and the data E8 needs).

Fetching is incremental and resumable - anything already cached is skipped,
so re-running after an interruption costs nothing.
"""
import argparse
import json
import re
import sys
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import LOGS, REPO, STATE          # noqa: E402
from pipeline.store import Store, missing       # noqa: E402

TARGET_RE = re.compile(r"\[INFO\] TARGET (\w+) \|")
RESCREEN_RE = re.compile(r"added (\w+)\(")
LOG_RE = re.compile(r"orb_(\d{4}-\d{2}-\d{2})\.log$")


def pairs_from_trades() -> list[tuple[str, str]]:
    import csv
    f = STATE / "trade_history.csv"
    if not f.exists():
        return []
    with open(f, newline="", encoding="utf-8", errors="ignore") as fh:
        return sorted({(r["symbol"], r["date"]) for r in csv.DictReader(fh)})


def pairs_from_targets(days: int | None = None) -> list[tuple[str, str]]:
    """Every symbol the screener put on the board, per session."""
    cutoff = ((date.today() - timedelta(days=days)).isoformat()
              if days else "0000-00-00")
    out: set[tuple[str, str]] = set()
    for p in sorted(LOGS.glob("orb_*.log")):
        m = LOG_RE.search(p.name)
        if not m or m.group(1) < cutoff:
            continue
        day = m.group(1)
        for ln in open(p, errors="ignore"):
            for rx in (TARGET_RE, RESCREEN_RE):
                hit = rx.search(ln)
                if hit:
                    out.add((hit.group(1), day))
    return sorted(out)


def pairs_from_range(symbols: list[str], start: str,
                     end: str) -> list[tuple[str, str]]:
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    days = []
    d = d0
    while d <= d1:
        if d.weekday() < 5:          # holidays just return no bars
            days.append(d.isoformat())
        d += timedelta(days=1)
    return [(s, day) for s in symbols for day in days]


def migrate(store: Store) -> int:
    """Import the legacy .replay_cache into the unified store.

    Those files are named <SYMBOL>_<DAY>_<FEED>_v2.json and hold exactly the
    schema pipeline.sources.minute_bars produces, so this is a pure copy -
    no refetch, no network. Originals are left in place (rm is blocked on
    some mounts, and keeping them costs nothing).
    """
    legacy = REPO / ".replay_cache"
    if not legacy.exists():
        print("no .replay_cache to migrate")
        return 0
    n = 0
    for f in legacy.glob("*.json"):
        parts = f.stem.split("_")
        if len(parts) < 3:
            continue
        symbol, day, feed = parts[0], parts[1], parts[2]
        if store.has("minute_bars", symbol, day, feed):
            continue
        try:
            store.write("minute_bars", symbol, day,
                        json.loads(f.read_text()), feed)
            n += 1
        except Exception as e:
            print(f"  ! {f.name}: {e}")
    print(f"migrated {n} cached session(s) from .replay_cache")
    return n


def coverage(store: Store, pairs: list[tuple[str, str]], feed: str) -> None:
    gaps = missing(store, "minute_bars", pairs, feed)
    have = len(pairs) - len(gaps)
    inv = store.inventory()
    print(f"universe      {len(pairs)} symbol-days, "
          f"{len({s for s, _ in pairs})} symbols")
    print(f"cached        {have} ({have / max(len(pairs), 1):.0%})")
    print(f"to fetch      {len(gaps)}")
    print(f"store         {inv['entries']} entries, "
          f"{inv['bytes'] / 1e6:.1f} MB, {len(inv['symbols'])} symbols")
    by_day: OrderedDict = OrderedDict()
    for _, d in gaps:
        by_day[d] = by_day.get(d, 0) + 1
    if by_day:
        worst = sorted(by_day.items(), key=lambda kv: -kv[1])[:5]
        print("biggest gaps  " + ", ".join(f"{d} ({n})" for d, n in worst))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-trades", action="store_true")
    ap.add_argument("--from-targets", action="store_true")
    ap.add_argument("--symbols", default=None, help="comma-separated")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--days", type=int, default=None,
                    help="limit --from-targets to the last N days")
    ap.add_argument("--feed", default="sip", choices=["sip", "iex"])
    ap.add_argument("--coverage", action="store_true",
                    help="report only, fetch nothing")
    ap.add_argument("--migrate", action="store_true")
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after N fetches (for a cautious first run)")
    ap.add_argument("--sleep", type=float, default=0.25,
                    help="seconds between fetches")
    a = ap.parse_args()

    store = Store()
    if a.migrate:
        migrate(store)

    pairs: list[tuple[str, str]] = []
    if a.from_trades:
        pairs += pairs_from_trades()
    if a.from_targets:
        pairs += pairs_from_targets(a.days)
    if a.symbols and a.start and a.end:
        pairs += pairs_from_range([s.strip().upper()
                                   for s in a.symbols.split(",")],
                                  a.start, a.end)
    pairs = sorted(set(pairs))
    if not pairs:
        if a.migrate:
            return 0
        ap.error("pick a universe: --from-trades, --from-targets, "
                 "or --symbols with --start/--end")

    coverage(store, pairs, a.feed)
    if a.coverage:
        return 0

    gaps = missing(store, "minute_bars", pairs, a.feed)
    if not gaps:
        print("nothing to fetch")
        return 0
    if a.limit:
        gaps = gaps[:a.limit]

    from pipeline.sources import minute_bars
    cli = __import__("pipeline.sources", fromlist=["client"]).client()
    print(f"\nfetching {len(gaps)} session(s), feed={a.feed}")
    t0 = time.time()
    empty = 0
    for i, (sym, day) in enumerate(gaps, 1):
        rows = store.get("minute_bars", sym, day,
                         lambda s=sym, d=day: minute_bars(cli, s, d, a.feed),
                         feed=a.feed)
        if not rows:
            empty += 1
        if i % 25 == 0 or i == len(gaps):
            print(f"  {i}/{len(gaps)}  {sym} {day}  "
                  f"({time.time() - t0:.0f}s, {empty} empty)")
        time.sleep(a.sleep)
    print(f"\ndone in {time.time() - t0:.0f}s. {store.stats()}")
    print(f"{empty} session(s) returned no bars "
          f"(holiday, halt, or not listed yet)")
    inv = store.inventory()
    print(f"store now {inv['entries']} entries, {inv['bytes'] / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
