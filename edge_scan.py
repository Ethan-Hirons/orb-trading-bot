"""Edge scan: does the ORB entry signal have drift? See PREREG-EDGE-SCAN-2026-10-27.md.

Two stages, so the expensive pickle load runs once:

    python -u edge_scan.py extract     # .sweep_cache/*.pkl -> edge_signals.csv
    python -u edge_scan.py report      # read the CSV, print every table

Nothing here touches config.yaml, the bot, or the broker. Read-only research.
"""

from __future__ import annotations

import csv
import glob
import math
import pickle
import random
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OUT = Path("edge_signals.csv")

OR_MIN = 15            # opening range length, minutes
WINDOW_END = 75        # last minute (from 09:30) a breakout may trigger
VOL_RATIO = 0.5        # breakout bar volume >= this x mean OR bar volume
MIN_OR_BARS = 8        # skip thin IEX coverage
ENTRY_COST = 0.0005    # measured mean entry slippage (E1)
EXIT_COST = 0.0005     # spread/overshoot on the way out
EOD_MIN = 380          # 15:50, the bot's flatten time
HORIZONS = (5, 15, 30, 60, 120)
HOLDOUT_START = "2026-01-01"


def _minute(ts) -> int:
    t = ts.astimezone(ET)
    return (t.hour - 9) * 60 + t.minute - 30


def _exit_sim(bars, i0, entry, stop_pct, trail_pct=None, arm_pct=None):
    """Walk 1-min bars after entry. Stop checked against the bar low BEFORE the
    peak is updated with the bar high (pessimistic ordering). Returns net %."""
    stop = entry * (1 - stop_pct / 100)
    peak = entry
    for m, b in bars[i0 + 1:]:
        if m >= EOD_MIN:
            return 100 * (b.open / entry - 1) - 100 * EXIT_COST
        if b.low <= stop:
            px = min(stop, b.open) * (1 - 0.0008)   # E11: median overshoot 0.08pp
            return 100 * (px / entry - 1)
        peak = max(peak, b.high)
        if trail_pct and peak >= entry * (1 + arm_pct / 100):
            stop = max(stop, peak * (1 - trail_pct / 100))
    last = bars[-1][1]
    return 100 * (last.close / entry - 1) - 100 * EXIT_COST


def extract() -> None:
    import __main__
    sys.path.insert(0, ".")
    import sweep
    __main__._Bar = sweep._Bar   # the pickles were written from sweep.py's __main__

    rows = []
    for f in sorted(glob.glob(".sweep_cache/prefetch_*.pkl")):
        print(f"loading {f} ...", flush=True)
        cache = pickle.loads(Path(f).read_bytes())
        for entry_tuple in cache:
            day, cands, news, minute, atr = entry_tuple[:5]
            for c in cands:
                raw = minute.get(c.symbol) or []
                bars = [(_minute(b.timestamp), b) for b in raw]
                bars = [x for x in bars if 0 <= x[0] < 390]
                orb = [b for m, b in bars if m < OR_MIN]
                if len(orb) < MIN_OR_BARS or orb[0].open < 5:
                    continue
                hi = max(b.high for b in orb)
                lo = min(b.low for b in orb)
                avg_v = sum(b.volume for b in orb) / len(orb)
                first5 = [b for m, b in bars if m < 5]
                green = first5[-1].close > first5[0].open if first5 else None

                cum_pv = sum((b.high + b.low + b.close) / 3 * b.volume for b in orb)
                cum_v = sum(b.volume for b in orb)
                for side in ("up", "down"):
                    row = _signal_row(day, c, news, atr, bars, side, hi, lo, avg_v,
                                      green, cum_pv, cum_v)
                    if row:
                        rows.append(row)
        del cache
        print(f"  signals so far: {len(rows)}", flush=True)

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} signals -> {OUT}")


def _signal_row(day, c, news, atr, bars, side, hi, lo, avg_v, green, cum_pv, cum_v):
    """One LONG entry per side: 'up' = close above the OR high (the bot's
    breakout), 'down' = close below the OR low (buying the downside break)."""
    sig = None
    for i, (m, b) in enumerate(bars):
        if m < OR_MIN:
            continue
        cum_pv += (b.high + b.low + b.close) / 3 * b.volume
        cum_v += b.volume
        if m >= WINDOW_END:
            break
        hit = b.close > hi if side == "up" else b.close < lo
        if hit and b.volume >= VOL_RATIO * avg_v:
            sig = (i, m, b, cum_pv / cum_v if cum_v else b.close)
            break
    if sig is None:
        return None
    i0, m0, b0, vwap = sig
    entry = b0.close * (1 + ENTRY_COST)

    def ret_at(h):
        tgt = m0 + h
        if tgt >= EOD_MIN:
            return None
        for m, b in bars[i0 + 1:]:
            if m >= tgt:
                return 100 * (b.close / entry - 1)
        return None

    eod_bar = next((b for m, b in bars if m >= EOD_MIN), bars[-1][1])
    after = [b for m, b in bars[i0 + 1:] if m < EOD_MIN]
    atr_pct = atr.get(c.symbol) if isinstance(atr, dict) else None
    return {
        "side": side,
        "day": str(day), "symbol": c.symbol,
        "price": round(b0.close, 4), "gap_pct": round(c.gap_pct, 3),
        "rel_volume": round(getattr(c, "rel_volume", 0) or 0, 3),
        "news": round(getattr(news.get(c.symbol), "score", 0) or 0, 3)
        if isinstance(news, dict) else 0,
        "atr_pct": round(atr_pct, 4) if atr_pct else "",
        "or_width_pct": round(100 * (hi - lo) / lo, 4),
        "green5": int(bool(green)) if green is not None else "",
        "above_vwap": int(b0.close > vwap),
        "brk_min": m0,
        "brk_vol_ratio": round(b0.volume / avg_v, 3) if avg_v else "",
        "mfe": round(100 * (max(b.high for b in after) / entry - 1), 4) if after else 0,
        "mae": round(100 * (min(b.low for b in after) / entry - 1), 4) if after else 0,
        **{f"r{h}": (round(r, 4) if (r := ret_at(h)) is not None else "")
           for h in HORIZONS},
        "eod": round(100 * (eod_bar.open / entry - 1) - 100 * EXIT_COST, 4),
        "x_live": round(_exit_sim(bars, i0, entry, 1.5, 1.25, 1.25), 4),
        "x_hold15": round(_exit_sim(bars, i0, entry, 1.5), 4),
        "x_hold25": round(_exit_sim(bars, i0, entry, 2.5), 4),
        "x_atr": round(_exit_sim(bars, i0, entry, max(1.5, atr_pct)), 4)
        if atr_pct and atr_pct > 0.2 else "",
    }


# ---------------------------------------------------------------- report

def _load():
    with OUT.open() as fh:
        return list(csv.DictReader(fh))


def _f(r, k):
    v = r.get(k, "")
    return float(v) if v not in ("", None) else None


def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def boot_ci(rows, key, n=2000, seed=7):
    """Day-clustered bootstrap CI for the mean of `key`."""
    by_day = {}
    for r in rows:
        v = _f(r, key)
        if v is not None:
            by_day.setdefault(r["day"], []).append(v)
    days = list(by_day)
    if len(days) < 5:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        s = c = 0
        for d in (rng.choice(days) for _ in days):
            s += sum(by_day[d]); c += len(by_day[d])
        stats.append(s / c)
    stats.sort()
    allv = [v for d in days for v in by_day[d]]
    return _mean(allv), stats[int(0.025 * n)], stats[int(0.975 * n)]


def boot_diff(a_rows, b_rows, key, n=2000, seed=11):
    """Day-clustered bootstrap CI for mean(A) - mean(B). Days resampled jointly."""
    def by_day(rows):
        d = {}
        for r in rows:
            v = _f(r, key)
            if v is not None:
                d.setdefault(r["day"], []).append(v)
        return d
    A, B = by_day(a_rows), by_day(b_rows)
    days = sorted(set(A) | set(B))
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        sa = ca = sb = cb = 0
        for d in (rng.choice(days) for _ in days):
            if d in A: sa += sum(A[d]); ca += len(A[d])
            if d in B: sb += sum(B[d]); cb += len(B[d])
        if ca and cb:
            stats.append(sa / ca - sb / cb)
    stats.sort()
    ma = _mean([v for x in A.values() for v in x])
    mb = _mean([v for x in B.values() for v in x])
    k = len(stats)
    if not k:
        return ma - mb, float("nan"), float("nan")
    return ma - mb, stats[int(0.025 * k)], stats[int(0.975 * k)]


def _terciles(rows, key):
    vals = sorted(v for r in rows if (v := _f(r, key)) is not None)
    if len(vals) < 9:
        return None, None
    return vals[len(vals) // 3], vals[2 * len(vals) // 3]


SPLITS = {
    "C1 gap up vs gap down":
        (lambda r, t: _f(r, "gap_pct") > 0, lambda r, t: _f(r, "gap_pct") < 0, None),
    "C2 first 5m green vs red":
        (lambda r, t: r["green5"] == "1", lambda r, t: r["green5"] == "0", None),
    "C3 above VWAP vs below":
        (lambda r, t: r["above_vwap"] == "1", lambda r, t: r["above_vwap"] == "0", None),
    "C4 OR/ATR narrow vs wide":
        (lambda r, t: r["_orr"] is not None and r["_orr"] <= t[0],
         lambda r, t: r["_orr"] is not None and r["_orr"] >= t[1], "_orr"),
    "C5 early (<=10:00) vs late (>10:15)":
        (lambda r, t: int(r["brk_min"]) <= 30, lambda r, t: int(r["brk_min"]) > 45, None),
    "C6 relvol top vs bottom":
        (lambda r, t: _f(r, "rel_volume") >= t[1], lambda r, t: _f(r, "rel_volume") <= t[0],
         "rel_volume"),
    "C7 brk-bar vol top vs bottom":
        (lambda r, t: _f(r, "brk_vol_ratio") >= t[1],
         lambda r, t: _f(r, "brk_vol_ratio") <= t[0], "brk_vol_ratio"),
}


def report() -> None:
    allrows = _load()
    rows = [r for r in allrows if r.get("side", "up") == "up"]
    for r in rows:
        a, w = _f(r, "atr_pct"), _f(r, "or_width_pct")
        r["_orr"] = w / a if a and a > 0.2 and w is not None else None
    disc = [r for r in rows if r["day"] < HOLDOUT_START]
    hold = [r for r in rows if r["day"] >= HOLDOUT_START]
    ndays = len({r["day"] for r in rows})
    print(f"signals: {len(rows)} over {ndays} days "
          f"(discovery {len(disc)}, holdout {len(hold)})\n")

    print("Q0 - drift of the raw long-breakout signal (% per trade, day-clustered 95% CI)")
    print(f"{'':10} {'horizon':>8} {'n':>6} {'mean':>8} {'CI lo':>8} {'CI hi':>8}")
    for label, part in (("ALL", rows), ("discovery", disc), ("holdout", hold)):
        for k in [f"r{h}" for h in HORIZONS] + ["eod"]:
            n = sum(1 for r in part if _f(r, k) is not None)
            m, lo, hi = boot_ci(part, k)
            print(f"{label:10} {k:>8} {n:6d} {m:+8.3f} {lo:+8.3f} {hi:+8.3f}")
        print()

    print("Exit rules on the same signals (net %, day-clustered CI) - descriptive")
    for k, name in (("x_live", "stop1.5+trail1.25 (live)"), ("x_hold15", "stop1.5 hold"),
                    ("x_hold25", "stop2.5 hold"), ("x_atr", "stop max(1.5,ATR) hold"),
                    ("eod", "no stop, hold")):
        out = []
        for part in (rows, disc, hold):
            m, lo, hi = boot_ci(part, k)
            out.append(f"{m:+.3f} [{lo:+.3f},{hi:+.3f}]")
        print(f"  {name:26} all {out[0]:24} disc {out[1]:24} hold {out[2]}")
    print()

    print("Conditional splits - mean EOD return, A minus B (predicted A > B)")
    for name, (fa, fb, tkey) in SPLITS.items():
        res = []
        for part in (disc, hold):
            t = _terciles(part, tkey) if tkey else None
            A = [r for r in part if _safe(fa, r, t)]
            B = [r for r in part if _safe(fb, r, t)]
            d, lo, hi = boot_diff(A, B, "eod")
            res.append((len(A), len(B), d, lo, hi,
                        _mean([_f(r, "eod") for r in A]), _mean([_f(r, "eod") for r in B])))
        (na, nb, dd, _, _, ma, mb), (ha, hb, hd, hlo, hhi, hma, hmb) = res
        ok = dd > 0 and hd > 0 and hlo > 0   # nan compares False -> fail
        print(f"  {name:34} disc A{na:5d} {ma:+.3f} B{nb:5d} {mb:+.3f} d={dd:+.3f} | "
              f"hold A{ha:5d} {hma:+.3f} B{hb:5d} {hmb:+.3f} d={hd:+.3f} "
              f"[{hlo:+.3f},{hhi:+.3f}]  {'PASS' if ok else 'fail'}")


def _safe(fn, r, t):
    try:
        return bool(fn(r, t))
    except (TypeError, ValueError):
        return False




def report_reversal() -> None:
    """ADDENDUM A, H-R: long on the first close BELOW the OR low."""
    rows = [r for r in _load() if r.get("side") == "down"]
    disc = [r for r in rows if r["day"] < HOLDOUT_START]
    hold = [r for r in rows if r["day"] >= HOLDOUT_START]
    print(f"H-R downside-break longs: {len(rows)} (discovery {len(disc)}, holdout {len(hold)})")
    print(f"{'':10} {'horizon':>8} {'n':>6} {'mean':>8} {'CI lo':>8} {'CI hi':>8}")
    for label, part in (("ALL", rows), ("discovery", disc), ("holdout", hold)):
        for k in [f"r{h}" for h in HORIZONS] + ["eod", "x_hold15", "x_live"]:
            n = sum(1 for r in part if _f(r, k) is not None)
            m, lo, hi = boot_ci(part, k)
            print(f"{label:10} {k:>8} {n:6d} {m:+8.3f} {lo:+8.3f} {hi:+8.3f}")
        print()
    md, _, _ = boot_ci(disc, "eod")
    mh, lo, _ = boot_ci(hold, "eod")
    print("H-R:", "PASS" if md > 0 and mh > 0 and lo > 0 else "FAIL",
          f"(disc {md:+.3f}, hold {mh:+.3f}, hold CI lo {lo:+.3f})")




# ---------------------------------------------------------------- H-G (addendum C)

GAP_OUT = Path("edge_gap.csv")


def extract_gap() -> None:
    """Every candidate with |gap| >= 5%: buy at the 09:30 bar close (and,
    descriptively, at 09:45), sell at the 15:50 bar open. No stop: drift only."""
    import __main__
    sys.path.insert(0, ".")
    import sweep
    __main__._Bar = sweep._Bar

    rows = []
    for f in sorted(glob.glob(".sweep_cache/prefetch_*.pkl")):
        print(f"loading {f} ...", flush=True)
        cache = pickle.loads(Path(f).read_bytes())
        for entry_tuple in cache:
            day, cands, _news, minute = entry_tuple[:4]
            for c in cands:
                if abs(c.gap_pct) < 5:
                    continue
                bars = [(_minute(b.timestamp), b) for b in (minute.get(c.symbol) or [])]
                bars = [x for x in bars if 0 <= x[0] < 390]
                orb = [b for m, b in bars if m < OR_MIN]
                if len(orb) < MIN_OR_BARS or orb[0].open < 5 or bars[0][0] > 1:
                    continue
                exit_bar = next((b for m, b in bars if m >= EOD_MIN), None)
                at45 = next((b for m, b in bars if m >= OR_MIN), None)
                if exit_bar is None or at45 is None:
                    continue
                out = exit_bar.open * (1 - EXIT_COST)
                e0 = bars[0][1].close * (1 + ENTRY_COST)
                e45 = at45.close * (1 + ENTRY_COST)
                rows.append({"day": str(day), "symbol": c.symbol,
                             "gap_pct": round(c.gap_pct, 3),
                             "eod": round(100 * (out / e0 - 1), 4),
                             "eod45": round(100 * (out / e45 - 1), 4)})
        del cache
        print(f"  rows so far: {len(rows)}", flush=True)
    with GAP_OUT.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} -> {GAP_OUT}")


def report_gap() -> None:
    with GAP_OUT.open() as fh:
        rows = list(csv.DictReader(fh))
    def part(pred):
        sel = [r for r in rows if pred(float(r["gap_pct"]))]
        return ([r for r in sel if r["day"] < HOLDOUT_START],
                [r for r in sel if r["day"] >= HOLDOUT_START])
    print("H-G: buy gap-downs <= -5% at the 09:30 bar close, sell 15:50 (net %, day-clustered CI)")
    for label, pred, key in (("gap <= -5  @09:30 [SCORED]", lambda g: g <= -5, "eod"),
                             ("gap <= -5  @09:45", lambda g: g <= -5, "eod45"),
                             ("gap <= -10 @09:30", lambda g: g <= -10, "eod"),
                             ("gap >= +5  @09:30 (mirror)", lambda g: g >= 5, "eod"),
                             ("gap >= +5  @09:45 (mirror)", lambda g: g >= 5, "eod45")):
        d, h = part(pred)
        md, dlo, dhi = boot_ci(d, key)
        mh, hlo, hhi = boot_ci(h, key)
        print(f"  {label:28} disc n={len(d):5d} {md:+.3f} [{dlo:+.3f},{dhi:+.3f}]  "
              f"hold n={len(h):5d} {mh:+.3f} [{hlo:+.3f},{hhi:+.3f}]")
        if "SCORED" in label:
            verdict = "PASS" if md > 0 and mh > 0 and hlo > 0 else "FAIL"
    print("H-G:", verdict)


if __name__ == "__main__":
    {"extract": extract, "report": report, "reversal": report_reversal,
     "extract_gap": extract_gap, "gap": report_gap}[sys.argv[1] if len(sys.argv) > 1 else "report"]()
