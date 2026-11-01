"""Checks for trend.py on hand-built series. Run with:  python tests/test_trend.py

No network and no cache: every price path below is synthetic, with a known answer.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trend as tr


def _days(start: str, n: int) -> list[str]:
    d, out = date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def close(a, b, tol=1e-12):
    return abs(a - b) < tol


def test_month_ends_are_last_trading_days():
    days = ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-29", "2024-03-01"]
    assert tr.month_ends(days) == [1, 3, 4]
    print("PASS test_month_ends_are_last_trading_days")


def test_t1_needs_ten_month_ends_and_compares_with_their_mean():
    me = list(range(12))
    rising = [100 + i for i in range(12)]            # last close above its 10-month mean
    assert tr.t1_signal(rising, me, 8) is None      # only 9 month-ends so far
    assert tr.t1_signal(rising, me, 9) is True
    falling = [100 - i for i in range(12)]
    assert tr.t1_signal(falling, me, 11) is False
    flat = [100.0] * 12                               # equal to the mean is NOT above it
    assert tr.t1_signal(flat, me, 11) is False
    assert tr.t1_signal([None] * 3 + rising[3:], me, 10) is None   # history starts too late
    print("PASS test_t1_needs_ten_month_ends_and_compares_with_their_mean")


def test_t2_beats_cash_over_twelve_months():
    me = list(range(14))
    px = [100.0] * 13 + [None]
    px[12] = 103.0                                    # +3% over 12 month-ends
    cash_lo = [1.0 + 0.02 * i / 12 for i in range(14)]   # cash +2% over the year
    cash_hi = [1.0 + 0.05 * i / 12 for i in range(14)]   # cash +5%
    assert tr.t2_signal(px, cash_lo, me, 11) is None      # needs 12 months back
    assert tr.t2_signal(px, cash_lo, me, 12) is True
    assert tr.t2_signal(px, cash_hi, me, 12) is False
    assert tr.t2_signal(px, cash_lo, me, 13) is None      # no price at this month-end
    print("PASS test_t2_beats_cash_over_twelve_months")


def test_signals_trade_the_next_day_and_empty_slots_sit_in_cash():
    days = _days("2023-01-02", 400)
    me = tr.month_ends(days)
    px = {t: [100.0 * (1.001 ** i) for i in range(len(days))] for t in tr.UNIVERSE}
    px["DBC"] = [100.0 * (0.999 ** i) for i in range(len(days))]     # a falling asset
    cash = [0.0] + [0.0001] * (len(days) - 1)
    tg = tr.targets("T1", days, px, cash, me)
    k = 12
    w = tg[me[k] + 1]                                   # executes the day AFTER the month-end
    assert me[k] not in tg and me[k] + 1 in tg
    assert "DBC" not in w and close(w["CASH"], 1 / 8) and close(w["SPY"], 1 / 8)
    assert close(sum(w.values()), 1.0)
    ew = tr.targets("EW", days, px, cash, me)[me[k] + 1]
    assert close(ew["CASH"], 0.0) and close(ew["DBC"], 1 / 8)
    assert tr.targets("6040", days, px, cash, me)[me[k] + 1] == {"SPY": 0.6, "IEF": 0.4, "CASH": 0.0}
    # the last month-end has no next day yet, so it has no trade
    assert me[-1] + 1 not in tg
    print("PASS test_signals_trade_the_next_day_and_empty_slots_sit_in_cash")


def test_simulate_drifts_charges_turnover_and_rebalances_on_execution_days():
    days = _days("2024-01-01", 4)
    px = {"SPY": [100.0, 110.0, 99.0, 99.0]}
    cash = [0.0, 0.0, 0.0, 0.0]
    tg = {0: {"SPY": 0.5, "CASH": 0.5}, 2: {"SPY": 0.5, "CASH": 0.5}}
    path = tr.simulate(days, px, cash, tg, 0, 3, cost=0.001)
    v0 = 1 - 0.001 * 1.0                        # from all cash: sell 0.5 cash + buy 0.5 SPY
    assert close(path[0][1], v0)
    v1 = v0 * (1 + 0.5 * 0.10)                  # day 1: SPY +10% on half the book
    assert close(path[1][1], v1)
    w_spy = 0.5 * 1.10 / 1.05                   # drifted weight before day 2
    v2 = v1 * (1 + w_spy * (99 / 110 - 1))      # day 2 uses the OLD weights...
    w_spy2 = w_spy * (99 / 110) / (1 + w_spy * (99 / 110 - 1))
    v2 *= 1 - 0.001 * 2 * abs(0.5 - w_spy2)     # ...then resets to 50/50 at that close
    assert close(path[2][1], v2), (path[2][1], v2)
    assert close(path[3][1], v2)                # flat day
    print("PASS test_simulate_drifts_charges_turnover_and_rebalances_on_execution_days")


def test_metrics_drawdown_cagr_sharpe_and_worst_year():
    days = _days("2020-01-01", 600)
    vals = [1.0] * 600
    for i in range(1, 600):
        vals[i] = vals[i - 1] * (1.001 if i % 2 else 0.9995)
    vals[300:] = [v * 0.8 for v in vals[300:]]           # a one-day 20% drop at day 300
    path = list(zip(days, vals))
    m = tr.metrics(path, [0.0] * 600, days)
    peak = max(vals[:300])
    assert close(m["mdd"], min(v / max(vals[: i + 1]) - 1 for i, v in enumerate(vals)))
    assert m["mdd"] < -0.19 and m["mdd"] <= vals[300] / peak - 1 + 1e-12
    span = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days
    assert close(m["cagr"], (vals[-1] / vals[0]) ** (365.25 / span) - 1)
    # the worst 252-day window holds the drop: 0.8 x the ~6.5% a year of drift, about -14.8%
    assert -0.16 < m["worst12"] < -0.10, m["worst12"]
    up = tr.metrics([(d, 1.0005 ** i) for i, d in enumerate(days)], [0.0] * 600, days)
    assert up["mdd"] == 0 and up["cagr"] > 0
    print("PASS test_metrics_drawdown_cagr_sharpe_and_worst_year")


def test_rebase_yearly_and_criteria():
    path = [("2008-12-31", 1.0), ("2009-01-02", 1.1), ("2009-12-31", 1.21), ("2010-06-30", 1.331)]
    sub = tr.rebase(path, "2009-01-02", "2009-12-31")
    assert sub[0][1] == 1.0 and close(sub[-1][1], 1.1)
    y = tr.yearly(path)
    assert close(y["2009"], 0.21) and close(y["2010"], 0.10) and y["2008"] == 0.0
    ew = {"mdd": -0.40, "cagr": 0.07, "sharpe": 0.50}
    # just inside each line: drawdown 30% smaller, CAGR within 1.5 pp, Sharpe not lower
    assert tr.criteria({"mdd": -0.279, "cagr": 0.0551, "sharpe": 0.50}, ew) == {"a": True, "b": True, "c": True}
    assert tr.criteria({"mdd": -0.281, "cagr": 0.0549, "sharpe": 0.49}, ew) == {"a": False, "b": False, "c": False}
    print("PASS test_rebase_yearly_and_criteria")


def run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    run_all()
