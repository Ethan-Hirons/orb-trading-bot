"""Offline checks for qqq_orb.py — synthetic minute bars, no API, no alpaca.

Run standalone:  python tests/test_qqq_orb.py   (expect "All 14 checks passed.")
Also collected by pytest as one test (test_qqq_orb_suite).
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qqq_orb import (  # noqa: E402
    Bar, run_combo, signal_direction, simulate_day, summarize,
)

ET = ZoneInfo("America/New_York")
DAY = date(2026, 8, 21)


def mk_bars(specs, day=DAY, start_min=0):
    """specs: list of (open, high, low, close) starting at 9:30+start_min."""
    t0 = datetime.combine(day, dtime(9, 30), tzinfo=ET) + timedelta(minutes=start_min)
    return [
        Bar(t0 + timedelta(minutes=i), o, h, l, c, 1000.0)
        for i, (o, h, l, c) in enumerate(specs)
    ]


def flat_day(price, n=380, day=DAY):
    return mk_bars([(price, price, price, price)] * n, day=day)


def run_all() -> None:
    checks = 0

    def ok(cond, label):
        nonlocal checks
        assert cond, f"FAILED: {label}"
        checks += 1
        print(f"  ok: {label}")

    # --- direction rule ----------------------------------------------------
    qqq_up = mk_bars([(500, 501, 499.5, 500.5)] * 5 + [(500.5,) * 4] * 375)
    qqq_down = mk_bars([(500, 500.2, 498, 499)] * 5 + [(499,) * 4] * 375)
    qqq_doji = mk_bars([(500, 501, 499, 500)] * 5 + [(500,) * 4] * 375)
    ok(signal_direction(qqq_up, DAY, 5) == "up", "bullish first candle -> up")
    ok(signal_direction(qqq_down, DAY, 5) == "down", "bearish first candle -> down")
    ok(signal_direction(qqq_doji, DAY, 5) is None, "doji -> no trade")

    # --- up day trades TQQQ long, EOD exit, exact pnl (no slippage) --------
    # TQQQ OR 9:30-9:34: low 99. Entry at 9:35 open 100. Drifts to close 102.
    tqqq = mk_bars([(100, 100.5, 99, 100)] * 5
                   + [(100, 102.2, 99.5, 102)] * 375)
    sqqq = flat_day(20)
    t = simulate_day(DAY, qqq_up, {"TQQQ": tqqq, "SQQQ": sqqq}, 10_000,
                     or_minutes=5, target_r=None, risk_pct=1.0, slippage_bps=0.0)
    ok(t is not None and t.symbol == "TQQQ" and t.signal == "up",
       "up day -> long TQQQ")
    # entry 100, stop 99, R=1; risk $100 -> 100 sh; notional cap 10k/100 = 100
    ok(t.qty == 100 and t.entry_price == 100 and t.stop_price == 99,
       "sizing: min(risk/R, equity/entry) whole shares")
    ok(t.exit_reason == "eod" and abs(t.pnl - 200.0) < 1e-6,
       "EOD exit: 100 sh x (102-100) = +200")

    # --- down day trades SQQQ ---------------------------------------------
    t2 = simulate_day(
        DAY, qqq_down,
        {"TQQQ": flat_day(100),
         "SQQQ": mk_bars([(20, 20.1, 19.8, 20)] * 5
                         + [(20, 20.5, 19.9, 20.4)] * 375)},
        10_000, or_minutes=5, target_r=None, slippage_bps=0.0)
    ok(t2 is not None and t2.symbol == "SQQQ", "down day -> long SQQQ")

    # --- stop hit fills at the stop; stop-first on ambiguous bars ----------
    tq_stop = mk_bars([(100, 100.5, 99, 100)] * 5
                      + [(100, 100.2, 99.8, 100)] * 10
                      + [(100, 120, 98.5, 100)] * 1   # touches stop AND target
                      + [(100, 100, 100, 100)] * 364)
    t3 = simulate_day(DAY, qqq_up, {"TQQQ": tq_stop, "SQQQ": flat_day(20)},
                      10_000, or_minutes=5, target_r=10.0, slippage_bps=0.0)
    ok(t3.exit_reason == "stop" and t3.exit_price == 99.0,
       "ambiguous bar -> stop first, filled at stop")
    ok(abs(t3.pnl - (-100.0)) < 1e-6, "stop loss = -1R = -$100")

    # --- gap through the stop fills at the (worse) open --------------------
    tq_gap = mk_bars([(100, 100.5, 99, 100)] * 5
                     + [(100, 100.2, 99.9, 100.1)] * 10
                     + [(97, 97.5, 96.8, 97)] * 1
                     + [(97, 97, 97, 97)] * 364)
    t4 = simulate_day(DAY, qqq_up, {"TQQQ": tq_gap, "SQQQ": flat_day(20)},
                      10_000, or_minutes=5, target_r=None, slippage_bps=0.0)
    ok(t4.exit_reason == "stop" and t4.exit_price == 97.0,
       "gap through stop -> filled at the open, not the stop")

    # --- 10R target --------------------------------------------------------
    tq_run = mk_bars([(100, 100.5, 99, 100)] * 5
                     + [(100 + i * 0.1, 100.2 + i * 0.1, 99.9 + i * 0.1,
                         100.1 + i * 0.1) for i in range(200)]
                     + [(120, 120, 120, 120)] * 175)
    t5 = simulate_day(DAY, qqq_up, {"TQQQ": tq_run, "SQQQ": flat_day(20)},
                      10_000, or_minutes=5, target_r=10.0, slippage_bps=0.0)
    ok(t5.exit_reason == "target" and abs(t5.exit_price - 110.0) < 1e-6
       and abs(t5.pnl - 1000.0) < 1e-6, "10R target: exit 110, +$1000 = +10R")
    ok(abs(t5.pnl_r - 10.0) < 0.01, "pnl_r records +10R")

    # --- slippage charged against you on both sides ------------------------
    t6 = simulate_day(DAY, qqq_up, {"TQQQ": tqqq, "SQQQ": sqqq}, 10_000,
                      or_minutes=5, target_r=None, slippage_bps=10.0)
    ok(t6.entry_price > 100.0 and t6.exit_price < 102.0 and t6.pnl < 200.0,
       "slippage raises entry, lowers exit, cuts pnl")

    # --- equity compounds across days; summarize is sane -------------------
    d2 = DAY + timedelta(days=3)  # skip weekend -> Monday
    by_day = {
        "QQQ": {DAY: qqq_up,
                d2: mk_bars([(500, 501, 499.5, 500.5)] * 5
                            + [(500.5,) * 4] * 375, day=d2)},
        "TQQQ": {DAY: tqqq,
                 d2: mk_bars([(100, 100.5, 99, 100)] * 5
                             + [(100, 102.2, 99.5, 102)] * 375, day=d2)},
        "SQQQ": {DAY: flat_day(20), d2: flat_day(20, day=d2)},
    }
    trades, curve, final = run_combo([DAY, d2], by_day, 10_000,
                                     or_minutes=5, target_r=None,
                                     risk_pct=1.0, slippage_bps=0.0)
    s = summarize(trades, curve, 10_000)
    ok(len(trades) == 2 and final > 10_200 and s["trades"] == 2
       and s["win_rate"] == 100.0 and s["max_dd"] == 0.0,
       "two-day compounding + summary stats")

    print(f"\nAll {checks} checks passed.")


def test_qqq_orb_suite():
    run_all()


if __name__ == "__main__":
    run_all()
