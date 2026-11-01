"""Monthly trend-following across asset-class ETFs (E18). See PREREG-TREND-2026-11-01.md.

    python -u trend.py fetch     # Yahoo daily chart API -> .trend_cache/ (adjusted closes)
    python -u trend.py check     # the data checks the PREREG lists (coverage, splits, jumps)
    python -u trend.py report    # T1, T2, EW and 60/40; the pre-registered verdicts

Standalone research: imports nothing from ORB or E16, places no orders, touches no account.
No pandas: daily series are plain lists aligned on SPY's trading days.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

UNIVERSE = ("SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ")
CASH = "BIL"                 # SPDR 1-3 Month T-Bill, total return; starts 2007-05-30
IRX = "^IRX"                 # 13-week T-bill yield (%), cash proxy before BIL exists
CACHE = Path(".trend_cache")
COST = 0.0005                # 5 bp per side, scored
COST_STRESS = 0.002          # 20 bp per side, stress only
SCORED_FROM = "2008-01-01"   # first execution on or after this: 2007-12-31's signals
DESC_FROM, DESC_TO = "2007-03-01", "2007-12-31"
SMA_MONTHS = 10
MOM_MONTHS = 12
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{t}"
STOOQ = "https://stooq.com/q/d/l/?s={t}.us&i=d"


# ---------------------------------------------------------------- data

def _yahoo(ticker: str) -> dict:
    import requests
    r = requests.get(YAHOO.format(t=ticker), timeout=30,
                     headers={"User-Agent": "Mozilla/5.0 (research script)"},
                     params={"period1": 0, "period2": int(time.time()), "interval": "1d",
                             "events": "div,split", "includeAdjustedClose": "true"})
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose") or q["close"]
    rows = [(datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"), c, a)
            for ts, c, a in zip(res["timestamp"], q["close"], adj) if c is not None and a is not None]
    ev = res.get("events", {})
    return {"rows": rows, "dividends": sorted(
                (datetime.fromtimestamp(int(k), timezone.utc).strftime("%Y-%m-%d"), v["amount"])
                for k, v in ev.get("dividends", {}).items()),
            "splits": sorted(
                (datetime.fromtimestamp(int(k), timezone.utc).strftime("%Y-%m-%d"),
                 f"{v['numerator']}:{v['denominator']}") for k, v in ev.get("splits", {}).items())}


def _stooq(ticker: str) -> dict:
    import requests
    r = requests.get(STOOQ.format(t=ticker.lower()), timeout=30)
    r.raise_for_status()
    lines = r.text.strip().splitlines()[1:]
    rows = [(p[0], float(p[4]), float(p[4])) for p in (ln.split(",") for ln in lines) if len(p) >= 5]
    return {"rows": rows, "dividends": [], "splits": []}


def fetch() -> None:
    CACHE.mkdir(exist_ok=True)
    meta = {"downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": {}}
    for t in UNIVERSE + (CASH, IRX):
        try:
            data, src = _yahoo(t), "yahoo chart v8 (adjclose: split+dividend adjusted)"
        except Exception as e:  # noqa: BLE001
            print(f"  {t}: Yahoo failed ({e}); trying Stooq")
            data, src = _stooq(t.replace("^", "")), "stooq daily csv"
        (CACHE / f"{t.replace('^', '_')}.json").write_text(json.dumps(data))
        meta["source"][t] = src
        rows = data["rows"]
        print(f"  {t:4} {len(rows)} days {rows[0][0]} -> {rows[-1][0]}  "
              f"{len(data['dividends'])} dividends, splits {data['splits'] or 'none'}  [{src}]")
    (CACHE / "meta.json").write_text(json.dumps(meta, indent=1))


def _raw(t: str) -> dict:
    return json.loads((CACHE / f"{t.replace('^', '_')}.json").read_text())


def load():
    """(days, adj prices per ETF, daily cash returns), all aligned on SPY's trading days.

    A day an ETF has no print (rare, early history) carries its last price: a 0% day.
    Days before an ETF exists are None. Cash = BIL's return where BIL has a previous
    close, else ^IRX / 100 / 252 from the previous day's yield.
    """
    days = [d for d, _, _ in _raw("SPY")["rows"]]
    px = {}
    for t in UNIVERSE + (CASH,):
        m = {d: a for d, _, a in _raw(t)["rows"]}
        out, last = [], None
        for d in days:
            last = m.get(d, last)
            out.append(last)
        px[t] = out
    irx = {d: c for d, c, _ in _raw(IRX)["rows"]}
    cash, y = [0.0], None
    for i in range(1, len(days)):
        y = irx.get(days[i - 1], y)
        b0, b1 = px[CASH][i - 1], px[CASH][i]
        cash.append(b1 / b0 - 1 if b0 and b1 else (y or 0.0) / 100 / 252)
    return days, {t: px[t] for t in UNIVERSE}, cash


# ---------------------------------------------------------------- signals

def month_ends(days: list[str]) -> list[int]:
    """Index of the last trading day of each month."""
    return [i for i in range(len(days)) if i + 1 == len(days) or days[i + 1][:7] != days[i][:7]]


def t1_signal(series: list, me: list[int], k: int) -> bool | None:
    """Faber: month-end close above the mean of the last 10 month-end closes (incl. this one)."""
    if k + 1 < SMA_MONTHS:
        return None
    window = [series[me[j]] for j in range(k - SMA_MONTHS + 1, k + 1)]
    if any(v is None for v in window):
        return None
    return window[-1] > sum(window) / SMA_MONTHS


def t2_signal(series: list, cash_growth: list[float], me: list[int], k: int) -> bool | None:
    """12-month total return above cash's over the same 12 month-ends."""
    if k < MOM_MONTHS:
        return None
    a, b = me[k - MOM_MONTHS], me[k]
    if series[a] is None or series[b] is None:
        return None
    return series[b] / series[a] - 1 > cash_growth[b] / cash_growth[a] - 1


def targets(rule: str, days, px, cash, me: list[int]) -> dict[int, dict[str, float]]:
    """{execution day index: target weights}. A month-end's signal trades the next day.

    rule: "T1", "T2", "EW" (1/8 each) or "6040". Slots without a signal sit in cash.
    """
    growth = [1.0]
    for r in cash[1:]:
        growth.append(growth[-1] * (1 + r))
    out = {}
    for k, i in enumerate(me):
        if i + 1 >= len(days):
            break                                   # the next day hasn't happened yet
        if rule == "6040":
            w = {"SPY": 0.6, "IEF": 0.4}
        else:
            w = {}
            for t in UNIVERSE:
                if rule == "EW":
                    on = px[t][i] is not None
                elif rule == "T1":
                    on = t1_signal(px[t], me, k)
                else:
                    on = t2_signal(px[t], growth, me, k)
                if on:
                    w[t] = w.get(t, 0.0) + 1 / len(UNIVERSE)
        w["CASH"] = 1.0 - sum(w.values())
        out[i + 1] = w
    return out


# ---------------------------------------------------------------- simulation

def simulate(days, px, cash, tg: dict[int, dict[str, float]], start: int, end: int, cost: float):
    """Daily values from the close of `start` (an execution day) to the close of `end`.

    Starts in cash, trades into tg[start] at that close (paying cost on the turnover), then
    drifts with daily returns and resets to the target at each later execution close.
    Returns [(day, value)], value 1.0 before the first trade's cost.
    """
    w = {"CASH": 1.0}
    value = 1.0
    out = []
    for t in range(start, end + 1):
        if t > start:
            r = {a: (cash[t] if a == "CASH" else px[a][t] / px[a][t - 1] - 1) for a in w}
            port = sum(w[a] * r[a] for a in w)
            value *= 1 + port
            w = {a: w[a] * (1 + r[a]) / (1 + port) for a in w}
        if t in tg:
            new = tg[t]
            turnover = sum(abs(new.get(a, 0.0) - w.get(a, 0.0)) for a in set(new) | set(w))
            value *= 1 - cost * turnover
            w = dict(new)
        out.append((days[t], value))
    return out


# ---------------------------------------------------------------- metrics

def metrics(path, cash, days) -> dict:
    vals = [v for _, v in path]
    idx = {d: i for i, d in enumerate(days)}
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    rf = [cash[idx[path[i][0]]] for i in range(1, len(path))]
    ex = [a - b for a, b in zip(rets, rf)]
    span = (date.fromisoformat(path[-1][0]) - date.fromisoformat(path[0][0])).days
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    worst12 = min((vals[i] / vals[i - 252] - 1 for i in range(252, len(vals))), default=float("nan"))
    sd = statistics.stdev(ex) if len(ex) > 1 else 0.0
    return {"cagr": (vals[-1] / vals[0]) ** (365.25 / span) - 1 if span else float("nan"),
            "vol": statistics.stdev(rets) * 252 ** 0.5 if len(rets) > 1 else float("nan"),
            "sharpe": statistics.mean(ex) / sd * 252 ** 0.5 if sd else float("nan"),
            "mdd": mdd, "worst12": worst12, "total": vals[-1] / vals[0] - 1}


def rebase(path, a: str, b: str):
    """The sub-path from day a to day b, as if started fresh there (value 1.0)."""
    sub = [(d, v) for d, v in path if a <= d <= b]
    return [(d, v / sub[0][1]) for d, v in sub]


def yearly(path) -> dict[str, float]:
    out, prev = {}, path[0][1]
    for i, (d, v) in enumerate(path):
        if i + 1 == len(path) or path[i + 1][0][:4] != d[:4]:
            out[d[:4]] = v / prev - 1
            prev = v
    return out


def criteria(t: dict, ew: dict) -> dict[str, bool]:
    """PREREG (a)-(c) for one period: drawdown 30% smaller, CAGR within 1.5pp, Sharpe not lower."""
    return {"a": abs(t["mdd"]) <= 0.70 * abs(ew["mdd"]),
            "b": t["cagr"] >= ew["cagr"] - 0.015,
            "c": t["sharpe"] >= ew["sharpe"]}


# ---------------------------------------------------------------- reports

def check() -> None:
    days, px, cash = load()
    meta = json.loads((CACHE / "meta.json").read_text())
    print(f"downloaded {meta['downloaded_at']}")
    for t in UNIVERSE + (CASH, IRX):
        raw = _raw(t)
        rows = raw["rows"]
        print(f"{t:4} {rows[0][0]} -> {rows[-1][0]}  {len(rows)} days  "
              f"dividends {len(raw['dividends'])}  splits {raw['splits'] or 'none'}  "
              f"[{meta['source'][t]}]")
        if t == IRX:
            continue
        jumps = [(rows[i][0], rows[i][2] / rows[i - 1][2] - 1) for i in range(1, len(rows))
                 if abs(rows[i][2] / rows[i - 1][2] - 1) > 0.15]
        raw_jumps = [(rows[i][0], rows[i][1] / rows[i - 1][1] - 1) for i in range(1, len(rows))
                     if abs(rows[i][1] / rows[i - 1][1] - 1) > 0.15]
        span = [r for r in rows if r[0] >= "2008-01-01"]
        tr_adj = span[-1][2] / span[0][2] - 1
        tr_raw = span[-1][1] / span[0][1] - 1
        yrs = (date.fromisoformat(span[-1][0]) - date.fromisoformat(span[0][0])).days / 365.25
        print(f"     adjclose |daily| > 15%: {[(d, f'{r:+.1%}') for d, r in jumps] or 'none'}")
        print(f"     raw close |daily| > 15%: {[(d, f'{r:+.1%}') for d, r in raw_jumps] or 'none'}")
        print(f"     2008->now: adj {tr_adj:+.0%} vs raw {tr_raw:+.0%}, "
              f"gap {((1 + tr_adj) / (1 + tr_raw)) ** (1 / yrs) - 1:+.2%}/yr (~ dividend yield)")
    print(f"aligned on SPY: {days[0]} -> {days[-1]}; first day each ETF has a price: " +
          ", ".join(f"{t} {days[next(i for i, v in enumerate(px[t]) if v)]}" for t in UNIVERSE))


def _fmt(m: dict) -> str:
    return (f"CAGR {m['cagr']:+6.2%}  vol {m['vol']:5.1%}  Sharpe {m['sharpe']:5.2f}  "
            f"maxDD {m['mdd']:+6.1%}  worst 12m {m['worst12']:+6.1%}")


def report() -> None:
    days, px, cash = load()
    me = month_ends(days)
    start = next(i for i in range(len(days)) if days[i] >= SCORED_FROM and (i - 1) in me)
    end = len(days) - 1
    mid_date = date.fromisoformat(days[start]) + (date.fromisoformat(days[end]) -
                                                  date.fromisoformat(days[start])) / 2
    mid = next(d for d in days if d >= mid_date.isoformat())
    halves = ((days[start], mid), (mid, days[end]))
    rules = ("T1", "T2", "EW", "6040")
    tg = {r: targets(r, days, px, cash, me) for r in rules}
    meta = json.loads((CACHE / "meta.json").read_text())
    print(f"E18 trend-following. Data downloaded {meta['downloaded_at']}. Scored "
          f"{days[start]} -> {days[end]}, halves split at {mid}.")
    res = {}
    for cost, label in ((COST, "5 bp per side (scored)"), (COST_STRESS, "20 bp per side (stress)")):
        print(f"\n== {label}")
        for r in rules:
            path = simulate(days, px, cash, tg[r], start, end, cost)
            full = metrics(path, cash, days)
            hs = [metrics(rebase(path, a, b), cash, days) for a, b in halves]
            res[(r, cost)] = (path, full, hs)
            print(f"  {r:4} full   {_fmt(full)}")
            for (a, b), m in zip(halves, hs):
                print(f"       {a[:7]}..{b[:7]} {_fmt(m)}")
    print("\n== year by year, 5 bp (calendar years; 2008 starts at the first trade)")
    ys = {r: yearly(res[(r, COST)][0]) for r in rules}
    print("  year " + "".join(f"{r:>9}" for r in rules))
    for y in ys["EW"]:
        print(f"  {y} " + "".join(f"{ys[r][y]:+9.1%}" for r in rules))
    turns = {r: sum(1 for i in tg[r] if start <= i <= end) for r in ("T1", "T2")}
    print(f"\n== signal changes (scored period): " + ", ".join(
        f"{r} {_changes(tg[r], start, end)} slot switches over {turns[r]} rebalances" for r in ("T1", "T2")))
    print("\n== pre-registered criteria vs EW, 5 bp")
    ew_full, ew_h = res[("EW", COST)][1], res[("EW", COST)][2]
    for r in ("T1", "T2"):
        full, hs = res[(r, COST)][1], res[(r, COST)][2]
        cf = criteria(full, ew_full)
        ch = [criteria(h, e) for h, e in zip(hs, ew_h)]
        d = all(all(c.values()) for c in ch)
        ok = all(cf.values()) and d
        print(f"  {r}: (a) maxDD {full['mdd']:+.1%} vs limit {0.7 * ew_full['mdd']:+.1%} "
              f"{'PASS' if cf['a'] else 'FAIL'};  (b) CAGR {full['cagr']:+.2%} vs floor "
              f"{ew_full['cagr'] - 0.015:+.2%} {'PASS' if cf['b'] else 'FAIL'};  (c) Sharpe "
              f"{full['sharpe']:.2f} vs {ew_full['sharpe']:.2f} {'PASS' if cf['c'] else 'FAIL'}")
        for (a, b), c in zip(halves, ch):
            print(f"      half {a[:7]}..{b[:7]}: " + "  ".join(
                f"({k}) {'PASS' if v else 'FAIL'}" for k, v in c.items()))
        print(f"  >>> {r}: {'PASS' if ok else 'FAIL'}")
    desc_s = next(i for i in range(len(days)) if days[i] >= DESC_FROM and (i - 1) in me)
    desc_e = max(i for i in range(len(days)) if days[i] <= DESC_TO)
    print(f"\n== descriptive only, {days[desc_s]} -> {days[desc_e]} (5 bp)")
    for r in rules:
        m = metrics(simulate(days, px, cash, tg[r], desc_s, desc_e, COST), cash, days)
        print(f"  {r:4} total {m['total']:+6.1%}  maxDD {m['mdd']:+6.1%}")


def _changes(tg: dict, start: int, end: int) -> int:
    """How many slot-level switches (asset <-> cash) the rebalances in [start, end] made."""
    keys = sorted(i for i in tg if start <= i <= end)
    n = 0
    for a, b in zip(keys, keys[1:]):
        n += sum((tg[a].get(t, 0) > 0) != (tg[b].get(t, 0) > 0) for t in UNIVERSE)
    return n


if __name__ == "__main__":
    {"fetch": fetch, "check": check, "report": report}[sys.argv[1] if len(sys.argv) > 1 else "report"]()
