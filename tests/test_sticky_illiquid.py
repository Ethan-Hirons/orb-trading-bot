"""v1.13: a name flagged illiquid earlier today must stay out for the rest of
the day, even if a later scan would let it back in.

Regression for NEBX on 2026-08-13: on the pre-market illiquid list at 01:35,
absent from the 15:00 list once the prev-day volume bar rolled over, then
entered at 15:47 on a +68.5% gap and lost -10.76. invariants.py FAILs a session
that trades a once-illiquid name; this asserts the bot obeys that rule.

Run from the project root:
  python tests/test_sticky_illiquid.py
No network, no config changes — a fake broker supplies all market data.
"""
import os
import sys

os.environ.setdefault("ORB_NO_FILE_LOG", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orb_bot.config import load_config
from orb_bot.screener import Screener


class FakeBroker:
    """Serves one scan's worth of market data. `volumes` is mutated between
    scans to simulate the prev-day bar rolling over."""

    def __init__(self, volumes):
        self.volumes = volumes

    def get_latest_prices(self, symbols):
        return {s: 40.0 for s in symbols}

    def get_prev_closes(self, symbols):
        return {s: 20.0 for s in symbols}  # +100% gap: always passes the gap filter

    def get_prev_day_volumes(self, symbols):
        return {s: self.volumes.get(s, 0.0) for s in symbols}


def make_screener(cfg, broker, symbols):
    sc = Screener.__new__(Screener)
    sc.cfg = cfg
    sc.broker = broker
    sc._most_actives = lambda: {}
    sc._movers = lambda: {s: 100.0 for s in symbols}
    return sc


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    return ok


def main():
    cfg = load_config()
    cfg.screener.enabled = True
    cfg.screener.min_prev_day_volume = 2_000_000
    cfg.screener.min_price = 5
    cfg.screener.max_price = 400
    cfg.screener.min_abs_gap_pct = 2.0
    cfg.screener.max_abs_gap_pct = 0
    cfg.screener.max_candidates = 40
    cfg.runtime.data_feed = "sip"  # no /25 IEX scaling, keeps the math obvious

    results = []
    syms = ["NEBX", "LIQD"]

    # Scan 1 (pre-market): NEBX is thin, LIQD is fine.
    broker = FakeBroker({"NEBX": 50_000.0, "LIQD": 9_000_000.0})
    sc = make_screener(cfg, broker, syms)
    got = sorted(c.symbol for c in sc.build_candidates())
    results.append(check("scan 1: thin name excluded", got, ["LIQD"]))

    # Scan 2: NEBX's prev-day volume now clears the floor on its own.
    # Without the sticky set it would come back as a candidate.
    broker.volumes["NEBX"] = 9_000_000.0
    got = sorted(c.symbol for c in sc.build_candidates())
    results.append(check("scan 2: stays excluded despite passing the floor",
                         got, ["LIQD"]))

    # Scan 3: still out. The flag is for the whole session.
    got = sorted(c.symbol for c in sc.build_candidates())
    results.append(check("scan 3: still excluded", got, ["LIQD"]))

    # A clean name is never affected.
    results.append(check("liquid name still present across all scans",
                         "LIQD" in got, True))

    # New session date clears the set.
    sc._illiquid_day = None
    got = sorted(c.symbol for c in sc.build_candidates())
    results.append(check("new session: sticky set reset, NEBX eligible again",
                         got, ["LIQD", "NEBX"]))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
        return 0
    print(f"{results.count(False)}/{len(results)} checks FAILED.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
