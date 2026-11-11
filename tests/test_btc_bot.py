"""btc_bot.py and the survey rule it trades. No network, no keys.

    python -m pytest tests/test_btc_bot.py -q
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import btc_bot as bb  # noqa: E402
import survey  # noqa: E402

NOW = datetime(2026, 11, 12, 0, 10, tzinfo=timezone.utc)


def make_bars(n=600, end="2026-11-12", drift=0.002, seed=1) -> list[dict]:
    """n daily bars ending with the still-forming bar stamped `end`."""
    rng = np.random.default_rng(seed)
    px = 50_000 * np.exp(np.cumsum(drift + 0.02 * rng.standard_normal(n)))
    days = pd.date_range(end=end, periods=n, freq="D")
    return [{"t": d.strftime("%Y-%m-%dT00:00:00Z"), "c": float(p)} for d, p in zip(days, px)]


class FakeBroker:
    def __init__(self, bars, equity=10_000.0, qty=0.0, account="PA1", fill=True, price=None):
        self._bars, self.cash, self.qty, self._account = bars, equity, qty, account
        self.price = price or bars[-1]["c"]
        self.fill, self.sent, self.cancelled, self.other = fill, [], [], {}
        self.working = []

    def account(self):
        return self._account, self.cash + self.qty * self.price

    def positions(self):
        out = dict(self.other)
        if self.qty:
            out["BTCUSD"] = (self.qty, self.qty * self.price)
        return out

    def open_orders(self):
        return list(self.working)

    def market(self, side, notional, qty):
        self.sent.append((side, notional, qty))
        if self.fill:
            q = qty if qty is not None else notional / self.price
            self.qty += q if side == "buy" else -q
            self.cash += -q * self.price if side == "buy" else q * self.price
        return f"o{len(self.sent)}"

    def order(self, oid):
        return ("filled", self.price, 1.0) if self.fill else ("new", 0.0, 0.0)

    def cancel(self, oid):
        self.cancelled.append(oid)
        self.working = [w for w in self.working if w[0] != oid]

    def bars(self):
        return self._bars

    def last_price(self):
        return self.price


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(bb, "LEDGER", tmp_path / "ledger.csv")
    monkeypatch.setattr(bb, "DISARM", tmp_path / "DISARMED")
    monkeypatch.setattr(bb, "notify", lambda *a, **k: None)


# ---------------------------------------------------------------- the rule

def test_forming_bar_is_dropped():
    closes = bb.completed_closes(make_bars(), NOW)
    assert closes.index[-1] == pd.Timestamp("2026-11-11")
    assert len(closes) == 599


def test_history_checks():
    closes = bb.completed_closes(make_bars(), NOW)
    assert bb.check_history(closes, NOW) == ""
    assert "only" in bb.check_history(closes.tail(100), NOW)
    assert "expected" in bb.check_history(closes, NOW + timedelta(days=2))
    assert "missing" in bb.check_history(closes.drop(closes.index[-50]), NOW)


def test_target_is_long_only_and_capped():
    for seed in range(5):
        closes = bb.completed_closes(make_bars(seed=seed), NOW)
        tg = survey.donchian_targets(closes)
        assert tg.min() >= 0 and tg.max() <= 1


def test_target_zero_in_a_steady_decline_and_positive_in_a_rise():
    days = pd.date_range("2024-01-01", periods=700, freq="D")
    down = pd.Series(50_000 * np.exp(-0.003 * np.arange(700)), index=days)   # never a new high
    up = pd.Series(50_000 * np.exp(0.003 * np.arange(700)), index=days)      # always one
    assert survey.donchian_targets(down).iloc[-1] == 0
    assert survey.donchian_targets(up).iloc[-1] > 0.5


def test_target_uses_no_future_data():
    closes = bb.completed_closes(make_bars(), NOW)
    full = survey.donchian_targets(closes)
    cut = survey.donchian_targets(closes.iloc[:-30])
    assert np.allclose(full.iloc[:-30].to_numpy(), cut.to_numpy())


def test_decide_band_and_sides():
    assert bb.decide(0.30, 0.27, 10_000, 50_000, 0.054) is None          # inside the band
    assert bb.decide(0.30, 0.10, 10_000, 50_000, 0.02) == ("buy", 2000.0, None)
    side, notional, qty = bb.decide(0.10, 0.30, 10_000, 50_000, 0.06)
    assert side == "sell" and notional is None and qty == pytest.approx(0.04)
    assert bb.decide(0.0, 0.30, 10_000, 50_000, 0.06) == ("sell", None, 0.06)   # closes it all
    assert bb.decide(0.0, 0.04, 10_000, 50_000, 0.008) is None            # dust stays inside band


def test_threshold_matches_backtest_rule():
    """decide() and survey.apply_threshold must trade on the same days."""
    closes = bb.completed_closes(make_bars(seed=3), NOW)
    tg = survey.donchian_targets(closes)
    w, turn = survey.apply_threshold(tg, closes.pct_change().shift(-1), bb.BAND, False)
    cur, n_bot = 0.0, 0
    r = closes.pct_change().shift(-1).fillna(0.0).to_numpy()
    for i, t in enumerate(tg.to_numpy()):
        if bb.decide(float(t), cur, 1e9, 1.0, 1e9) is not None:
            cur, n_bot = float(t), n_bot + 1
        cur = cur * (1 + r[i]) / (1 + cur * r[i])
    assert n_bot == int((turn > 0).sum())


# ---------------------------------------------------------------- the runner

def test_refuses_wrong_account_and_sends_nothing():
    b = FakeBroker(make_bars(), account="PA_OTHER")
    row = bb.run_once(b, NOW, "PA1", sleep=lambda s: None)
    assert row["action"] == "refused" and b.sent == []


def test_refuses_unset_account():
    b = FakeBroker(make_bars())
    assert bb.run_once(b, NOW, "", sleep=lambda s: None)["action"] == "refused"
    assert b.sent == []


def test_refuses_foreign_position():
    b = FakeBroker(make_bars())
    b.other = {"QQQ": (10.0, 5000.0)}
    row = bb.run_once(b, NOW, "PA1", sleep=lambda s: None)
    assert row["action"] == "refused" and "QQQ" in row["status"] and b.sent == []


def test_refuses_foreign_open_order():
    b = FakeBroker(make_bars())
    b.working = [("x1", "QQQ")]
    row = bb.run_once(b, NOW, "PA1", sleep=lambda s: None)
    assert row["action"] == "refused" and b.sent == [] and b.cancelled == []


def test_refuses_when_disarmed():
    bb.DISARM.write_text("")
    b = FakeBroker(make_bars())
    assert bb.run_once(b, NOW, "PA1", sleep=lambda s: None)["action"] == "refused"
    assert b.sent == []


def test_refuses_stale_data():
    b = FakeBroker(make_bars(end="2026-11-09"))
    row = bb.run_once(b, NOW, "PA1", sleep=lambda s: None)
    assert row["action"] == "refused" and b.sent == []


def test_buys_to_target_from_flat_then_holds_then_skips_rerun():
    bars = make_bars(drift=0.004)
    b = FakeBroker(bars)
    target = bb.target_weight(bb.completed_closes(bars, NOW))
    assert target > bb.BAND
    row = bb.run_once(b, NOW, "PA1", sleep=lambda s: None)
    assert row["action"] == "trade" and row["side"] == "buy"
    assert b.sent == [("buy", round(target * 10_000, 2), None)]
    assert row["weight_after"] == pytest.approx(target, abs=0.01)
    again = bb.run_once(b, NOW + timedelta(minutes=5), "PA1", sleep=lambda s: None)
    assert again["action"] == "skipped" and len(b.sent) == 1


def test_sells_everything_when_target_is_zero():
    days = pd.date_range(end="2026-11-12", periods=700, freq="D")
    px = 50_000 * np.exp(-0.003 * np.arange(700))
    bars = [{"t": d.strftime("%Y-%m-%dT00:00:00Z"), "c": float(p)} for d, p in zip(days, px)]
    b = FakeBroker(bars, equity=5_000.0, qty=0.5)
    row = bb.run_once(b, NOW, "PA1", sleep=lambda s: None)
    assert row["action"] == "trade" and b.sent == [("sell", None, 0.5)] and b.qty == 0


def test_unfilled_order_is_cancelled_and_reported():
    b = FakeBroker(make_bars(drift=0.004), fill=False)
    row = bb.run_once(b, NOW, "PA1", sleep=lambda s: None)
    assert row["action"] == "trade" and row["status"] != "filled" and b.cancelled == ["o1"]


def test_never_targets_more_than_equity():
    b = FakeBroker(make_bars(drift=0.01, seed=2))
    bb.run_once(b, NOW, "PA1", sleep=lambda s: None)
    for side, notional, _ in b.sent:
        assert side == "buy" and notional <= 10_000
