"""Checks for index_scan.py's signal rules on synthetic sessions.

Run with:  python tests/test_index_scan.py
No network, no cache: every session is built by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import index_scan as ix
from index_scan import COST, Session


def day(d: str, price, last: int = 389, vol: float = 100.0) -> Session:
    """price(minute) -> close of that minute's bar; open == close for simplicity."""
    return Session(d, [(m, price(m), price(m), price(m), price(m), vol, 0.0)
                       for m in range(last + 1)])


def flat(d: str, px: float = 100.0, last: int = 389) -> Session:
    return day(d, lambda m: px, last)


def close(a, b, tol=1e-12):
    return abs(a - b) < tol


def test_session_price_is_close_of_previous_bar():
    s = Session("2024-01-02", [(0, 10, 10, 10, 10, 1, 0), (5, 11, 11, 11, 11, 1, 0),
                               (389, 12, 12, 12, 12, 1, 0)])
    assert s.open == 10 and s.close == 12 and s.full and not s.early
    assert s.p(3) == 10          # bars 1-2 missing: forward-filled from 09:30
    assert s.p(6) == 11          # price at 09:36 = close of the 09:35 bar
    assert s.p(360) == 11
    # a half-day keeps its after-hours bars in the feed; they must not become the close
    half = day("2024-11-29", lambda m: 10.0 if m < 210 else 99.0)
    assert half.early and not half.full and not half.broken and half.close == 10.0
    gap = flat("2019-08-12", last=361)                 # feed stops at 15:31
    assert gap.broken and not gap.full and not gap.early
    late_open = Session("2024-01-03", [(7, 1, 1, 1, 1, 1, 0), (389, 1, 1, 1, 1, 1, 0)])
    assert late_open.open is None and ix.sessions({"x": late_open.bars}) == []
    print("PASS test_session_price_is_close_of_previous_bar")


def test_h1_trades_last_half_hour_in_direction_of_day():
    prev = flat("2024-01-02")
    up = day("2024-01-03", lambda m: 101.0 if m < 389 else 102.0)
    down = day("2024-01-04", lambda m: 99.0 if m < 389 else 98.0)
    rows = ix.h1_rows([prev, up])
    assert len(rows) == 1 and rows[0]["side"] == 1
    assert close(rows[0]["r"], 102 / 101 - 1 - 2 * COST)
    rows = ix.h1_rows([up, down])
    assert rows[0]["side"] == -1                       # 99 < previous close 102
    assert close(rows[0]["r"], -(98 / 99 - 1) - 2 * COST)
    # an early-close session is never traded, and a broken close is never a reference
    assert ix.h1_rows([prev, flat("2024-11-29", 101, last=209)]) == []
    assert ix.h1_rows([flat("2019-08-12", last=361), up]) == []
    print("PASS test_h1_trades_last_half_hour_in_direction_of_day")


def test_h2_overnight_is_close_to_next_open():
    a = flat("2024-01-05", 100.0)                      # Friday
    b = day("2024-01-08", lambda m: 101.0 if m == 0 else 103.0)   # Monday
    rows = ix.h2_rows([a, b])
    assert close(rows[0]["r"], 101 / 100 - 1 - 2 * COST)
    assert close(rows[0]["intraday"], 103 / 101 - 1 - 2 * COST)
    assert rows[0]["monday"] and rows[0]["day"] == "2024-01-08"
    print("PASS test_h2_overnight_is_close_to_next_open")


def _history(n=14):
    # every history day sits 0.1% above its open after 09:30 -> sigma = 0.001
    return [day(f"2024-02-{i + 1:02d}", lambda m: 100.0 if m == 0 else 100.1) for i in range(n)]


def test_h3_enters_on_band_breach_and_holds_to_close():
    hist = _history()
    breakout = day("2024-03-01", lambda m: 100.0 if m < 25 else (100.5 if m < 389 else 101.0))
    rows = ix.h3_rows(hist + [breakout])
    assert len(rows) == 1 and rows[0]["active"]
    # UB = max(open 100, prev close 100.1) * 1.001 = 100.2001; 100.5 breaches at 10:00
    assert close(rows[0]["r"], 101 / 100.5 - 1 - 2 * COST)
    quiet = day("2024-03-01", lambda m: 100.05)
    rows = ix.h3_rows(hist + [quiet])
    assert not rows[0]["active"] and rows[0]["r"] == 0
    print("PASS test_h3_enters_on_band_breach_and_holds_to_close")


def test_h3_flips_on_opposite_band_and_charges_both_sides():
    hist = _history()

    def path(m):
        if m < 29:
            return 100.0
        if m < 59:
            return 100.5        # 10:00 checkpoint: long
        return 99.0 if m == 389 else 99.5   # 10:30 checkpoint: below LB -> flip short

    rows = ix.h3_rows(hist + [day("2024-03-01", path)])
    expect = (99.5 / 100.5 - 1) + -(99.0 / 99.5 - 1) - 4 * COST   # entry, flip (2), close
    assert close(rows[0]["r"], expect), rows[0]
    assert close(rows[0]["long"], 99.5 / 100.5 - 1)
    print("PASS test_h3_flips_on_opposite_band_and_charges_both_sides")


def test_h3_vwap_stop_exits_where_base_model_holds():
    hist = _history()

    def path(m):
        if m < 25:
            return 100.0
        if m < 30:
            return 101.0        # 10:00: long at 101, VWAP ~100.17 is below the band
        if m < 60:
            return 100.3        # 10:30: VWAP ~100.23, still held
        return 99.0 if m == 389 else 100.21   # 11:00: below VWAP ~100.226 -> stopped

    s = day("2024-03-01", path)
    base = ix.h3_rows(hist + [s])[0]
    trail = ix.h3_rows(hist + [s], vwap_stop=True)[0]
    assert close(base["r"], 99.0 / 101 - 1 - 2 * COST)        # never reaches LB 99.9
    assert close(trail["r"], 100.21 / 101 - 1 - 2 * COST)     # one exit, no re-entry
    print("PASS test_h3_vwap_stop_exits_where_base_model_holds")


def test_h3_lookback_skips_early_closes():
    hist = _history()
    early = flat("2023-11-24", 100.0, last=209)
    today = day("2024-03-01", lambda m: 100.0 if m < 25 else 100.5)
    rows = ix.h3_rows(hist[:7] + [early] + hist[7:] + [today])
    # the early close is neither scored nor counted in the 14-day lookback
    assert [r["day"] for r in rows] == ["2024-03-01"]
    print("PASS test_h3_lookback_skips_early_closes")


def test_h4_turn_of_month_days():
    days = ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02", "2024-02-05",
            "2024-02-06", "2024-02-28", "2024-02-29", "2024-03-01"]
    ss = [flat(d, 100.0 + i) for i, d in enumerate(days)]
    tom = {r["day"]: r["tom"] for r in ix.h4_rows(ss)}
    # January's position-in-month is unknown (data starts mid-month), so it's skipped
    assert tom == {"2024-02-01": True, "2024-02-02": True, "2024-02-05": True,
                   "2024-02-06": False, "2024-02-28": False, "2024-02-29": True,
                   "2024-03-01": True}, tom
    w = ix.h4_windows(ss)
    assert len(w) == 1 and w[0]["day"] == "2024-01-31"     # Feb's +3 is past the data
    assert close(w[0]["r"], 104 / 100 - 1 - 2 * COST)      # Close(-2) Jan 30 -> Close(+3) Feb 5
    print("PASS test_h4_turn_of_month_days")


def test_e16_verdict_looks_only_at_fixed_sample_sizes():
    v = ix.e16_verdict
    assert v([0.001] * 10, [0.001]).startswith("RUNNING: 10/250")
    # futility is judged on the first 120 exactly; a later recovery doesn't undo it
    early_bad = [-0.0001] * 120 + [0.01] * 200
    assert v(early_bad, [0.001]).startswith("STOPPED for futility at 120")
    assert v([0.0001] * 150, [0.001]).endswith("next look at 180")
    # clear positive drift with noise passes; the same with long-only <= 0 fails
    good = [0.002 if i % 2 else -0.001 for i in range(260)]
    assert v(good, [0.001]).startswith("FINAL PASS"), v(good, [0.001])
    assert v(good, [-0.001]).startswith("FINAL FAIL")
    # positive only because of five huge sessions -> fails the without-top-5 check
    lucky = [0.05] * 5 + [-0.0002] * 245
    assert v(lucky, [0.001]).startswith(("FINAL FAIL", "STOPPED"))
    print("PASS test_e16_verdict_looks_only_at_fixed_sample_sizes")


def run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    run_all()
