"""Loss anatomy: where the stops come from. Read-only, stdlib only.

Usage:  python loss_anatomy.py              (all trades)
        python loss_anatomy.py --since 2026-09-23   (forward test, PREREG-2026-09-22.md)

Joins state/trade_history.csv with state/trade_features.csv and the TARGET /
ENTER / RELVOL lines in logs/, classifies exits with bookkeeping.py's own
classify_exit, and prints the buckets the pre-registered hypotheses are
about. Changes nothing.

Known data issue it works around: trade_features.csv gained a column in
v1.12 (rel_volume_open) but its header row was never rewritten, so rows from
2026-08-13 on have 18 fields under a 17-field header.
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics as st
from collections import defaultdict
from pathlib import Path

import bookkeeping as bk

ROOT = Path(__file__).resolve().parent
F18 = ["date", "entry_time_et", "symbol", "side", "qty", "entry_ref", "or_pct",
       "gap_pct", "sentiment", "n_articles", "rel_volume", "rel_volume_open",
       "spread_pct", "minutes_after_open", "tp_pct", "stop_pct", "bias",
       "added_intraday"]

# Leveraged / inverse single-stock and index ETPs seen in this trial. The bot
# itself only knows these via Alpaca asset titles at runtime (selection.py).
LEVERAGED = {"TQQQ", "SQQQ", "SOXL", "SOXS", "TSLL", "CONL", "SNXX", "SNDQ",
             "NBIZ", "NBIL", "NEBX", "SMCX", "SMCL", "IREX", "IREG", "IRE",
             "CWVX", "PLTZ", "PYPG"}
CRYPTO_ETF = {"BITO", "IBIT", "ETHA"}

RE_T = re.compile(r"TARGET (\w+) \| gap ([+-][\d.]+)%, sentiment [+-][\d.]+ "
                  r"\((\d+) articles\), bias (\w+) \| score ([\d.]+)")
RE_E = re.compile(r"ENTER (LONG|SHORT) (\w+) qty=\d+ ref=[\d.]+ "
                  r"TP=[\d.]+\(\+[\d.]+%\) SL=[\d.]+\(-([\d.]+)%\)")


def load_features() -> dict:
    out: dict = defaultdict(list)
    path = ROOT / "state" / "trade_features.csv"
    if not path.exists():
        return out
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) == 17:
                row = row[:11] + [""] + row[11:]
            d = dict(zip(F18, row))
            out[(d["date"], d["symbol"], d["side"])].append(d)
    return out


def load_log_features() -> dict:
    """(date, symbol) -> bias / articles / score / stop, from the log itself."""
    out = {}
    for p in sorted((ROOT / "logs").glob("orb_*.log")):
        date, tgt = p.stem.replace("orb_", ""), {}
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if m := RE_T.search(ln):
                tgt[m[1]] = dict(gap=float(m[2]), n_articles=int(m[3]),
                                 bias=m[4], score=float(m[5]))
            elif m := RE_E.search(ln):
                out[(date, m[2])] = {**tgt.get(m[2], {}), "stop_pct": float(m[3])}
    return out


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def build(since: str | None) -> list[dict]:
    days = {p.stem.replace("orb_", ""): bk.parse_day(p)
            for p in sorted((ROOT / "logs").glob("orb_*.log"))}
    feats, logf = load_features(), load_log_features()
    seen: dict = defaultdict(int)
    trades = []
    with open(ROOT / "state" / "trade_history.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if since and row["date"] < since:
                continue
            key = (row["date"], row["symbol"], row["side"])
            i = seen[key]; seen[key] += 1
            fr = feats[key][i] if i < len(feats[key]) else {}
            lf = logf.get((row["date"], row["symbol"]), {})
            stop = fnum(fr.get("stop_pct")) or lf.get("stop_pct")
            day = days.get(row["date"])
            t = {
                "date": row["date"], "symbol": row["symbol"], "side": row["side"],
                "pnl": float(row["pnl"]), "pnl_pct": float(row["pnl_pct"]),
                "exit": bk.classify_exit(row, day) if day else "no-log",
                "stop_pct": stop,
                "bias": fr.get("bias") or lf.get("bias"),
                "or_pct": fnum(fr.get("or_pct")),
                "instrument": ("leveraged ETP" if row["symbol"] in LEVERAGED
                               else "crypto ETF" if row["symbol"] in CRYPTO_ETF
                               else "stock"),
            }
            t["R"] = t["pnl_pct"] / stop if stop else None
            trades.append(t)
    return trades


def bucket_table(rows: list[dict], key, title: str) -> None:
    groups: dict = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    print(f"\n{title}")
    print(f"  {'bucket':<44}{'n':>4}{'stops':>7}{'meanR':>8}{'net $':>9}")
    for k in sorted(groups):
        g = groups[k]
        rs = [r["R"] for r in g if r["R"] is not None]
        stops = sum(r["exit"] == "stop" for r in g)
        print(f"  {k:<44}{len(g):>4}{stops / len(g):>7.0%}"
              f"{(st.mean(rs) if rs else 0):>+8.2f}{sum(r['pnl'] for r in g):>+9.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", help="only trades on/after YYYY-MM-DD")
    args = ap.parse_args()
    rows = [r for r in build(args.since) if r["side"] == "long"
            and r["stop_pct"] == 1.5 and r["date"] != "2026-08-03"]  # 08-03 = broken-trail artifact
    print(f"long, 1.5% stop, ex-2026-08-03: {len(rows)} trades, "
          f"net {sum(r['pnl'] for r in rows):+.2f}")
    bucket_table(rows, lambda r: r["exit"], "By exit type")
    bucket_table(rows, lambda r: f"{r['instrument']} / "
                 f"{'no directional news' if r['bias'] == 'any' else 'news-confirmed'}",
                 "H1 buckets (instrument x news)")
    ratio = defaultdict(list)
    for r in rows:
        if r["or_pct"]:
            ratio[r["exit"] == "stop"].append(r["or_pct"] / r["stop_pct"])
    if ratio[True] and ratio[False]:
        print(f"\nH2: opening-range width / stop, median — stopped "
              f"{st.median(ratio[True]):.2f}x (n={len(ratio[True])}) vs not "
              f"{st.median(ratio[False]):.2f}x (n={len(ratio[False])})")


if __name__ == "__main__":
    main()
