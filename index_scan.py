"""Index ETF scan: four signal families on SPY/QQQ/IWM/DIA. See PREREG-INDEX-2026-10-27.md.

    python -u index_scan.py fetch      # Alpaca SIP 1-min bars -> .index_cache/ (slow once)
    python -u index_scan.py check      # bar counts, early closes, gaps: data sanity only
    python -u index_scan.py report     # H1-H4, discovery and holdout side by side
    python -u index_scan.py forward    # E16: the frozen QQQ rule on sessions since 2026-10-28

Nothing here touches config.yaml, the bot, or the broker. Read-only research.
"""

from __future__ import annotations

import os
import pickle
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CACHE = Path(".index_cache")
SYMBOLS = ("SPY", "QQQ", "IWM", "DIA")
SCORED = "SPY"
START = date(2016, 1, 1)
HOLDOUT_START = "2022-01-01"
PUBLISHED_H3 = "2024-05-10"   # first SSRN version of Zarattini, Aziz & Barbon
COST = 0.00005                # 0.5 bp per side, scored
COST_HI = 0.0001              # 1 bp per side, sensitivity only
FULL_MIN = 385                # last bar at or after 15:55 -> full session
EARLY_END = 210               # 13:00: half-day sessions are cut here
# NYSE 13:00 closes in the sample. Alpaca returns after-hours bars past 13:00 on these days,
# so clock time alone would label them full sessions with an after-hours "close".
EARLY_CLOSES = frozenset({
    "2016-11-25", "2017-07-03", "2017-11-24", "2018-07-03", "2018-11-23", "2018-12-24",
    "2019-07-03", "2019-11-29", "2019-12-24", "2020-11-27", "2020-12-24", "2021-11-26",
    "2022-11-25", "2023-07-03", "2023-11-24", "2024-07-03", "2024-11-29", "2024-12-24",
    "2025-07-03", "2025-11-28", "2025-12-24",
})
CHECKPOINTS = tuple(range(30, 361, 30))   # 10:00 .. 15:30, minutes from 09:30
LOOKBACK = 14
# E16 forward test (PREREG-QQQ-NOISE-PAPER-2026-10-27.md): rules frozen as h3_rows, QQQ.
FORWARD_START = "2026-10-28"   # first session after the PREREG was written
FUTILITY_LOOKS = (120, 180)    # active sessions; stop if the mean so far is <= 0
FINAL_N = 250                  # active sessions; the only look that can pass


# ---------------------------------------------------------------- fetch

def _months(start: date, end: date):
    cur = date(start.year, start.month, 1)
    while cur <= end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        yield cur, nxt - timedelta(days=1)
        cur = nxt


def fetch() -> None:
    """One request per month for all four symbols; keeps regular-hours bars only.

    Each cached month is {symbol: {"YYYY-MM-DD": [(minute, o, h, l, c, v, vw), ...]}},
    minute counted from 09:30. Only complete past months are cached.
    """
    from dotenv import load_dotenv
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    load_dotenv()
    cli = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    CACHE.mkdir(exist_ok=True)
    today = date.today()
    for m0, m1 in _months(START, today):
        path = CACHE / f"{m0:%Y-%m}.pkl"
        complete = m1 < today
        if complete and path.exists():
            continue
        req = StockBarsRequest(
            symbol_or_symbols=list(SYMBOLS),
            timeframe=TimeFrame(1, TimeFrameUnit.Minute),
            start=datetime.combine(m0, datetime.min.time(), tzinfo=timezone.utc),
            end=datetime.combine(min(m1, today), datetime.max.time(), tzinfo=timezone.utc),
            feed=DataFeed.SIP,
            adjustment=Adjustment.ALL,
        )
        data = cli.get_stock_bars(req).data
        month = {}
        for sym in SYMBOLS:
            days = {}
            for b in data.get(sym, []):
                t = b.timestamp.astimezone(ET)
                minute = t.hour * 60 + t.minute - 570
                if 0 <= minute < 390:
                    days.setdefault(t.strftime("%Y-%m-%d"), []).append(
                        (minute, float(b.open), float(b.high), float(b.low), float(b.close),
                         float(b.volume), float(getattr(b, "vwap", 0) or 0)))
            month[sym] = days
        if complete:
            path.write_bytes(pickle.dumps(month))
        else:
            (CACHE / "partial.pkl").write_bytes(pickle.dumps(month))
        print(f"  {m0:%Y-%m}: " + "  ".join(f"{s} {len(month[s]):2d}d" for s in SYMBOLS), flush=True)


def load() -> dict[str, dict[str, list]]:
    """{symbol: {day: bars sorted by minute}} across every cached month."""
    out = {s: {} for s in SYMBOLS}
    for path in sorted(CACHE.glob("*.pkl")):
        month = pickle.loads(path.read_bytes())
        for s in SYMBOLS:
            out[s].update(month.get(s, {}))
    for s in SYMBOLS:
        out[s] = {d: sorted(b) for d, b in sorted(out[s].items())}
    return out


# ---------------------------------------------------------------- sessions

class Session:
    """One regular-hours session with forward-filled closes by minute."""

    def __init__(self, day: str, bars: list):
        self.day = day
        self.early = day in EARLY_CLOSES
        if self.early:
            bars = [b for b in bars if b[0] < EARLY_END]
        self.bars = bars
        self.open = bars[0][1] if bars[0][0] <= 2 else None
        self.close = bars[-1][4]
        self.last = bars[-1][0]
        self.full = not self.early and self.last >= FULL_MIN
        # ends early with no half-day to explain it: a feed gap, so its close is unreliable
        self.broken = not self.early and not self.full
        closes = [None] * 390
        for b in bars:
            closes[b[0]] = b[4]
        prev = None
        for i in range(390):
            prev = closes[i] if closes[i] is not None else prev
            closes[i] = prev
        self._c = closes
        pv = v = 0.0
        vwap = [None] * 390
        k = 0
        for i in range(390):
            while k < len(bars) and bars[k][0] == i:
                b = bars[k]
                px = b[6] if b[6] > 0 else (b[2] + b[3] + b[4]) / 3
                pv += px * b[5]; v += b[5]; k += 1
            vwap[i] = pv / v if v else None
        self._vwap = vwap

    def p(self, minute: int):
        """Price at 09:30 + minute: close of the bar labelled one minute earlier."""
        return self._c[minute - 1]

    def vwap(self, minute: int):
        return self._vwap[minute - 1]


def sessions(bars_by_day: dict) -> list[Session]:
    return [s for s in (Session(d, b) for d, b in bars_by_day.items()) if s.open]


# ---------------------------------------------------------------- stats

def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def boot(xs, n=2000, seed=7):
    """Mean and 95% CI by resampling sessions."""
    if len(xs) < 5:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    k = len(xs)
    stats = sorted(sum(rng.choices(xs, k=k)) / k for _ in range(n))
    return _mean(xs), stats[int(0.025 * n)], stats[int(0.975 * n)]


def boot_diff(a, b, n=2000, seed=11):
    """mean(a) - mean(b), resampling within each group."""
    if len(a) < 5 or len(b) < 5:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    stats = sorted(_mean(rng.choices(a, k=len(a))) - _mean(rng.choices(b, k=len(b)))
                   for _ in range(n))
    return _mean(a) - _mean(b), stats[int(0.025 * n)], stats[int(0.975 * n)]


def _bp(x):
    return f"{x * 1e4:+6.2f}"


def _row(label, rows, key="r"):
    """rows: list of (day, value) or dicts; prints disc / hold side by side in bp."""
    d = [r[key] for r in rows if r["day"] < HOLDOUT_START]
    h = [r[key] for r in rows if r["day"] >= HOLDOUT_START]
    md, dlo, dhi = boot(d)
    mh, hlo, hhi = boot(h)
    print(f"  {label:34} disc n={len(d):4d} {_bp(md)} [{_bp(dlo)},{_bp(dhi)}]   "
          f"hold n={len(h):4d} {_bp(mh)} [{_bp(hlo)},{_bp(hhi)}]")
    return md, mh, hlo


def _by_year(rows, key="r"):
    years = sorted({r["day"][:4] for r in rows})
    parts = []
    for y in years:
        xs = [r[key] for r in rows if r["day"][:4] == y]
        parts.append(f"{y} {_bp(_mean(xs)).strip()}")
    print("    by year (bp): " + "  ".join(parts))


def _verdict(name, ok):
    print(f"  >>> {name}: {'PASS' if ok else 'FAIL'}")


# ---------------------------------------------------------------- H1

def h1_rows(ss: list[Session], cost=COST):
    rows = []
    for prev, s in zip(ss, ss[1:]):
        if not s.full or prev.broken:
            continue
        p1530 = s.p(360)
        r = p1530 / prev.close - 1
        if r == 0:
            continue
        side = 1 if r > 0 else -1
        p1000 = s.p(30)
        rows.append({"day": s.day, "side": side, "signal": r,
                     "r": side * (s.close / p1530 - 1) - 2 * cost,
                     "gao": (1 if p1000 > prev.close else -1) * (s.close / p1530 - 1) - 2 * cost})
    return rows


def report_h1(data):
    print("\nH1 market intraday momentum: sign(prev close -> 15:30) held 15:30 -> close, net bp")
    verdict = None
    for sym in SYMBOLS:
        rows = h1_rows(data[sym])
        md, mh, hlo = _row(f"{sym}{' [SCORED]' if sym == SCORED else ''}", rows)
        if sym == SCORED:
            verdict = md > 0 and mh > 0 and hlo > 0
            _by_year(rows)
            _row("  long leg", [r for r in rows if r["side"] > 0])
            _row("  short leg", [r for r in rows if r["side"] < 0])
            disc = sorted(abs(r["signal"]) for r in rows if r["day"] < HOLDOUT_START)
            cut = disc[2 * len(disc) // 3]
            _row(f"  top tercile |r| (>= {cut * 100:.2f}%)", [r for r in rows if abs(r["signal"]) >= cut])
            _row("  Gao: sign(prev close -> 10:00)", rows, "gao")
            _row("  at 1 bp per side", h1_rows(data[sym], COST_HI))
    _verdict("H1", verdict)


# ---------------------------------------------------------------- H2

def h2_rows(ss: list[Session], cost=COST):
    rows = []
    for s, nxt in zip(ss, ss[1:]):
        if s.broken or nxt.broken:
            continue
        rows.append({"day": nxt.day, "r": nxt.open / s.close - 1 - 2 * cost,
                     "intraday": nxt.close / nxt.open - 1 - 2 * cost,
                     "monday": datetime.strptime(nxt.day, "%Y-%m-%d").weekday() == 0})
    return rows


def report_h2(data):
    print("\nH2 overnight drift: long Close(t) -> Open(t+1), net bp")
    verdict = None
    for sym in SYMBOLS:
        rows = h2_rows(data[sym])
        md, mh, hlo = _row(f"{sym}{' [SCORED]' if sym == SCORED else ''}", rows)
        if sym == SCORED:
            verdict = md > 0 and mh > 0 and hlo > 0
            _by_year(rows)
            _row("  intraday Open -> Close (complement)", rows, "intraday")
            _row("  into Monday", [r for r in rows if r["monday"]])
            _row("  other nights", [r for r in rows if not r["monday"]])
            _row("  at 1 bp per side", h2_rows(data[sym], COST_HI))
    _verdict("H2", verdict)


# ---------------------------------------------------------------- H3

def h3_rows(ss: list[Session], cost=COST, vwap_stop=False, long_only=False):
    """Base model: enter/flip on a band breach at each checkpoint, flat at the close.

    vwap_stop=True is the paper's refinement: longs need p > max(UB, VWAP) and exit
    below it, shorts mirror with min(LB, VWAP). Sigma uses the previous 14 FULL sessions.
    long_only (post-hoc, cash account): a short signal only closes a long.
    """
    rows, hist = [], []
    for i, s in enumerate(ss):
        if i and s.full and not ss[i - 1].broken and len(hist) == LOOKBACK:
            rows.append(_h3_day(s, ss[i - 1], hist, cost, vwap_stop, long_only))
        if s.full:
            hist = (hist + [s])[-LOOKBACK:]
    return rows


def _h3_day(s: Session, prev: Session, hist: list[Session], cost, vwap_stop, long_only=False):
    hi_ref, lo_ref = max(s.open, prev.close), min(s.open, prev.close)
    pos, entry, ret, sides = 0, 0.0, 0.0, 0
    legs = {1: 0.0, -1: 0.0}

    def close_at(price):
        nonlocal ret, sides
        seg = pos * (price / entry - 1)
        ret += seg; legs[pos] += seg; sides += 1

    for m in CHECKPOINTS:
        sigma = _mean([abs(x.p(m) / x.open - 1) for x in hist])
        ub, lb, p = hi_ref * (1 + sigma), lo_ref * (1 - sigma), s.p(m)
        if vwap_stop:
            vw = s.vwap(m)
            ub, lb = max(ub, vw), min(lb, vw)
            if (pos > 0 and p < ub) or (pos < 0 and p > lb):
                close_at(p)
                pos = 0
        want = 1 if p > ub else -1 if p < lb else 0
        if want and want != pos:
            if pos:
                close_at(p)
                pos = 0
            if want > 0 or not long_only:
                pos, entry = want, p
                sides += 1
    if pos:
        close_at(s.close)
    return {"day": s.day, "active": sides > 0, "r": ret - sides * cost,
            "long": legs[1], "short": legs[-1]}


def report_h3(data):
    print("\nH3 noise-area momentum (Zarattini/Aziz/Barbon base model), net bp per ACTIVE session")
    verdict = None
    for sym in SYMBOLS:
        rows = h3_rows(data[sym])
        active = [r for r in rows if r["active"]]
        md, mh, hlo = _row(f"{sym}{' [SCORED]' if sym == SCORED else ''}", active)
        if sym == SCORED:
            post = [r["r"] for r in active if r["day"] >= PUBLISHED_H3]
            mp, plo, phi = boot(post)
            print(f"  {'  post-publication ' + PUBLISHED_H3 + '+':34} n={len(post):4d} {_bp(mp)} "
                  f"[{_bp(plo)},{_bp(phi)}]")
            verdict = md > 0 and mh > 0 and hlo > 0 and mp > 0
            _by_year(active)
            _row("  per trading day incl. flat", rows)
            _row("  long-leg gross", active, "long")
            _row("  short-leg gross", active, "short")
            _row("  VWAP trailing stop (paper)", [r for r in h3_rows(data[sym], vwap_stop=True)
                                                  if r["active"]])
            _row("  at 1 bp per side", [r for r in h3_rows(data[sym], COST_HI) if r["active"]])
    _verdict("H3", verdict)


# ---------------------------------------------------------------- H4

def h4_rows(ss: list[Session]):
    """Close-to-close returns; tom = month's last session or one of its first three.

    Rows start at the first month boundary in the data, where position-in-month is known.
    """
    rows, k = [], None
    for i in range(1, len(ss)):
        if ss[i].day[:7] != ss[i - 1].day[:7]:
            k = 0
        elif k is not None:
            k += 1
        if k is None or ss[i].broken or ss[i - 1].broken:
            continue
        last = i + 1 < len(ss) and ss[i + 1].day[:7] != ss[i].day[:7]
        rows.append({"day": ss[i].day, "r": ss[i].close / ss[i - 1].close - 1,
                     "tom": last or k < 3})
    return rows


def h4_windows(ss: list[Session], cost=COST):
    """Buy Close of day -2, sell Close of day +3: one trade per month end."""
    out = []
    for i in range(1, len(ss) - 3):
        if ss[i].day[:7] != ss[i + 1].day[:7]:          # i is day -1
            if ss[i - 1].broken or ss[i + 3].broken:
                continue
            out.append({"day": ss[i].day, "r": ss[i + 3].close / ss[i - 1].close - 1 - 2 * cost})
    return out


def report_h4(data):
    print("\nH4 turn of month: mean daily return on TOM days (-1,+1,+2,+3) minus other days, bp")
    verdict = None
    for sym in SYMBOLS:
        rows = h4_rows(data[sym])
        parts = []
        for lo, hi in (("", HOLDOUT_START), (HOLDOUT_START, "9999")):
            tom = [r["r"] for r in rows if lo <= r["day"] < hi and r["tom"]]
            rest = [r["r"] for r in rows if lo <= r["day"] < hi and not r["tom"]]
            parts.append((len(tom), len(rest), *boot_diff(tom, rest)))
        (dn, dr, md, dlo, dhi), (hn, hr, mh, hlo, hhi) = parts
        print(f"  {sym + (' [SCORED]' if sym == SCORED else ''):34} disc {dn}/{dr} {_bp(md)} "
              f"[{_bp(dlo)},{_bp(dhi)}]   hold {hn}/{hr} {_bp(mh)} [{_bp(hlo)},{_bp(hhi)}]")
        if sym == SCORED:
            verdict = md > 0 and mh > 0 and hlo > 0
            _row("  TOM window as one trade", h4_windows(data[sym]))
    _verdict("H4", verdict)


# ---------------------------------------------------------------- post-hoc

def _sharpe(rows):
    xs = [r["r"] for r in rows]
    m = _mean(xs)
    sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
    return m / sd * 252 ** 0.5 if sd else float("nan")


def report_posthoc(data):
    """NOT pre-registered. Chosen after seeing H3's QQQ/IWM split; for judging a forward test."""
    print("\nPOST-HOC (not scored): H3 noise-area momentum, all symbols, after the fact")
    for sym in SYMBOLS:
        rows = h3_rows(data[sym])
        active = [r for r in rows if r["active"]]
        post = [r["r"] for r in active if r["day"] >= PUBLISHED_H3]
        mp, plo, phi = boot(post)
        print(f"\n {sym}: post-publication n={len(post)} {_bp(mp)} [{_bp(plo)},{_bp(phi)}]   "
              f"Sharpe per trading day (unlevered): disc "
              f"{_sharpe([r for r in rows if r['day'] < HOLDOUT_START]):+.2f}  hold "
              f"{_sharpe([r for r in rows if r['day'] >= HOLDOUT_START]):+.2f}")
        _by_year(active)
        _row("  long-leg gross", active, "long")
        _row("  short-leg gross", active, "short")
        _row("  long only (cash account)", [r for r in h3_rows(data[sym], long_only=True)
                                            if r["active"]])
        _row("  VWAP trailing stop", [r for r in h3_rows(data[sym], vwap_stop=True) if r["active"]])
        _row("  at 2 bp per side", [r for r in h3_rows(data[sym], 2 * COST_HI) if r["active"]])
        hold = sorted((r["r"] for r in active if r["day"] >= HOLDOUT_START), reverse=True)
        print(f"    holdout without its best 10 sessions: {_bp(_mean(hold[10:]))} bp "
              f"(best 10 = {sum(hold[:10]) / max(sum(hold), 1e-12) * 100:.0f}% of the total)")


# ---------------------------------------------------------------- E16 forward test

def e16_verdict(active: list[float], long_only: list[float]) -> str:
    """Apply the E16 gate to forward returns, in session order. Pure, for testing.

    active: net return per active session (base model). long_only: per active long-only session.
    Looks are taken on the FIRST n active sessions exactly, never on a moving window.
    """
    for n in FUTILITY_LOOKS:
        if len(active) >= n and _mean(active[:n]) <= 0:
            return f"STOPPED for futility at {n} active sessions (mean {_bp(_mean(active[:n])).strip()} bp)"
    if len(active) < FINAL_N:
        nxt = next((n for n in FUTILITY_LOOKS + (FINAL_N,) if n > len(active)), FINAL_N)
        return f"RUNNING: {len(active)}/{FINAL_N} active sessions, next look at {nxt}"
    xs = active[:FINAL_N]
    m = _mean(xs)
    sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
    t = m / (sd / len(xs) ** 0.5) if sd else float("nan")
    ex5 = _mean(sorted(xs, reverse=True)[5:])
    lo = _mean(long_only)
    ok = t >= 1.645 and lo > 0 and ex5 > 0
    return (f"FINAL {'PASS' if ok else 'FAIL'}: mean {_bp(m).strip()} bp, t {t:+.2f} (need 1.645), "
            f"long-only {_bp(lo).strip()} bp, without top 5 {_bp(ex5).strip()} bp")


def report_forward() -> None:
    """E16 shadow record: the frozen H3 rule on QQQ SIP bars since FORWARD_START."""
    ss = _sessions()["QQQ"]
    print(f"E16 forward shadow, QQQ noise-area base model, sessions >= {FORWARD_START}")
    for label, cost in (("0.5 bp/side (scored floor)", COST), ("2 bp/side (stress)", 2 * COST_HI)):
        rows = [r for r in h3_rows(ss, cost) if r["day"] >= FORWARD_START]
        active = [r["r"] for r in rows if r["active"]]
        lo = [r["r"] for r in h3_rows(ss, cost, long_only=True)
              if r["day"] >= FORWARD_START and r["active"]]
        m, clo, chi = boot(active)
        print(f"  {label:28} sessions {len(rows):4d}  active {len(active):4d}  "
              f"mean {_bp(m)} [{_bp(clo)},{_bp(chi)}]  long-only n={len(lo)} {_bp(_mean(lo))}")
        print(f"    {e16_verdict(active, lo)}")
    vw = [r["r"] for r in h3_rows(ss, vwap_stop=True) if r["day"] >= FORWARD_START and r["active"]]
    print(f"  VWAP-stop variant (descriptive)  active {len(vw):4d}  mean {_bp(_mean(vw))}")


# ---------------------------------------------------------------- main

def _sessions() -> dict[str, list[Session]]:
    raw = load()
    return {s: sessions(raw[s]) for s in SYMBOLS}


def check() -> None:
    raw = load()
    for sym in SYMBOLS:
        ss = sessions(raw[sym])
        full = sum(s.full for s in ss)
        early = [s.day for s in ss if s.early]
        odd = [s.day for s in ss if s.broken]
        nbars = sorted(len(s.bars) for s in ss if s.full)
        print(f"{sym}: {len(raw[sym])} days, {len(ss)} with an open, {full} full, "
              f"{len(early)} early closes, {len(odd)} broken ({', '.join(odd[:6])})")
        print(f"   {ss[0].day} -> {ss[-1].day}; bars per full day min {nbars[0]} "
              f"median {nbars[len(nbars) // 2]}")
    spy = {s.day for s in sessions(raw[SCORED])}
    for sym in SYMBOLS[1:]:
        other = {s.day for s in sessions(raw[sym])}
        print(f"{sym} vs SPY: missing {len(spy - other)}, extra {len(other - spy)}")


def report() -> None:
    data = _sessions()
    report_h1(data)
    report_h2(data)
    report_h3(data)
    report_h4(data)


def posthoc() -> None:
    report_posthoc(_sessions())


if __name__ == "__main__":
    {"fetch": fetch, "check": check, "report": report, "posthoc": posthoc,
     "forward": report_forward}[sys.argv[1] if len(sys.argv) > 1 else "report"]()
