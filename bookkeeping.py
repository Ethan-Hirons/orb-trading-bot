"""Trial bookkeeping: read-only report over logs/ + state/trade_history.csv.

Usage:  python bookkeeping.py

Changes nothing. For each trading day it reports targets picked, trades
taken, WHY entries were skipped, and HOW each trade exited:
  take-profit / stop / trail / news-exit / time-stop / eod-flatten / breaker
Then running totals for the paper trial.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
HISTORY = ROOT / "state" / "trade_history.csv"

RE_TARGETS = re.compile(r"Today's targets: (.+)$")
RE_ENTER = re.compile(
    r"ENTER (LONG|SHORT) (\w+) qty=(\d+) ref=([\d.]+) "
    r"TP=([\d.]+)\(\+([\d.]+)%\) SL=([\d.]+)\(-([\d.]+)%\)"
)
RE_BIAS_SKIP = re.compile(r"(\w+) broke out (\w+) but news bias is (\w+); skipping")
RE_NO_PLAN = re.compile(r"(\w+) breakout (\w+) but no valid trade plan")
RE_NEWS_EXIT = re.compile(r"ADVERSE NEWS EXIT: closing (\w+) (\w+)")
RE_TIME_STOP = re.compile(r"TIME STOP (\w+): held")
# v1.11: native server-side trailing stops.
RE_TRAIL_EXIT = re.compile(r"TRAIL EXIT (\w+): trailing stop filled")
RE_BREAKER = re.compile(r"CIRCUIT BREAKER: (.+)$")
RE_DUP_UNDERLYING = re.compile(r"DUP-UNDERLYING (\w+): same underlying (\w+)")
# v1.9 dead-slot fix: short-biased names dropped at selection when shorts are off.
RE_DEAD_SLOT = re.compile(r"DEAD SLOT (\w+): short bias but shorts are disabled")
# v1.14 correlated-theme cap
RE_CORRELATED = re.compile(r"CORRELATED (\w+): ([\d.]+) correlation with (\w+)")
RE_DAY_DONE = re.compile(
    r"Day done\. Trades: (\d+) \| End equity: ([\d.]+) \| PnL: ([+-][\d.]+) \(([+-][\d.]+)%\)"
)


def parse_day(path: Path) -> dict:
    d = {
        "date": path.stem.replace("orb_", ""),
        "targets": [], "entries": {}, "bias_skips": set(), "no_plan": set(),
        "news_exits": set(), "time_stops": set(), "breaker": None, "done": None,
        "dup_underlying": {}, "dead_slots": set(), "trail_exits": set(),
        "correlated": {},
    }
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if m := RE_TARGETS.search(line):
            d["targets"] = [t.strip() for t in m.group(1).split(",") if "(" in t]
        elif m := RE_ENTER.search(line):
            d["entries"][m.group(2)] = {
                "side": m.group(1).lower(), "tp_pct": float(m.group(6)),
                "sl_pct": float(m.group(8)),
            }
        elif m := RE_BIAS_SKIP.search(line):
            d["bias_skips"].add(m.group(1))
        elif m := RE_NO_PLAN.search(line):
            d["no_plan"].add(m.group(1))
        elif m := RE_NEWS_EXIT.search(line):
            d["news_exits"].add(m.group(2))
        elif m := RE_TIME_STOP.search(line):
            d["time_stops"].add(m.group(1))
        elif m := RE_TRAIL_EXIT.search(line):
            d["trail_exits"].add(m.group(1))
        elif m := RE_DUP_UNDERLYING.search(line):
            d["dup_underlying"][m.group(1)] = m.group(2)
        elif m := RE_DEAD_SLOT.search(line):
            d["dead_slots"].add(m.group(1))
        elif m := RE_CORRELATED.search(line):
            d["correlated"][m.group(1)] = (m.group(3), m.group(2))
        elif m := RE_BREAKER.search(line):
            d["breaker"] = m.group(1)
        elif m := RE_DAY_DONE.search(line):
            d["done"] = {
                "trades": int(m.group(1)), "equity": float(m.group(2)),
                "pnl": float(m.group(3)), "pnl_pct": float(m.group(4)),
            }
    return d


def classify_exit(row: dict, day: dict) -> str:
    sym = row["symbol"]
    pnl_pct = float(row["pnl_pct"])
    if sym in day["news_exits"]:
        return "news-exit"
    if sym in day["time_stops"]:
        return "time-stop"  # closed by the 180-min hold cap (neither TP nor SL hit)
    if day["breaker"]:
        return "breaker"
    if sym in day["trail_exits"]:
        return "trail"
    e = day["entries"].get(sym)
    # Within 90% of the bracket level counts as that bracket firing.
    if e and pnl_pct >= 0.9 * e["tp_pct"]:
        return "take-profit"
    if e and pnl_pct <= -0.9 * e["sl_pct"]:
        return "stop"
    return "eod-flatten"


def main() -> None:
    history: dict[str, list[dict]] = {}
    if HISTORY.exists():
        with open(HISTORY, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                history.setdefault(row["date"], []).append(row)

    exit_types: Counter = Counter()
    skip_reasons: Counter = Counter()
    pnls: list[float] = []
    total_targets = total_trades = 0

    print(f"{'DATE':<12}{'TGTS':>5}{'TRADES':>7}{'PNL':>9}  DETAIL")
    for path in sorted(LOGS.glob("orb_*.log")):
        day = parse_day(path)
        if not day["done"] and not day["entries"]:
            continue  # day never traded (crash / not armed / pre-open only)
        rows = history.get(day["date"], [])
        details = []
        for row in rows:
            kind = classify_exit(row, day)
            exit_types[kind] += 1
            pnls.append(float(row["pnl"]))
            details.append(f"{row['symbol']} {row['side']} {float(row['pnl']):+.2f} ({kind})")
        for s in day["bias_skips"]:
            skip_reasons["bias-mismatch"] += 1
            details.append(f"{s} skipped (bias)")
        for s in day["no_plan"]:
            skip_reasons["no-valid-plan"] += 1
        for s, key in day["dup_underlying"].items():
            skip_reasons["dup-underlying"] += 1
            details.append(f"{s} deduped (same underlying {key})")
        for s in day["dead_slots"]:
            skip_reasons["dead-slot-short-bias"] += 1
            details.append(f"{s} dropped (short bias, shorts off)")
        for s, (peer, corr) in day["correlated"].items():
            skip_reasons["correlated-theme"] += 1
            details.append(f"{s} dropped (corr {corr} with {peer})")
        untouched = (
            {t.split("(")[0] for t in day["targets"]}
            - set(day["entries"]) - day["bias_skips"] - day["no_plan"]
        )
        skip_reasons["no-breakout"] += len(untouched)
        if day["breaker"]:
            details.append(f"BREAKER: {day['breaker']}")

        total_targets += len(day["targets"])
        total_trades += len(rows)
        done = day["done"]
        pnl_s = f"{done['pnl']:+8.2f}" if done else "  (open?)"
        print(f"{day['date']:<12}{len(day['targets']):>5}{len(rows):>7}{pnl_s}  "
              + ("; ".join(details) or "-"))

    print("\n--- TRIAL TOTALS ---")
    print(f"days={sum(1 for _ in LOGS.glob('orb_*.log'))} "
          f"targets={total_targets} completed_trades={total_trades}")
    if pnls:
        wins = [p for p in pnls if p > 0]
        print(f"net_pnl={sum(pnls):+.2f} win_rate={len(wins)}/{len(pnls)} "
              f"avg_win={sum(wins)/len(wins) if wins else 0:+.2f} "
              f"avg_loss={(sum(p for p in pnls if p <= 0)/max(1, len(pnls)-len(wins))):+.2f}")
    print(f"exit_types={dict(exit_types)}")
    print(f"skip_reasons={dict(skip_reasons)}")
    print(f"\nTrades until trial target (30): {max(0, 30 - total_trades)}")


if __name__ == "__main__":
    main()
