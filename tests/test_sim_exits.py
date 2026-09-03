"""Checks for trailing-stop / time-stop simulation in backtest.simulate_symbol_day.

Run from the project root (needs alpaca-py installed, no API access required):
  python tests/test_sim_exits.py
Synthetic minute bars only — no network, no config changes.
"""
import os
import sys

os.environ.setdefault("ORB_NO_FILE_LOG", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta

from backtest import ET, simulate_symbol_day
from orb_bot.config import load_config


@dataclass
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def make_day(prices, day="2026-07-20", vol=50_000):
    """prices: list of (minute_offset_from_0930, o, h, l, c)."""
    d = datetime.strptime(day, "%Y-%m-%d").date()
    base = datetime.combine(d, dtime(9, 30), tzinfo=ET)
    return [Bar(base + timedelta(minutes=m), o, h, l, c, vol)
            for m, o, h, l, c in prices]


def main():
    cfg = load_config()
    cfg.strategy.allow_shorts = True
    # Pin the exit params these synthetic scenarios were built around — the
    # live config.yaml changes over time (v1.9 flipped stop/tp/trail/hold/
    # breakeven on 2026-08-03) and these tests exercise the SIM's exit
    # mechanics, not the current live tuning.
    cfg.exits.stop_pct = 3.0
    cfg.exits.tp_min_pct = 3.0
    cfg.exits.tp_max_pct = 5.0
    cfg.exits.breakeven_trigger_pct = 1.5
    cfg.exits.max_hold_minutes = 180
    cfg.exits.trail_pct = 0.0

    # OR 9:30-9:45 around 100 (high 101 / low 99), confirmed long breakout,
    # run-up to ~106, slow fade, then a crash through ~97.
    ramp = [(15 + i, 101 + i * 0.2, 101.3 + i * 0.2, 100.9 + i * 0.2,
             101.2 + i * 0.2) for i in range(8)]
    rise = [(23 + i, 102.8 + i * 0.4, 103.4 + i * 0.4, 102.7 + i * 0.4,
             103.2 + i * 0.4) for i in range(8)]
    fade = [(31 + i, 106.0 - i * 0.05, 106.1 - i * 0.05, 105.8 - i * 0.05,
             105.9 - i * 0.05) for i in range(60)]
    crash = [(91 + i, 103.0 - i * 0.5, 103.1 - i * 0.5, 102.4 - i * 0.5,
              102.5 - i * 0.5) for i in range(12)]
    tail = [(103 + i, 97.0, 97.2, 96.8, 97.0) for i in range(240)]
    day_bars = make_day(
        [(i, 100, 101, 99, 100.5) for i in range(15)]
        + ramp + rise + fade + crash + tail
    )

    run = lambda **kw: simulate_symbol_day("TEST", day_bars, cfg, "any",
                                           2050.0, **kw)
    b = run(trail_pct=None, max_hold_minutes=0)      # baseline, no time stop
    ch = run()                                       # live-config defaults
    h = run(max_hold_minutes=60)
    t30 = run(max_hold_minutes=30)
    tr = run(trail_pct=2.0, max_hold_minutes=0)
    nv = run(trail_pct=8.0, max_hold_minutes=0)      # too wide to ever hit

    fill = b.entry_price
    assert b.side == "long"
    # breakeven armed on the run-up, crash hits it (no tp / raw stop touch)
    assert b.exit_reason == "breakeven", b.exit_reason
    print("PASS baseline breakeven")
    # cfg default (180 min): breakeven fires first here -> identical to baseline
    assert cfg.exits.max_hold_minutes == 180
    assert (ch.exit_reason, ch.exit_time) == (b.exit_reason, b.exit_time)
    print("PASS default kwargs = live time stop, inert when stop fires first")
    assert h.exit_reason == "time" and t30.exit_reason == "time"
    print("PASS time stop fires (30/60 min caps)")
    assert tr.exit_reason == "trail" and tr.exit_price > fill
    assert tr.pnl > b.pnl and tr.pnl > h.pnl
    print("PASS 2% trail exits near the peak, beats baseline here")
    assert (nv.exit_reason, round(nv.exit_price, 4)) == \
           (b.exit_reason, round(b.exit_price, 4))
    print("PASS never-hit trail == baseline")

    # Short mirror: breakdown, drop to ~94, rip back up through entry.
    sramp = [(15 + i, 98.8 - i * 0.2, 99.0 - i * 0.2, 98.5 - i * 0.2,
              98.7 - i * 0.2) for i in range(8)]
    sdrop = [(23 + i, 97.2 - i * 0.4, 97.3 - i * 0.4, 96.6 - i * 0.4,
              96.8 - i * 0.4) for i in range(8)]
    srip = [(31 + i, 94.0 + i * 0.5, 94.6 + i * 0.5, 93.9 + i * 0.5,
             94.5 + i * 0.5) for i in range(20)]
    stail = [(51 + i, 103.5, 103.7, 103.3, 103.5) for i in range(240)]
    sday = make_day([(i, 100, 101, 99, 99.5) for i in range(15)]
                    + sramp + sdrop + srip + stail)
    st = simulate_symbol_day("TEST", sday, cfg, "any", 2050.0,
                             trail_pct=2.0, max_hold_minutes=0)
    assert st.side == "short" and st.exit_reason == "trail" and st.pnl > 0
    print("PASS short-side trail mirrors correctly")

    # --- 2026-07-29 additions: trail arming semantics + tp off ---
    # Small-MFE loser: breakout, peak only ~+0.1%, then a crash. An
    # entry-armed 2% trail cuts it at ~-2%; breakeven arming (live v1.8)
    # leaves the raw -3% stop to catch it.
    wob = [(15 + i, 101.2, 101.2 + 0.1 * (i % 2), 101.0, 101.15)
           for i in range(10)]
    crash2 = [(25 + i, 101.0 - i * 0.5, 101.1 - i * 0.5, 100.4 - i * 0.5,
               100.5 - i * 0.5) for i in range(12)]
    tail2 = [(37 + i, 95.5, 95.7, 95.3, 95.5) for i in range(240)]
    wday = make_day([(i, 100, 101, 99, 100.5) for i in range(15)]
                    + wob + crash2 + tail2)
    run2 = lambda **kw: simulate_symbol_day("TEST", wday, cfg, "any",
                                            2050.0, **kw)
    ae = run2(trail_pct=2.0, max_hold_minutes=0, trail_arm="entry")
    ab = run2(trail_pct=2.0, max_hold_minutes=0, trail_arm="breakeven")
    ad = run2(trail_pct=2.0, max_hold_minutes=0)  # default = entry (old sim)
    assert ae.exit_reason == "trail" and ae.pnl < 0, (ae.exit_reason, ae.pnl)
    assert ab.exit_reason == "stop", ab.exit_reason
    assert ae.pnl > ab.pnl
    assert (ad.exit_reason, ad.exit_price) == (ae.exit_reason, ae.exit_price)
    print("PASS trail arming: entry cuts small-MFE loser early, breakeven "
          "(live v1.8) rides to the raw stop")

    # tp_off: strong runner (+6%) that would hit the scaled TP; with the TP
    # off it keeps running and exits via trail (higher peak) or EOD.
    rise3 = [(15 + i, 101.2 + 0.9 * i, 102.0 + 0.9 * i, 101.0 + 0.9 * i,
              101.9 + 0.9 * i) for i in range(8)]
    fade3 = [(23 + i, 104.5, 104.7, 104.3, 104.5) for i in range(240)]
    rday = make_day([(i, 100, 101, 99, 100.5) for i in range(15)]
                    + rise3 + fade3)
    run3 = lambda **kw: simulate_symbol_day("TEST", rday, cfg, "any",
                                            2050.0, **kw)
    tp_on = run3(trail_pct=None, max_hold_minutes=0)
    tpo_tr = run3(trail_pct=2.0, max_hold_minutes=0, tp_off=True)
    tpo_eod = run3(trail_pct=None, max_hold_minutes=0, tp_off=True)
    assert tp_on.exit_reason == "tp", tp_on.exit_reason
    assert tpo_tr.exit_reason == "trail" and tpo_tr.pnl > 0, tpo_tr.exit_reason
    assert tpo_eod.exit_reason == "eod" and tpo_eod.pnl > 0, tpo_eod.exit_reason
    print("PASS tp_off: runner rides past the scaled TP to trail/EOD exits")

    print("\nAll simulate_symbol_day exit checks passed.")


if __name__ == "__main__":
    main()
