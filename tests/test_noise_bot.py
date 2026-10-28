"""Checks for noise_bot.py: a whole simulated session against a fake broker and clock.

Run with:  python tests/test_noise_bot.py
No network, no keys, no orders: FakeBroker fills everything itself.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import index_scan as ix
import noise_bot as nb
from index_scan import ET

TODAY = date(2026, 11, 2)          # a Monday
ALERTS: list = []
nb.notify = lambda title, body, priority="default": ALERTS.append((title, body))


def _days_before(d: date, n: int) -> list[str]:
    out = []
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.isoformat())
    return out[::-1]


def _bar(m: int, px: float) -> tuple:
    return (m, px, px, px, px, 1000.0, 0.0)


def _history(today_bars=17):
    """20 full sessions sitting 0.1% above their open (sigma = 0.001), then today's first bars."""
    hist = {d: [_bar(m, 100.0 if m == 0 else 100.1) for m in range(390)]
            for d in _days_before(TODAY, 20)}
    hist[TODAY.isoformat()] = [_bar(m, 100.0) for m in range(today_bars)]
    return hist


class FakeClock:
    def __init__(self, t: datetime):
        self.t = t

    def now(self):
        return self.t

    def sleep_until(self, t):
        self.t = max(self.t, t)


class FakeBroker:
    def __init__(self, clock, prices, account="PA1", held=None, foreign_orders=(),
                 cal=None, moc_rejects=False):
        self.clock, self.prices = clock, prices
        self.account = account
        self.held = dict(held or {})
        self.foreign_orders = set(foreign_orders)
        self.cal = cal or {**{d: "16:00" for d in _days_before(TODAY, 20)}, TODAY.isoformat(): "16:00"}
        self.moc_rejects = moc_rejects
        self.orders, self._status = [], {}
        self.close_price = 99.0

    def _apply(self, side, qty, price):
        q = self.held.get("QQQ", 0.0) + (qty if side == "buy" else -qty)
        if q:
            self.held["QQQ"] = q
        else:
            self.held.pop("QQQ", None)

    def _settle(self):
        if self.clock.now().time() >= dtime(16, 0):
            for oid, (status, price, side, qty, moc) in list(self._status.items()):
                if moc and status == "accepted":
                    self._apply(side, qty, self.close_price)
                    self._status[oid] = ("filled", self.close_price, side, qty, moc)

    def account_number(self):
        return self.account

    def positions(self):
        self._settle()
        return dict(self.held)

    def open_order_symbols(self):
        return set(self.foreign_orders)

    def market(self, side, qty, at_close=False):
        oid = f"o{len(self.orders)}"
        self.orders.append((side, qty, at_close))
        if at_close:
            if self.moc_rejects:
                raise RuntimeError("MOC not supported")
            self._status[oid] = ("accepted", 0.0, side, qty, True)
        else:
            price = self.prices(self.clock.now())
            self._apply(side, qty, price)
            self._status[oid] = ("filled", price, side, qty, False)
        return oid

    def order(self, oid):
        self._settle()
        status, price, *_ = self._status[oid]
        return status, price

    def flatten(self):
        self.held.clear()

    def latest_trade(self):
        return self.prices(self.clock.now()), self.clock.now()

    def minute_bars(self, start, end):
        return _history()

    def calendar(self, start, end):
        return dict(self.cal)


def _price_path(t: datetime) -> float:
    # UB = max(open 100, prev close 100.1) * 1.001 = 100.2001;  LB = 100 * 0.999 = 99.9
    if t.time() < dtime(10, 0):
        return 100.0
    if t.time() < dtime(10, 30):
        return 100.5           # 10:00: above UB -> long
    return 99.5                # 10:30: below LB -> flip short; holds short after


def _session(**kw):
    with tempfile.TemporaryDirectory() as tmp:
        nb.LEDGER, nb.DISARM = Path(tmp) / "ledger.csv", Path(tmp) / "NOISE_DISARMED"
        clock = FakeClock(datetime.combine(TODAY, dtime(9, 15), tzinfo=ET))
        b = FakeBroker(clock, kw.pop("prices", _price_path), **kw)
        rc = nb.run(b, clock, expected_account="PA1")
        rows = nb._rows()
    return rc, b, rows


def test_full_session_enters_flips_and_exits_on_close():
    rc, b, rows = _session()
    assert rc == 0
    assert b.orders == [("buy", 99, False),            # 10:00: 10_000 // 100.5
                        ("sell", 99, False),           # 10:30: close the long...
                        ("sell", 100, False),          # ...then open the short, 10_000 // 99.5
                        ("buy", 100, True)], b.orders  # market-on-close cover
    assert not b.held
    assert len(rows) == 13 and rows[-1]["checkpoint"] == "close" and rows[-1]["pos_after"] == "0"
    assert [r["pos_after"] for r in rows[:3]] == ["1", "-1", "-1"]
    print("PASS test_full_session_enters_flips_and_exits_on_close")


def test_rejected_moc_falls_back_to_market_before_the_close():
    rc, b, rows = _session(moc_rejects=True)
    assert rc == 0 and not b.held
    assert b.orders[-1] == ("buy", 100, False), b.orders      # 15:58 market cover
    print("PASS test_rejected_moc_falls_back_to_market_before_the_close")


def test_refuses_an_account_holding_anything_else():
    for kw in ({"held": {"AAPL": 5}}, {"foreign_orders": {"TSLA"}}, {"account": "PA-ORB"}):
        rc, b, rows = _session(**kw)
        assert rc == 2 and b.orders == [] and rows == [], kw
    print("PASS test_refuses_an_account_holding_anything_else")


def test_half_day_and_holiday_are_not_traded():
    cal = {**{d: "16:00" for d in _days_before(TODAY, 20)}, TODAY.isoformat(): "13:00"}
    rc, b, rows = _session(cal=cal)
    assert rc == 0 and b.orders == [] and rows == []
    rc, b, rows = _session(cal={d: "16:00" for d in _days_before(TODAY, 20)})
    assert rc == 0 and b.orders == [] and rows == []
    print("PASS test_half_day_and_holiday_are_not_traded")


def test_overnight_leftover_is_closed_at_the_open():
    rc, b, rows = _session(held={"QQQ": 7.0}, prices=lambda t: 100.0)   # never breaches
    assert b.orders == [("sell", 7, False)] and not b.held and rc == 0
    print("PASS test_overnight_leftover_is_closed_at_the_open")


def test_stale_price_holds_instead_of_trading():
    with tempfile.TemporaryDirectory() as tmp:
        nb.LEDGER, nb.DISARM = Path(tmp) / "ledger.csv", Path(tmp) / "x"
        clock = FakeClock(datetime.combine(TODAY, dtime(9, 15), tzinfo=ET))
        b = FakeBroker(clock, _price_path)
        b.latest_trade = lambda: (100.5, clock.now() - timedelta(minutes=10))
        assert nb.run(b, clock, expected_account="PA1") == 0
        assert b.orders == []
        assert all("no fresh price" in r["orders"] for r in nb._rows() if r["checkpoint"] != "close")
    print("PASS test_stale_price_holds_instead_of_trading")


def test_plan_matches_the_backtest_rule():
    """The levels the bot trades must be the ones index_scan's shadow replays."""
    hist = _history(today_bars=390)
    cal = {d: "16:00" for d in hist}
    p, why = nb.plan(hist, cal, TODAY.isoformat())
    assert p and not why
    ss = ix.sessions(hist, frozenset())
    path, s = nb.shadow_path(ss, TODAY.isoformat())
    for m, _, ub, lb, _ in path:
        assert p["levels"][m][1:] == (ub, lb), m
    assert abs(p["levels"][30][1] - 100.2001) < 1e-9 and abs(p["levels"][30][2] - 99.9) < 1e-9
    print("PASS test_plan_matches_the_backtest_rule")


def test_reconcile_scores_agreement_and_slippage_against_sip():
    import contextlib
    import io

    open_dt = datetime.combine(TODAY, dtime(9, 30), tzinfo=ET)
    sip = _history()
    # SIP bar k closes at the price the fake broker quoted one minute later: P(m) = price at m
    sip[TODAY.isoformat()] = [_bar(k, _price_path(open_dt + timedelta(minutes=k + 1)))
                              for k in range(390)]
    sip[TODAY.isoformat()][0] = _bar(0, 100.0)
    real_load = ix.load
    with tempfile.TemporaryDirectory() as tmp:
        nb.LEDGER, nb.DISARM = Path(tmp) / "ledger.csv", Path(tmp) / "x"
        clock = FakeClock(datetime.combine(TODAY, dtime(9, 15), tzinfo=ET))
        nb.run(FakeBroker(clock, _price_path), clock, expected_account="PA1")
        ix.load = lambda symbols=ix.SYMBOLS: {"QQQ": sip}
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                nb.reconcile()
        finally:
            ix.load = real_load
    text = out.getvalue()
    assert "full coverage 1/1, verified flat 1/1" in text, text
    assert "12/12 (100.0%)" in text, text
    # three entry fills at the SIP price (0 bp) and a cover at 99.0 against a 99.5 close:
    # (99.0 / 99.5 - 1) / 4 fills = -50.25 bp / 4
    assert "mean -12.56 bp over 4 fills" in text, text
    print("PASS test_reconcile_scores_agreement_and_slippage_against_sip")


def run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    run_all()
