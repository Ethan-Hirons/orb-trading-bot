"""Checks for noise_bot.py: whole simulated sessions against a fake broker and clock.

Run with:  python tests/test_noise_bot.py
No network, no keys, no orders: FakeBroker fills everything itself.

FakeBroker models what Alpaca did on 2026-10-30, not an idealised exchange:
  * market-on-close (CLS) orders sit "accepted" until the 16:00 auction, then fill fully,
    partly (the rest expires) or not at all, as configured;
  * DAY market orders sent before 09:30 queue and fill at the open;
  * DAY market orders sent after 16:00 queue for the NEXT session (never fill today);
  * regular-hours market orders fill at once, unless `stall` says the next exit order hangs.
It accepts both the pre-fix calls (market(..., at_close), flatten) and the fixed ones
(cancel, open_order_ids, filled), so the regression tests can run against either version.
"""

from __future__ import annotations

import contextlib
import io
import json
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
nb.notify = lambda title, body, priority="default": ALERTS.append((title, body, priority))
OPEN = {"accepted", "new", "partially_filled", "pending_new"}


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


def _at(t: dtime) -> datetime:
    return datetime.combine(TODAY, t, tzinfo=ET)


class FakeClock:
    def __init__(self, t: datetime):
        self.t = t

    def now(self):
        return self.t

    def sleep_until(self, t):
        self.t = max(self.t, t)


class FakeBroker:
    def __init__(self, clock, prices, account="PA1", held=None, foreign_orders=(),
                 cal=None, moc="full", stall=0, queued=()):
        self.clock, self.prices = clock, prices
        self.account = account
        self.held = dict(held or {})
        self.foreign_orders = set(foreign_orders)
        self.cal = cal or {**{d: "16:00" for d in _days_before(TODAY, 20)}, TODAY.isoformat(): "16:00"}
        self.moc = moc            # the closing auction: "full", "partial", "none", or "reject" on send
        self.stall = stall        # this many market orders sent at/after 15:58 never fill
        self.orders, self.book, self.fills = [], {}, []
        self.close_price = 99.0
        for side, qty in queued:  # e.g. Friday's after-hours close, still working on Monday
            self.book[f"q{len(self.book)}"] = dict(side=side, qty=qty, kind="queued",
                                                   status="accepted", price=0.0, filled=0)

    # --- the exchange
    def _fill(self, o, n, price):
        if n:
            q = self.held.get("QQQ", 0.0) + (n if o["side"] == "buy" else -n)
            if q:
                self.held["QQQ"] = q
            else:
                self.held.pop("QQQ", None)
            self.fills.append((self.clock.now(), o["side"], n, price))
        o.update(filled=n, price=price if n else 0.0, status="filled" if n == o["qty"] else "expired")

    def _settle(self):
        now = self.clock.now()
        for o in self.book.values():
            if o["status"] not in OPEN:
                continue
            if o["kind"] in ("preopen", "queued") and now >= _at(dtime(9, 30)):
                self._fill(o, o["qty"], self.prices(_at(dtime(9, 30))))
            elif o["kind"] == "cls" and now >= _at(dtime(16, 0)):
                n = {"full": o["qty"], "partial": o["qty"] * 9 // 14, "none": 0}[self.moc]
                self._fill(o, n, self.close_price)

    # --- the broker API
    def account_number(self):
        return self.account

    def positions(self):
        self._settle()
        return dict(self.held)

    def open_order_ids(self):
        self._settle()
        return [oid for oid, o in self.book.items() if o["status"] in OPEN]

    def open_order_symbols(self):
        return ({"QQQ"} if self.open_order_ids() else set()) | self.foreign_orders

    def market(self, side, qty, at_close=False):
        now = self.clock.now()
        oid = f"o{len(self.orders)}"
        self.orders.append((side, qty, at_close))
        if at_close and self.moc == "reject":
            raise RuntimeError("MOC not supported")
        kind = ("cls" if at_close else "preopen" if now < _at(dtime(9, 30))
                else "afterhours" if now >= _at(dtime(16, 0)) else "day")
        o = self.book[oid] = dict(side=side, qty=qty, kind=kind, status="accepted", price=0.0, filled=0)
        if kind == "day":
            if now >= _at(dtime(15, 58)) and self.stall > 0:
                self.stall -= 1
                o["status"] = "new"                  # hangs until cancelled
            else:
                self._fill(o, qty, self.prices(now))
        return oid

    def order(self, oid):
        self._settle()
        return self.book[oid]["status"], self.book[oid]["price"]

    def filled(self, oid):
        self._settle()
        return self.book[oid]["filled"]

    def cancel(self, oid):
        if self.book[oid]["status"] in OPEN:
            self.book[oid]["status"] = "canceled"

    def flatten(self):
        """Alpaca's close_all_positions(cancel_orders=True): after hours it QUEUES the closes."""
        for oid in self.open_order_ids():
            self.cancel(oid)
        for sym, q in list(self.held.items()):
            self.market("sell" if q > 0 else "buy", int(abs(q)))

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


def _session(start=dtime(9, 15), **kw):
    ALERTS.clear()
    with tempfile.TemporaryDirectory() as tmp:
        nb.LEDGER, nb.DISARM = Path(tmp) / "ledger.csv", Path(tmp) / "NOISE_DISARMED"
        clock = FakeClock(_at(start))
        b = FakeBroker(clock, kw.pop("prices", _price_path), **kw)
        rc = nb.run(b, clock, expected_account="PA1")
        rows = nb._rows()
    return rc, b, rows


def _bought(b) -> float:
    return sum(n if side == "buy" else -n for _, side, n, _ in b.fills)


# ---------------------------------------------------------------- close-out regressions
# Each of these fails on the pre-fix close-out (MOC at 15:30, fallback only on a terminal
# MOC, after-hours flatten at 16:05) and passes on the fixed one.

def test_regression_moc_pending_then_expiring():
    """2026-10-30: the MOC filled 9 of 14 in the auction and the rest expired at 16:02."""
    rc, b, rows = _session(moc="partial")
    assert not any(cls for _, _, cls in b.orders), "no market-on-close orders at all"
    assert rc == 0 and not b.held, (rc, b.held)
    assert b.open_order_ids() == [], "nothing queued for tomorrow"
    print("PASS test_regression_moc_pending_then_expiring")


def test_regression_moc_filled():
    """Even a fully filled MOC flattens only in the auction; the fix is flat before 16:00."""
    rc, b, rows = _session(moc="full")
    assert b.orders == [("buy", 99, False),            # 10:00: 10_000 // 100.5
                        ("sell", 99, False),           # 10:30: close the long...
                        ("sell", 100, False),          # ...then open the short, 10_000 // 99.5
                        ("buy", 100, False)], b.orders # 15:58: one closing market order
    t, side, n, _ = b.fills[-1]
    assert (side, n) == ("buy", 100) and _at(dtime(15, 58)) <= t < _at(dtime(16, 0)), b.fills[-1]
    rec = json.loads(rows[-1]["orders"])
    assert rec["v"] == 2 and [(e["status"], e["filled"], e["at"]) for e in rec["exits"]] == \
        [("filled", 100, "15:58:00")], rec
    assert rc == 0 and rows[-1]["pos_after"] == "0"
    print("PASS test_regression_moc_filled")


def test_regression_fallback_filled():
    """The first closing order hangs: it is cancelled and confirmed dead, then the retry fills.

    Pre-fix: MOC rejected, the 15:58 fallback hangs, nothing retries, 16:05 not flat.
    """
    rc, b, rows = _session(moc="reject", stall=1)
    assert rc == 0 and not b.held and b.open_order_ids() == [], (rc, b.held)
    exits = json.loads(rows[-1]["orders"])["exits"]
    assert [e["status"] for e in exits] == ["canceled", "filled"], exits
    assert _bought(b) == 0, "exactly one closing fill: never double-traded"
    print("PASS test_regression_fallback_filled")


def test_regression_not_flat_at_1605():
    """No exit fills at all: alert, cancel, and queue NOTHING after hours."""
    rc, b, rows = _session(moc="none", stall=99)
    assert rc == 3 and b.held == {"QQQ": -100.0}, (rc, b.held)
    assert b.open_order_ids() == [], "an after-hours close would fill on top of Monday's"
    assert any(t == "NOT FLAT" and p == "urgent" for t, _, p in ALERTS), ALERTS
    assert rows[-1]["checkpoint"] == "close" and rows[-1]["pos_after"] == "?"
    print("PASS test_regression_not_flat_at_1605")


def test_regression_monday_leftover_with_a_queued_order():
    """Friday left short 5 AND a queued buy 5. Pre-fix both buys fill at the open: long 5."""
    rc, b, rows = _session(held={"QQQ": -5.0}, queued=[("buy", 5)], prices=lambda t: 100.0)
    assert not b.held, f"account must be flat after the open, not {b.held}"
    assert [n for _, side, n, _ in b.fills] == [5], b.fills
    assert rc == 0 and b.open_order_ids() == []
    print("PASS test_regression_monday_leftover_with_a_queued_order")


# ---------------------------------------------------------------- the rest of the session

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
        clock = FakeClock(_at(dtime(9, 15)))
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


def test_reconcile_counts_stage_a_from_the_fix_and_scores_exits_same_minute():
    open_dt = _at(dtime(9, 30))
    sip = _history()
    # SIP bar k closes at the price the fake broker quoted one minute later: P(m) = price at m
    sip[TODAY.isoformat()] = [_bar(k, _price_path(open_dt + timedelta(minutes=k + 1)))
                              for k in range(390)]
    sip[TODAY.isoformat()][0] = _bar(0, 100.0)
    real_load = ix.load
    with tempfile.TemporaryDirectory() as tmp:
        nb.LEDGER, nb.DISARM = Path(tmp) / "ledger.csv", Path(tmp) / "x"
        # one pre-fix session (old close-row format) that Stage A must ignore
        nb._ledger({"day": "2026-10-30", "checkpoint": "close", "pos_before": -1, "pos_after": "?",
                    "orders": json.dumps([["expired", 690.6, "x"]])})
        clock = FakeClock(_at(dtime(9, 15)))
        nb.run(FakeBroker(clock, _price_path), clock, expected_account="PA1")
        ix.load = lambda symbols=ix.SYMBOLS: {"QQQ": sip}
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                nb.reconcile()
        finally:
            ix.load = real_load
    text = out.getvalue()
    assert "from 2026-11-02, 1 pre-fix session(s) ignored): 1 sessions" in text, text
    assert "full coverage 1/1, verified flat 1/1" in text, text
    assert "12/12 (100.0%)" in text, text
    # three entries and the 15:58 exit, each at the same-minute SIP price
    assert "mean +0.00 bp over 4 fills" in text, text
    assert "exit timing vs the shadow's close (not a cost): mean +0.00 bp over 1 exits" in text, text
    print("PASS test_reconcile_counts_stage_a_from_the_fix_and_scores_exits_same_minute")


def run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    run_all()
