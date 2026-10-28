"""H-V (PREREG-EDGE-SCAN-2026-10-27, addendum B): SIP opening relative volume.

    python -u relvol_sip.py fetch    # SIP 09:30-09:34 volume -> relvol_sip.json
    python -u relvol_sip.py report   # join onto edge_signals.csv and score H-V

relvol = volume(09:30-09:34, SIP) / mean of the same window over the prior 14
sessions (at least 10 present). Read-only research; touches nothing live.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import edge_scan as es

OUT = Path("relvol_sip.json")
LOOKBACK = 14


def fetch() -> None:
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from alpaca.data.enums import DataFeed
    from pipeline import sources

    cli = sources.client()
    sig = [r for r in es._load() if r["side"] == "up"]
    sig_days = sorted({r["day"] for r in sig})

    # Trading calendar from SPY daily bars, with room for the lookback.
    first = (datetime.strptime(sig_days[0], "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    spy = sources.daily_bars(cli, "SPY", first, sig_days[-1])
    cal = sorted({b["d"] for b in spy})
    idx = {d: i for i, d in enumerate(cal)}

    # Which symbols each calendar day must cover: the signal day plus the
    # LOOKBACK sessions before it.
    need: dict[str, set] = {}
    for r in sig:
        i = idx.get(r["day"])
        if i is None:
            continue
        for d in cal[max(0, i - LOOKBACK): i + 1]:
            need.setdefault(d, set()).add(r["symbol"])

    vols: dict[str, dict[str, int]] = json.loads(OUT.read_text()) if OUT.exists() else {}
    days = sorted(need)
    for n, d in enumerate(days):
        if d in vols:
            continue
        dd = datetime.strptime(d, "%Y-%m-%d").date()
        syms = sorted(need[d])
        vols[d] = {}
        for k in range(0, len(syms), 150):
            chunk = syms[k:k + 150]
            try:
                req = StockBarsRequest(
                    symbol_or_symbols=chunk,
                    timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                    start=datetime.combine(dd, sources.MARKET_OPEN, tzinfo=sources.ET),
                    end=datetime.combine(dd, sources.MARKET_OPEN, tzinfo=sources.ET)
                    + timedelta(minutes=5),
                    feed=DataFeed.SIP,
                )
                data = cli.get_stock_bars(req).data
            except Exception as e:  # noqa: BLE001
                print(f"{d}: {e}", flush=True)
                continue
            for s, bars in data.items():
                for b in bars:
                    if b.timestamp.astimezone(sources.ET).strftime("%H:%M") == "09:30":
                        vols[d][s] = int(b.volume)
        if n % 20 == 0:
            OUT.write_text(json.dumps(vols))
            print(f"  {n}/{len(days)} {d} {len(vols[d])} symbols", flush=True)
    OUT.write_text(json.dumps(vols))
    print(f"wrote {OUT}: {len(vols)} days")


def _relvol(vols, cal, day, sym):
    i = cal.index(day) if day in cal else None
    if i is None or sym not in vols.get(day, {}):
        return None
    hist = [vols[d][sym] for d in cal[max(0, i - LOOKBACK): i] if sym in vols.get(d, {})]
    if len(hist) < 10 or sum(hist) == 0:
        return None
    return vols[day][sym] / (sum(hist) / len(hist))


def report() -> None:
    vols = json.loads(OUT.read_text())
    cal = sorted(vols)
    rows = [r for r in es._load() if r["side"] == "up"]
    for r in rows:
        r["_rv"] = _relvol(vols, cal, r["day"], r["symbol"])
    have = [r for r in rows if r["_rv"] is not None]
    print(f"signals with SIP relvol: {len(have)} of {len(rows)}")
    disc = [r for r in have if r["day"] < es.HOLDOUT_START]
    hold = [r for r in have if r["day"] >= es.HOLDOUT_START]
    for k in ("eod", "x_live"):
        print(f"\nmetric {k}: top vs bottom tercile of SIP relvol")
        res = []
        for name, part in (("discovery", disc), ("holdout", hold)):
            v = sorted(r["_rv"] for r in part)
            t1, t2 = v[len(v) // 3], v[2 * len(v) // 3]
            A = [r for r in part if r["_rv"] >= t2]
            B = [r for r in part if r["_rv"] <= t1]
            d, lo, hi = es.boot_diff(A, B, k)
            ma, _, _ = es.boot_ci(A, k)
            mb, _, _ = es.boot_ci(B, k)
            res.append((d, lo))
            print(f"  {name:9} terciles {t1:.2f}/{t2:.2f}  top n={len(A)} {ma:+.3f}  "
                  f"bottom n={len(B)} {mb:+.3f}  diff {d:+.3f} [{lo:+.3f},{hi:+.3f}]")
        if k == "eod":
            ok = res[0][0] > 0 and res[1][0] > 0 and res[1][1] > 0
            print("  H-V:", "PASS" if ok else "FAIL")
        print("  descriptive, paper's absolute cut (relvol >= 1 vs < 1):")
        for name, part in (("discovery", disc), ("holdout", hold)):
            A = [r for r in part if r["_rv"] >= 1]
            B = [r for r in part if r["_rv"] < 1]
            d, lo, hi = es.boot_diff(A, B, k)
            print(f"    {name:9} >=1 n={len(A)}  <1 n={len(B)}  diff {d:+.3f} [{lo:+.3f},{hi:+.3f}]")
    # gradient, all periods, descriptive
    print("\nrelvol gradient (all periods, EOD and live-exit means):")
    for lo_, hi_ in ((0, 1), (1, 2), (2, 4), (4, 8), (8, 1e9)):
        part = [r for r in have if lo_ <= r["_rv"] < hi_]
        if len(part) >= 20:
            m, a, b = es.boot_ci(part, "eod")
            x, _, _ = es.boot_ci(part, "x_live")
            print(f"  [{lo_:>3},{hi_:>5}) n={len(part):5d}  eod {m:+.3f} [{a:+.3f},{b:+.3f}]  live {x:+.3f}")


if __name__ == "__main__":
    {"fetch": fetch, "report": report}[sys.argv[1]]()
