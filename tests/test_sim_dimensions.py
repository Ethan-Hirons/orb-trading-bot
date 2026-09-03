"""Checks for the v1.13 sweep dimensions in backtest.simulate_symbol_day:
`first_candle_dir` (the ORB paper's direction rule) and
`entry_window_override` (entry cutoff in minutes after the open).

Run from the project root (needs alpaca-py installed, no API access required):
  python tests/test_sim_dimensions.py
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


def base_cfg():
    cfg = load_config()
    cfg.strategy.allow_shorts = True
    cfg.strategy.opening_range_minutes = 5
    cfg.strategy.entry_window_minutes = 180
    cfg.strategy.confirm_bar_close = True
    cfg.strategy.confirm_lookback_bars = 3
    cfg.strategy.require_volume_filter = False
    cfg.strategy.respect_news_bias = False
    cfg.exits.stop_pct = 1.5
    cfg.exits.max_hold_minutes = 0
    cfg.exits.breakeven_trigger_pct = 0
    cfg.sizing.max_position_notional = 0
    return cfg


def flat(m_from, m_to, price):
    return [(m, price, price, price, price) for m in range(m_from, m_to)]


# --------------------------------------------------------------------------
# first_candle_dir: the OR candle body picks the side
# --------------------------------------------------------------------------

def bearish_or_then_upside_break():
    """OR candle closes DOWN (100 -> 99) but price later breaks the OR HIGH.

    Live rule: takes the long. Paper rule: bearish OR forbids longs entirely.
    """
    bars = [
        (0, 100.0, 100.5, 99.0, 100.0),
        (1, 100.0, 100.5, 99.0, 99.8),
        (2, 99.8, 100.0, 99.0, 99.5),
        (3, 99.5, 99.8, 99.0, 99.2),
        (4, 99.2, 99.5, 99.0, 99.0),   # OR: open 100.0, close 99.0 -> bearish
    ]
    bars += [(5, 99.0, 99.2, 98.9, 99.1), (6, 99.1, 101.5, 99.1, 101.0)]
    bars += flat(7, 40, 101.0)
    return make_day(bars)


def bullish_or_then_upside_break():
    """OR candle closes UP; price breaks the OR high. Both rules allow long."""
    bars = [
        (0, 99.0, 99.5, 98.8, 99.2),
        (1, 99.2, 99.6, 99.0, 99.4),
        (2, 99.4, 99.8, 99.2, 99.6),
        (3, 99.6, 100.0, 99.4, 99.8),
        (4, 99.8, 100.0, 99.6, 100.0),  # OR: open 99.0, close 100.0 -> bullish
    ]
    bars += [(5, 100.0, 100.2, 99.9, 100.1), (6, 100.1, 101.5, 100.1, 101.0)]
    bars += flat(7, 40, 101.0)
    return make_day(bars)


def doji_or():
    """OR opens and closes at the same price -> paper places no order."""
    bars = [
        (0, 100.0, 100.5, 99.5, 100.2),
        (1, 100.2, 100.6, 99.8, 100.1),
        (2, 100.1, 100.4, 99.7, 99.9),
        (3, 99.9, 100.3, 99.6, 100.1),
        (4, 100.1, 100.5, 99.8, 100.0),  # open 100.0, close 100.0 -> doji
    ]
    bars += [(5, 100.0, 100.2, 99.9, 100.1), (6, 100.1, 101.5, 100.1, 101.0)]
    bars += flat(7, 40, 101.0)
    return make_day(bars)


# --------------------------------------------------------------------------
# entry_window_override: cutoff in minutes after the open
# --------------------------------------------------------------------------

def late_breakout():
    """Range holds until minute 90, then breaks out. Entry fills at m91."""
    bars = [
        (0, 100.0, 100.5, 99.5, 100.1),
        (1, 100.1, 100.5, 99.5, 100.2),
        (2, 100.2, 100.5, 99.5, 100.0),
        (3, 100.0, 100.5, 99.5, 100.1),
        (4, 100.1, 100.5, 99.5, 100.3),  # OR high 100.5, low 99.5, bullish
    ]
    bars += flat(5, 90, 100.0)
    bars += [(90, 100.0, 101.5, 100.0, 101.2)]
    bars += flat(91, 130, 101.2)
    return make_day(bars)


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    return ok


def main():
    cfg = base_cfg()
    results = []

    print("first_candle_dir (ORB paper direction rule):")

    t = simulate_symbol_day("T1", bearish_or_then_upside_break(), cfg, "any",
                            10_000, first_candle_dir=False)
    results.append(check("bearish OR, upside break, rule OFF -> long taken",
                         t.side if t else None, "long"))

    t = simulate_symbol_day("T1", bearish_or_then_upside_break(), cfg, "any",
                            10_000, first_candle_dir=True)
    results.append(check("bearish OR, upside break, rule ON -> no trade",
                         t.side if t else None, None))

    t = simulate_symbol_day("T2", bullish_or_then_upside_break(), cfg, "any",
                            10_000, first_candle_dir=True)
    results.append(check("bullish OR, upside break, rule ON -> long taken",
                         t.side if t else None, "long"))

    t = simulate_symbol_day("T3", doji_or(), cfg, "any", 10_000,
                            first_candle_dir=True)
    results.append(check("doji OR, rule ON -> no trade",
                         t.side if t else None, None))

    t = simulate_symbol_day("T3", doji_or(), cfg, "any", 10_000,
                            first_candle_dir=False)
    results.append(check("doji OR, rule OFF -> long taken",
                         t.side if t else None, "long"))

    print("\nentry_window_override (entry cutoff):")

    t = simulate_symbol_day("T4", late_breakout(), cfg, "any", 10_000)
    results.append(check("breakout at m90, no override (cfg 180) -> entered",
                         t is not None, True))

    t = simulate_symbol_day("T4", late_breakout(), cfg, "any", 10_000,
                            entry_window_override=180)
    results.append(check("breakout at m90, window 180 -> entered",
                         t is not None, True))

    t = simulate_symbol_day("T4", late_breakout(), cfg, "any", 10_000,
                            entry_window_override=60)
    results.append(check("breakout at m90, window 60 -> skipped",
                         t is not None, False))

    t = simulate_symbol_day("T4", late_breakout(), cfg, "any", 10_000,
                            entry_window_override=30)
    results.append(check("breakout at m90, window 30 -> skipped",
                         t is not None, False))

    # The override must not disturb an early entry.
    t = simulate_symbol_day("T2", bullish_or_then_upside_break(), cfg, "any",
                            10_000, entry_window_override=30)
    results.append(check("breakout at m6, window 30 -> entered",
                         t is not None, True))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
        return 0
    print(f"{results.count(False)}/{len(results)} checks FAILED.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
