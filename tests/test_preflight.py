"""Pre-flight refusal tests (v1.18).

Run with:  python tests/test_preflight.py
Synthetic only; never touches Alpaca.

The check that matters most is the credential/mode cross-check. Two instances
of the same code run from two directories against two accounts, and the way
that goes wrong is not subtle: live keys used with paper caps, or paper keys
in the directory you believe is live and therefore are not watching.
"""

from __future__ import annotations

import os
os.environ.setdefault("ORB_NO_FILE_LOG", "1")

import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orb_bot import preflight

LOG = logging.getLogger("test-preflight")


class FakeAccount:
    def __init__(self, **kw):
        self.status = kw.get("status", "ACTIVE")
        self.equity = kw.get("equity", 2000.0)
        self.trading_blocked = kw.get("trading_blocked", False)
        self.account_blocked = kw.get("account_blocked", False)
        self.transfers_blocked = kw.get("transfers_blocked", False)


class FakeBroker:
    def __init__(self, acct=None, raises=False):
        self.trading = self
        self._acct, self._raises = acct or FakeAccount(), raises

    def get_account(self):
        if self._raises:
            raise RuntimeError("network down")
        return self._acct


class Box:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def make_cfg(paper=True, notional=600, max_trades=3, shorts=False,
             loss=2.0, flatten=True, feed="sip"):
    return Box(
        credentials=Box(paper=paper),
        sizing=Box(max_position_notional=notional, risk_per_trade_pct=1.0),
        strategy=Box(max_trades_per_day=max_trades, allow_shorts=shorts),
        risk=Box(daily_max_loss_pct=loss, flatten_on_breaker=flatten),
        runtime=Box(data_feed=feed),
    )


def run_checks(cfg, broker, mode, confirmed=True):
    os.environ["ORB_MODE"] = mode
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "LIVE_CONFIRMED"
        if confirmed:
            f.write_text("yes")
        preflight.CONFIRM_FILE = f
        return preflight.check(cfg, broker, LOG)


def joined(problems):
    return " | ".join(problems).lower()


def test_clean_live_passes():
    p = run_checks(make_cfg(paper=False), FakeBroker(), "live")
    assert p == [], p
    print("PASS test_clean_live_passes")


def test_live_with_paper_keys_is_blocked():
    p = run_checks(make_cfg(paper=True), FakeBroker(), "live")
    assert any("paper keys" in x.lower() for x in p), p
    print("PASS test_live_with_paper_keys_is_blocked")


def test_paper_instance_with_live_keys_is_blocked():
    """The dangerous direction: real money traded from the instance whose
    numbers you read as 'just paper'."""
    p = run_checks(make_cfg(paper=False), FakeBroker(), "paper")
    assert any("live credentials" in x.lower() for x in p), p
    print("PASS test_paper_instance_with_live_keys_is_blocked")


def test_live_requires_confirmation_file():
    p = run_checks(make_cfg(paper=False), FakeBroker(), "live", confirmed=False)
    assert any("live_confirmed" in x.lower() for x in p), p
    print("PASS test_live_requires_confirmation_file")


def test_sizing_that_borrows_is_blocked():
    """3 x $700 = $2,100 against $2,000 equity is an intraday debit."""
    cfg = make_cfg(paper=False, notional=700, max_trades=3)
    p = run_checks(cfg, FakeBroker(FakeAccount(equity=2000.0)), "live")
    assert any("borrows intraday" in x.lower() for x in p), p
    # $600 fits.
    cfg = make_cfg(paper=False, notional=600, max_trades=3)
    p = run_checks(cfg, FakeBroker(FakeAccount(equity=2000.0)), "live")
    assert p == [], p
    print("PASS test_sizing_that_borrows_is_blocked")


def test_live_config_sanity():
    p = run_checks(make_cfg(paper=False, shorts=True), FakeBroker(), "live")
    assert any("allow_shorts" in x.lower() for x in p), p

    p = run_checks(make_cfg(paper=False, loss=5.0), FakeBroker(), "live")
    assert any("daily_max_loss_pct" in x.lower() for x in p), p

    p = run_checks(make_cfg(paper=False, flatten=False), FakeBroker(), "live")
    assert any("flatten_on_breaker" in x.lower() for x in p), p
    print("PASS test_live_config_sanity")


def test_blocked_or_unreadable_account():
    p = run_checks(make_cfg(paper=False),
                   FakeBroker(FakeAccount(trading_blocked=True)), "live")
    assert any("blocked" in x.lower() for x in p), p

    p = run_checks(make_cfg(paper=False),
                   FakeBroker(FakeAccount(status="ACCOUNT_UPDATED")), "live")
    assert any("not active" in joined(p) for _ in [0]), p

    p = run_checks(make_cfg(paper=False), FakeBroker(raises=True), "live")
    assert any("could not read the account" in x.lower() for x in p), p
    print("PASS test_blocked_or_unreadable_account")


def test_paper_side_stays_permissive():
    """Paper must not inherit the live limits: its job is to gather sample,
    and a paper instance refusing to trade is a lost session for nothing."""
    cfg = make_cfg(paper=True, notional=700, max_trades=10, loss=5.0, feed="iex")
    p = run_checks(cfg, FakeBroker(FakeAccount(equity=2000.0)), "paper")
    assert p == [], p
    print("PASS test_paper_side_stays_permissive")


def run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} checks passed.")


if __name__ == "__main__":
    run_all()
