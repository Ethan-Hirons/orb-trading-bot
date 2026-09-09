"""Unit + simulation tests for the ORB bot's pure logic.

Run with:  python tests/test_logic.py
Uses synthetic data only; does NOT call Alpaca or need API keys.
"""

from __future__ import annotations

import os
os.environ.setdefault("ORB_NO_FILE_LOG", "1")  # keep tests out of the live day log

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orb_bot.config import (
    Config, Credentials, ExitsConfig, NewsConfig, RiskConfig, RuntimeConfig,
    ScreenerConfig, SelectionConfig, SizingConfig, StrategyConfig,
)
from orb_bot.news import NewsSignal, score_text, should_exit_on_news
from orb_bot.report import compute_stats, pair_fills, pair_fills_all
from orb_bot.risk import BreakerStatus, RiskManager
from orb_bot.screener import Candidate
from orb_bot.selection import build_targets, cap_per_underlying, underlying_key
from orb_bot.strategy import (
    build_trade_plan, check_breakout, compute_opening_range, confirm_breakout,
    find_confirm_bar, passes_volume_filter, scale_take_profit_pct,
)


@dataclass
class FakeBar:
    high: float
    low: float
    open: float = 0.0
    close: float = 0.0
    volume: float = 1000.0


def _exits(**kw):
    base = dict(mode="percent", stop_pct=3.0, tp_min_pct=3.0, tp_max_pct=5.0,
               vol_ref_low_pct=0.5, vol_ref_high_pct=2.0)
    base.update(kw)
    return ExitsConfig(**base)


def _sizing(**kw):
    base = dict(risk_per_trade_pct=1.0, max_position_notional=0.0, whole_shares_only=True)
    base.update(kw)
    return SizingConfig(**base)


def _dummy_config(**over):
    cfg = Config(
        fallback_symbols=["SPY", "QQQ"],
        screener=ScreenerConfig(),
        news=NewsConfig(),
        selection=SelectionConfig(max_targets=3),
        strategy=StrategyConfig(),
        exits=_exits(),
        sizing=_sizing(),
        risk=RiskConfig(),
        runtime=RuntimeConfig(),
        credentials=Credentials("k", "s", True),
    )
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def test_opening_range_and_breakout():
    bars = [FakeBar(high=101, low=99), FakeBar(high=102, low=98)]
    rng = compute_opening_range("SPY", bars)
    assert rng.high == 102 and rng.low == 98 and rng.size == 4
    s = StrategyConfig(allow_shorts=True)
    assert check_breakout(rng, 103, s) == "long"
    assert check_breakout(rng, 97, s) == "short"
    assert check_breakout(rng, 100, s) is None
    assert check_breakout(rng, 97, StrategyConfig(allow_shorts=False)) is None
    print("PASS test_opening_range_and_breakout")


def test_scale_take_profit():
    e = _exits()
    assert abs(scale_take_profit_pct(0.5, e) - 3.0) < 1e-9   # at/below low -> min
    assert abs(scale_take_profit_pct(2.0, e) - 5.0) < 1e-9   # at/above high -> max
    assert abs(scale_take_profit_pct(1.25, e) - 4.0) < 1e-9  # midpoint -> 4%
    assert abs(scale_take_profit_pct(0.1, e) - 3.0) < 1e-9   # clamp low
    assert abs(scale_take_profit_pct(9.0, e) - 5.0) < 1e-9   # clamp high
    print("PASS test_scale_take_profit")


def test_percent_exit_long():
    bars = [FakeBar(high=102, low=98)]
    rng = compute_opening_range("SPY", bars)
    price = 102.5
    plan = build_trade_plan(rng, "long", price, 2000.0, _exits(), _sizing())
    assert plan is not None
    # 3% stop below entry (prices are rounded to whole cents for real orders)
    assert abs(plan.stop_pct - 3.0) < 1e-6
    assert abs(plan.stop_loss - round(price * 0.97, 2)) < 1e-9
    # TP between 3% and 5% above entry
    assert 3.0 <= plan.tp_pct <= 5.0
    assert plan.take_profit > price > plan.stop_loss
    # risk-based size: risk $20, per-share risk = 102.5*3% = 3.075 -> floor(6.5)=6
    assert plan.qty == 6
    print("PASS test_percent_exit_long")


def test_percent_exit_short():
    bars = [FakeBar(high=102, low=98)]
    rng = compute_opening_range("SPY", bars)
    price = 97.5
    plan = build_trade_plan(rng, "short", price, 2000.0, _exits(), _sizing())
    assert plan is not None
    assert abs(plan.stop_pct - 3.0) < 1e-6
    assert abs(plan.stop_loss - round(price * 1.03, 2)) < 1e-9
    assert plan.take_profit < price < plan.stop_loss
    print("PASS test_percent_exit_short")


def test_notional_cap():
    bars = [FakeBar(high=102, low=98)]
    rng = compute_opening_range("SPY", bars)
    plan = build_trade_plan(
        rng, "long", 100.0, 100000.0, _exits(),
        _sizing(risk_per_trade_pct=50.0, max_position_notional=300.0),
    )
    assert plan is not None and plan.qty == 3  # 300/100
    print("PASS test_notional_cap")


def test_range_mode():
    bars = [FakeBar(high=102, low=98)]
    rng = compute_opening_range("SPY", bars)
    e = _exits(mode="range", take_profit_r=1.0, stop_buffer_r=0.10)
    plan = build_trade_plan(rng, "long", 102.5, 2000.0, e, _sizing())
    assert plan is not None
    assert abs(plan.stop_loss - 97.6) < 1e-6   # 98 - 0.4
    assert abs(plan.take_profit - 106.5) < 1e-6  # 102.5 + 4
    print("PASS test_range_mode")


def test_risk_breakers():
    r = RiskManager(RiskConfig(daily_profit_target_pct=6.0, daily_max_loss_pct=5.0), 2000.0)
    assert r.assess(2100.0).status == BreakerStatus.OK
    assert r.assess(2121.0).status == BreakerStatus.PROFIT_TARGET   # +6.05%
    assert r.assess(1899.0).status == BreakerStatus.MAX_LOSS        # -5.05%
    print("PASS test_risk_breakers")


def test_sentiment_scoring():
    assert score_text("Company beats earnings, raises guidance, shares surge") > 0.3
    assert score_text("Company misses, downgrade and lawsuit, shares plunge") < -0.3
    assert abs(score_text("The company held a meeting today")) < 1e-9
    print("PASS test_sentiment_scoring")


def test_selection_bias_and_ranking():
    cfg = _dummy_config()
    candidates = [
        Candidate("AAA", price=50, prev_close=45, gap_pct=11.0, rel_volume=1.0, source="mover"),
        Candidate("BBB", price=20, prev_close=21, gap_pct=-4.0, rel_volume=0.5, source="mover"),
        Candidate("CCC", price=80, prev_close=79.5, gap_pct=0.6, rel_volume=0.2, source="active"),
    ]
    news = {
        "AAA": NewsSignal("AAA", 0.8, 5, "AAA beats and surges"),      # strong +
        "BBB": NewsSignal("BBB", -0.6, 3, "BBB downgrade"),            # strong -
        "CCC": NewsSignal("CCC", 0.0, 0, ""),                          # none
    }
    targets = build_targets(cfg, candidates, news)
    by = {t.symbol: t for t in targets}
    assert by["AAA"].bias == "long"
    assert by["BBB"].bias == "short"
    assert by["CCC"].bias == "any"       # weak gap, no news
    # AAA should rank first (big gap + strong sentiment + high volume).
    assert targets[0].symbol == "AAA"
    print("PASS test_selection_bias_and_ranking")


def test_dead_slot_short_bias_when_shorts_off():
    """v1.9: shorts off + respect_news_bias on => short-biased names can never
    trade, so they must not occupy target slots."""
    candidates = [
        Candidate("AAA", price=50, prev_close=45, gap_pct=11.0, rel_volume=1.0, source="mover"),
        Candidate("BBB", price=20, prev_close=21, gap_pct=-4.0, rel_volume=0.5, source="mover"),
        Candidate("CCC", price=80, prev_close=79.5, gap_pct=0.6, rel_volume=0.2, source="active"),
    ]
    news = {
        "AAA": NewsSignal("AAA", 0.8, 5, "AAA beats and surges"),
        "BBB": NewsSignal("BBB", -0.6, 3, "BBB downgrade"),   # short bias
        "CCC": NewsSignal("CCC", 0.0, 0, ""),
    }
    cfg = _dummy_config()
    cfg.strategy.allow_shorts = False
    syms = {t.symbol for t in build_targets(cfg, candidates, news)}
    assert "BBB" not in syms and {"AAA", "CCC"} <= syms  # slot freed

    # respect_news_bias off: BBB could still trade long -> keep it.
    cfg.strategy.respect_news_bias = False
    syms = {t.symbol for t in build_targets(cfg, candidates, news)}
    assert "BBB" in syms

    # Shorts on: behavior unchanged.
    cfg.strategy.respect_news_bias = True
    cfg.strategy.allow_shorts = True
    syms = {t.symbol for t in build_targets(cfg, candidates, news)}
    assert "BBB" in syms
    print("PASS test_dead_slot_short_bias_when_shorts_off")


def test_require_news_filter():
    cfg = _dummy_config()
    cfg.news = NewsConfig(require_news=True)
    candidates = [
        Candidate("AAA", price=50, prev_close=45, gap_pct=11.0, rel_volume=1.0, source="mover"),
        Candidate("ZZZ", price=30, prev_close=28, gap_pct=7.0, rel_volume=0.9, source="mover"),
    ]
    news = {"AAA": NewsSignal("AAA", 0.5, 4, "news"), "ZZZ": NewsSignal("ZZZ", 0.0, 0, "")}
    targets = build_targets(cfg, candidates, news)
    syms = {t.symbol for t in targets}
    assert "AAA" in syms and "ZZZ" not in syms  # ZZZ dropped (no news)
    print("PASS test_require_news_filter")


def test_min_articles_filter():
    cfg = _dummy_config()
    cfg.news = NewsConfig(min_articles=3)
    candidates = [
        Candidate("AAA", price=50, prev_close=45, gap_pct=11.0, rel_volume=1.0, source="mover"),
        Candidate("BBB", price=30, prev_close=28, gap_pct=7.0, rel_volume=0.9, source="mover"),
    ]
    news = {"AAA": NewsSignal("AAA", 0.5, 4, "news"), "BBB": NewsSignal("BBB", 0.4, 2, "news")}
    syms = {t.symbol for t in build_targets(cfg, candidates, news)}
    assert "AAA" in syms and "BBB" not in syms  # BBB dropped (only 2 articles)
    print("PASS test_min_articles_filter")


def test_stop_limit_plan():
    bars = [FakeBar(high=102, low=98)]
    rng = compute_opening_range("SPY", bars)
    e = _exits(stop_limit=True, stop_limit_offset_pct=0.5)
    plan = build_trade_plan(rng, "long", 100.0, 2000.0, e, _sizing())
    assert plan is not None and plan.stop_limit is not None
    # long: stop 97.00, limit 0.5% below -> 96.52 (sell no lower than this)
    assert abs(plan.stop_loss - 97.0) < 1e-9
    assert abs(plan.stop_limit - round(97.0 * 0.995, 2)) < 1e-9
    plan_s = build_trade_plan(rng, "short", 97.0, 2000.0, e, _sizing())
    assert plan_s.stop_limit > plan_s.stop_loss  # short: buy-back cap above stop
    # disabled -> no stop-limit price
    plan_off = build_trade_plan(
        rng, "long", 100.0, 2000.0, _exits(stop_limit=False), _sizing()
    )
    assert plan_off.stop_limit is None
    print("PASS test_stop_limit_plan")


def test_adverse_news_exit_decision():
    cfg = NewsConfig(exit_on_adverse=True, adverse_exit_score=0.5)
    assert should_exit_on_news("long", -0.6, cfg) is True
    assert should_exit_on_news("long", -0.4, cfg) is False   # not strong enough
    assert should_exit_on_news("long", 0.8, cfg) is False    # good news, keep
    assert should_exit_on_news("short", 0.6, cfg) is True    # squeeze risk
    assert should_exit_on_news("short", -0.9, cfg) is False
    off = NewsConfig(exit_on_adverse=False, adverse_exit_score=0.5)
    assert should_exit_on_news("long", -1.0, off) is False
    print("PASS test_adverse_news_exit_decision")


def test_strong_lexicon_weighting():
    # "bankruptcy"/"fraud" are strong (weight 2) and should dominate.
    assert score_text("Company wins award but faces bankruptcy and fraud probe") < 0
    assert score_text("FDA approval, merger announced") > 0.5
    print("PASS test_strong_lexicon_weighting")


def test_pair_fills_round_trips():
    fills = [
        # long AAA: buy 10 @ 100, sell 10 @ 103 -> +30
        {"symbol": "AAA", "side": "buy", "qty": 10, "price": 100.0, "time": 1},
        # short BBB: sell 5 @ 50, buy 5 @ 51 -> -5
        {"symbol": "BBB", "side": "sell", "qty": 5, "price": 50.0, "time": 2},
        {"symbol": "AAA", "side": "sell", "qty": 10, "price": 103.0, "time": 3},
        {"symbol": "BBB", "side": "buy", "qty": 5, "price": 51.0, "time": 4},
        # CCC still open: no round trip recorded
        {"symbol": "CCC", "side": "buy", "qty": 7, "price": 20.0, "time": 5},
    ]
    trades = {t.symbol: t for t in pair_fills(fills, "2026-07-01")}
    assert set(trades) == {"AAA", "BBB"}
    assert trades["AAA"].side == "long" and abs(trades["AAA"].pnl - 30.0) < 1e-9
    assert trades["BBB"].side == "short" and abs(trades["BBB"].pnl - (-5.0)) < 1e-9
    assert abs(trades["AAA"].pnl_pct - 3.0) < 1e-6
    print("PASS test_pair_fills_round_trips")


def test_pair_fills_partial_exits():
    # long 10, exit in two halves at different prices
    fills = [
        {"symbol": "DDD", "side": "buy", "qty": 10, "price": 100.0, "time": 1},
        {"symbol": "DDD", "side": "sell", "qty": 5, "price": 104.0, "time": 2},
        {"symbol": "DDD", "side": "sell", "qty": 5, "price": 102.0, "time": 3},
    ]
    trades = pair_fills(fills, "2026-07-01")
    assert len(trades) == 1
    t = trades[0]
    assert abs(t.exit_price - 103.0) < 1e-9  # blended exit
    assert abs(t.pnl - 30.0) < 1e-9
    print("PASS test_pair_fills_partial_exits")


def test_pair_fills_all():
    from datetime import datetime, timezone

    def ts(day, hour, minute=0):
        # 14:00-20:00 UTC = 10:00-16:00 ET in July (EDT)
        return datetime(2026, 7, day, hour, minute, tzinfo=timezone.utc)

    fills = [
        # Day 1: long AAA +30 (normal same-day round trip)
        {"symbol": "AAA", "side": "buy", "qty": 10, "price": 100.0, "time": ts(14, 14)},
        {"symbol": "AAA", "side": "sell", "qty": 10, "price": 103.0, "time": ts(14, 19)},
        # Cross-day: short BBB opened day 1, covered day 2 (the INTC
        # 2026-07-16/17 case: a lost EOD close carried the position overnight;
        # per-day grouping could never pair this).
        {"symbol": "BBB", "side": "sell", "qty": 7, "price": 98.0, "time": ts(14, 15)},
        {"symbol": "BBB", "side": "buy", "qty": 7, "price": 97.0, "time": ts(15, 14)},
        # No timestamp: skipped, must not crash
        {"symbol": "EEE", "side": "buy", "qty": 1, "price": 5.0, "time": None},
    ]
    trades = pair_fills_all(fills)
    assert len(trades) == 2
    by = {t.symbol: t for t in trades}
    assert by["AAA"].date == "2026-07-14" and abs(by["AAA"].pnl - 30.0) < 1e-9
    # Cross-day trip is dated by its CLOSING fill.
    assert by["BBB"].date == "2026-07-15" and by["BBB"].side == "short"
    assert abs(by["BBB"].pnl - 7.0) < 1e-9
    print("PASS test_pair_fills_all")


def test_confirm_lookback():
    # Range high = 102. Breakout bar closed at 102.5 two bars ago, then a
    # pullback bar closed back inside. With lookback 1 (old behaviour) the
    # breakout is missed; with lookback 3 it confirms on the earlier bar.
    rng = compute_opening_range("SPY", [FakeBar(high=102, low=98)])
    bars = [
        FakeBar(high=101, low=100, close=100.5, volume=500),
        FakeBar(high=103, low=101, close=102.5, volume=900),   # confirming bar
        FakeBar(high=102.6, low=101.5, close=101.8, volume=300),  # pullback
    ]
    strict = StrategyConfig(confirm_bar_close=True, confirm_lookback_bars=1)
    loose = StrategyConfig(confirm_bar_close=True, confirm_lookback_bars=3)
    assert find_confirm_bar(rng, "long", bars, strict) is None
    got = find_confirm_bar(rng, "long", bars, loose)
    assert got is not None and got.close == 102.5  # picks the confirming bar
    assert confirm_breakout(rng, "long", bars, loose) is True
    # Confirmation off -> last bar returned so the volume filter has input.
    off = StrategyConfig(confirm_bar_close=False)
    assert find_confirm_bar(rng, "long", bars, off).close == 101.8
    # Short side: no bar closed below 98.
    assert find_confirm_bar(rng, "short", bars, loose) is None
    assert find_confirm_bar(rng, "long", [], loose) is None
    print("PASS test_confirm_lookback")


def test_volume_filter_ratio():
    # OR bars average 1000 volume; the breakout bar prints 600.
    or_bars = [FakeBar(high=102, low=98, volume=1500), FakeBar(high=101, low=99, volume=500)]
    bar = FakeBar(high=103, low=102, close=102.8, volume=600)
    full = StrategyConfig(require_volume_filter=True, volume_filter_ratio=1.0)
    half = StrategyConfig(require_volume_filter=True, volume_filter_ratio=0.5)
    assert passes_volume_filter(bar, or_bars, full) is False  # 600 < 1000
    assert passes_volume_filter(bar, or_bars, half) is True   # 600 >= 500
    off = StrategyConfig(require_volume_filter=False)
    assert passes_volume_filter(bar, or_bars, off) is True
    assert passes_volume_filter(None, or_bars, half) is True  # no bar -> pass
    print("PASS test_volume_filter_ratio")


def test_state_entry_times_roundtrip():
    from orb_bot.state import DailyState

    s = DailyState(trade_date="2026-07-17", armed=True)
    assert s.entry_times == {}  # default present for old state files
    s.entry_times["ATAI"] = "2026-07-17T14:31:36+00:00"
    # Simulate reload from JSON (what load_state does).
    import dataclasses, json
    s2 = DailyState(**json.loads(json.dumps(dataclasses.asdict(s))))
    assert s2.entry_times == {"ATAI": "2026-07-17T14:31:36+00:00"}
    print("PASS test_state_entry_times_roundtrip")


def test_compute_stats():
    pnls = [10.0, -5.0, 20.0, -10.0, 15.0]
    pcts = [1.0, -0.5, 2.0, -1.0, 1.5]
    s = compute_stats(pnls, pcts)
    assert s.n_trades == 5 and s.n_wins == 3
    assert abs(s.win_rate - 60.0) < 1e-9
    assert abs(s.expectancy - 6.0) < 1e-9
    assert abs(s.total_pnl - 30.0) < 1e-9
    assert abs(s.max_drawdown - 10.0) < 1e-9  # 30 -> 20 dip? curve: 10,5,25,15,30
    empty = compute_stats([], [])
    assert empty.n_trades == 0 and empty.expectancy == 0.0
    print("PASS test_compute_stats")


def test_screener_gap_cap_config():
    sc = ScreenerConfig(max_abs_gap_pct=75.0)
    assert sc.max_abs_gap_pct == 75.0
    assert ScreenerConfig().max_abs_gap_pct == 75.0  # default on
    print("PASS test_screener_gap_cap_config")


def test_underlying_key():
    # Leveraged single-stock ETPs -> ticker named in the asset title.
    assert underlying_key("SMCX", "Defiance Daily Target 2X Long SMCI ETF") == "SMCI"
    assert underlying_key("SMCL", "GraniteShares 2x Long SMCI Daily ETF") == "SMCI"
    assert underlying_key("SNXX", "T-Rex 2X Long SMCI Daily Target ETF") == "SMCI"
    assert underlying_key("TQQQ", "ProShares UltraPro QQQ") == "QQQ"
    assert underlying_key("SQQQ", "ProShares UltraPro Short QQQ") == "QQQ"
    # Common stocks / no leverage marker / no name -> own key (no grouping).
    assert underlying_key("SMCI", "Super Micro Computer, Inc. Common Stock") == "SMCI"
    assert underlying_key("AAPL", "Apple Inc. Common Stock") == "AAPL"
    assert underlying_key("XYZ", None) == "XYZ"
    # Index leveraged fund with no ticker in the name -> own key.
    assert underlying_key("SOXS", "Direxion Daily Semiconductor Bear 3X Shares") == "SOXS"
    print("PASS test_underlying_key")


def test_same_underlying_cap():
    cfg = _dummy_config()
    cfg.selection.max_targets = 5
    cfg.selection.max_per_underlying = 1
    names = {
        "SMCX": "Defiance Daily Target 2X Long SMCI ETF",
        "SMCL": "GraniteShares 2x Long SMCI Daily ETF",
        "SNXX": "T-Rex 2X Long SMCI Daily Target ETF",
        "SKYQ": "Sky Quarry Inc. Common Stock",
    }
    candidates = [
        Candidate("SMCX", price=10, prev_close=9, gap_pct=11.0, rel_volume=1.0, source="mover"),
        Candidate("SMCL", price=13, prev_close=12, gap_pct=8.0, rel_volume=0.8, source="mover"),
        Candidate("SNXX", price=20, prev_close=19, gap_pct=5.0, rel_volume=0.5, source="mover"),
        Candidate("SKYQ", price=6, prev_close=5.5, gap_pct=9.0, rel_volume=0.6, source="mover"),
    ]
    news = {s: NewsSignal(s, 0.6, 3, "up") for s in names}
    targets = build_targets(cfg, candidates, news, asset_names=names)
    syms = [t.symbol for t in targets]
    # Only ONE of the three SMCI trackers survives (the highest-scored), SKYQ kept.
    assert sum(s in {"SMCX", "SMCL", "SNXX"} for s in syms) == 1
    assert "SKYQ" in syms
    # Strongest tracker (biggest gap+volume) is the survivor.
    assert "SMCX" in syms

    # Rescreen path: an underlying already targeted/traded blocks new twins.
    t2 = build_targets(
        cfg, candidates, news, asset_names=names, taken_underlyings={"SMCI"}
    )
    assert all(s not in {"SMCX", "SMCL", "SNXX"} for s in (t.symbol for t in t2))
    assert [t.symbol for t in t2] == ["SKYQ"]

    # cap=0 disables; missing names degrade to no grouping.
    assert len(cap_per_underlying(targets, {}, 0)) == len(targets)
    t3 = build_targets(cfg, candidates, news, asset_names=None)
    assert len(t3) == 4
    print("PASS test_same_underlying_cap")


def test_trailing_stop():
    """v1.11 _check_trailing: one-shot handoff to a native server-side
    trailing stop once the peak is far enough past breakeven."""
    import logging

    from orb_bot.runner import Runner

    class FakePos:
        # v1.15: cur_pct is the LIVE unrealized P&L %, which is what Alpaca
        # anchors the trailing high-water mark on. Defaults to 0 (at entry).
        def __init__(self, symbol, entry, side, qty=10, cur_pct=0.0):
            self.symbol, self.avg_entry_price, self.side = symbol, entry, side
            self.qty = qty
            self.unrealized_plpc = cur_pct / 100.0

    class FakeBroker:
        def __init__(self, positions, ok=True):
            self.positions, self.activations, self.ok = positions, [], ok

        def list_positions(self):
            return self.positions

        def activate_trailing_stop(self, symbol, qty, side, trail_percent,
                                   fallback_stop=None, fallback_limit=None):
            self.activations.append(
                (symbol, qty, side, trail_percent,
                 None if fallback_stop is None else round(fallback_stop, 4),
                 None if fallback_limit is None else round(fallback_limit, 4))
            )
            return self.ok

    def make_runner(trail, positions, excursion, ok=True):
        r = Runner.__new__(Runner)
        r.cfg = _dummy_config()
        r.cfg.exits.trail_pct = trail
        r.broker = FakeBroker(positions, ok=ok)
        r._excursion = excursion
        r._trail_stops = {}
        r._trail_active = set()
        r._trail_deferred = set()
        r.log = logging.getLogger("test-trail")
        return r

    # Gated off: trail_pct=0 must be a no-op even with a huge peak.
    r = make_runner(0.0, [FakePos("AAA", 100.0, "long")],
                    {"AAA": {"mfe": 5, "mae": 0, "mfe_pct": 5.0, "mae_pct": 0}})
    r._check_trailing()
    assert r.broker.activations == []

    # Long, peak +4% AND price still there, trail 1.5: trailed level
    # 104*(1-1.5%) = 102.44 > entry -> hand off to the native trailing stop.
    r = make_runner(1.5, [FakePos("AAA", 100.0, "long", cur_pct=4.0)],
                    {"AAA": {"mfe": 4, "mae": 0, "mfe_pct": 4.0, "mae_pct": 0}})
    r._check_trailing()
    assert len(r.broker.activations) == 1
    sym, qty, side, trail, fb_stop, fb_lim = r.broker.activations[0]
    assert sym == "AAA" and qty == 10 and side == "long" and trail == 1.5
    # Fallback = the original initial stop (protection restored on failure).
    assert fb_stop is not None and abs(fb_stop - 100.0 * (1 - r.cfg.exits.stop_pct / 100)) < 0.01
    assert fb_lim is not None and fb_lim < fb_stop  # stop-limit floor below
    assert "AAA" in r._trail_active
    # Already handed off: no re-activation (no API spam).
    r._check_trailing()
    assert len(r.broker.activations) == 1
    # Position disappears -> trail fill detected, armed set cleaned up.
    r.broker.positions = []
    r._check_trailing()
    assert "AAA" not in r._trail_active

    # Early peak (+1%): trailed level would be below entry -> leave alone
    # (initial stop governs; arming waits for breakeven).
    r = make_runner(1.5, [FakePos("BBB", 50.0, "long", cur_pct=1.0)],
                    {"BBB": {"mfe": 0.5, "mae": 0, "mfe_pct": 1.0, "mae_pct": 0}})
    r._check_trailing()
    assert r.broker.activations == []

    # v1.15 REGRESSION (2026-09-09 IRD): peak +3.22% came from a bar high the
    # 30s poll missed, and by the time the trail could arm price was back to
    # +0.52%. Alpaca anchors the high-water mark HERE, not at the peak, so
    # arming would hand over a trail locking -0.74% -- worse than the -1.5%
    # bracket stop it replaces, and the old code did exactly that while logging
    # "would lock ~+1.93%". Must NOT arm, and must say why exactly once.
    r = make_runner(1.25, [FakePos("IRD", 6.83, "long", qty=102, cur_pct=0.52)],
                    {"IRD": {"mfe": 22.44, "mae": -56.10,
                             "mfe_pct": 3.22, "mae_pct": -8.05}})
    r._check_trailing()
    assert r.broker.activations == []
    assert "IRD" in r._trail_deferred
    r._check_trailing()  # dedup: still no arm, no second log
    assert r.broker.activations == []
    # Same peak, but price actually holds up there -> arms normally.
    r = make_runner(1.25, [FakePos("IRD", 6.83, "long", qty=102, cur_pct=3.22)],
                    {"IRD": {"mfe": 22.44, "mae": -56.10,
                             "mfe_pct": 3.22, "mae_pct": -8.05}})
    r._check_trailing()
    assert len(r.broker.activations) == 1
    assert "IRD" in r._trail_active and "IRD" not in r._trail_deferred

    # Short mirror, peak +4% (price fell 4%) and still there: trailed level
    # 96*(1+1.5%) = 97.44 < entry -> arms; fallback stop above entry.
    r = make_runner(1.5, [FakePos("CCC", 100.0, "short", cur_pct=4.0)],
                    {"CCC": {"mfe": 4, "mae": 0, "mfe_pct": 4.0, "mae_pct": 0}})
    r._check_trailing()
    (sym, qty, side, trail, fb_stop, fb_lim), = r.broker.activations
    assert sym == "CCC" and side == "short"
    assert fb_stop is not None and fb_stop > 100.0
    assert fb_lim is not None and fb_lim > fb_stop

    # Negative excursion: nothing to trail.
    r = make_runner(1.5, [FakePos("DDD", 10.0, "long")],
                    {"DDD": {"mfe": -1, "mae": -2, "mfe_pct": -1.0, "mae_pct": -2.0}})
    r._check_trailing()
    assert r.broker.activations == []

    # Handoff failure: NOT marked active (so it retries next loop).
    r = make_runner(1.5, [FakePos("EEE", 100.0, "long", cur_pct=4.0)],
                    {"EEE": {"mfe": 4, "mae": 0, "mfe_pct": 4.0, "mae_pct": 0}},
                    ok=False)
    r._check_trailing()
    assert len(r.broker.activations) == 1 and "EEE" not in r._trail_active
    print("PASS test_trailing_stop")


def test_profit_breaker_disabled_by_zero():
    """v1.12: daily_profit_target_pct 0 must DISABLE the profit breaker, not
    fire it the instant the day ticks green. The loss breaker must survive."""
    r = RiskManager(RiskConfig(daily_profit_target_pct=0, daily_max_loss_pct=5.0),
                    2000.0)
    assert r.assess(2000.0).status == BreakerStatus.OK
    assert r.assess(2001.0).status == BreakerStatus.OK      # +0.05%, was a trap
    assert r.assess(2200.0).status == BreakerStatus.OK      # +10%: let it run
    assert r.assess(1899.0).status == BreakerStatus.MAX_LOSS
    # A positive target still works, so the flip is reversible.
    r2 = RiskManager(RiskConfig(daily_profit_target_pct=6.0,
                                daily_max_loss_pct=5.0), 2000.0)
    assert r2.assess(2121.0).status == BreakerStatus.PROFIT_TARGET
    print("PASS test_profit_breaker_disabled_by_zero")


def test_opening_rel_volume_gate():
    """v1.12: the relative-volume gate blocks a quiet open, passes a busy one,
    and — critically — does NOT block when history is unavailable. Skipping
    every unmeasurable name would gut the universe the way the v1.11.1
    liquidity floor did on 2026-08-11 (44/46 candidates rejected)."""
    def gated(rel_vol, floor):
        # Mirrors the runner's condition exactly.
        return bool(floor > 0 and rel_vol is not None and rel_vol < floor)

    assert gated(0.4, 1.0) is True       # quiet open, gate on -> skip
    assert gated(2.5, 1.0) is False      # busy open -> trade
    assert gated(None, 1.0) is False     # unknown history -> trade, not skip
    assert gated(0.01, 0.0) is False     # gate off -> never blocks
    print("PASS test_opening_rel_volume_gate")


def test_strategy_config_relvol_defaults_off():
    """The new filter must default to measure-only so live behavior is
    unchanged until it has been swept."""
    s = StrategyConfig()
    assert s.min_opening_rel_volume == 0.0
    assert s.rel_volume_lookback_days == 14
    print("PASS test_strategy_config_relvol_defaults_off")


def test_excursion_from_minute_bars():
    """v1.13: minute-bar highs/lows must lift a peak the 30s poll missed.

    This is the bug that mattered: `_check_trailing` arms off `mfe_pct`, so
    a spike the poll slept through was a trail that never armed."""
    import logging
    from datetime import datetime, timezone

    from orb_bot.runner import Runner

    class FakePos:
        def __init__(self, symbol, entry, side, qty=10):
            self.symbol, self.avg_entry_price, self.side = symbol, entry, side
            self.qty = qty

    class FakeBroker:
        def __init__(self, bars, positions):
            self.bars, self.positions, self.calls = bars, positions, []

        def list_positions(self):
            return self.positions

        def get_minute_bars(self, symbol, start, end):
            self.calls.append(symbol)
            return self.bars.get(symbol, [])

    def make_runner(bars, positions, polled, interval=60):
        r = Runner.__new__(Runner)
        r.cfg = _dummy_config()
        r.cfg.runtime.excursion_bar_seconds = interval
        r.broker = FakeBroker(bars, positions)
        r._excursion = dict(polled)
        r._last_bar_excursion = 0.0
        r._bar_scan_from = {}
        r.log = logging.getLogger("test-exc")
        return r

    # Long entered at 100. Poll only ever saw +1.0%; a bar high of 112 means
    # the true peak was +12%. The refined peak must win.
    pos = [FakePos("AAA", 100.0, "long")]
    bars = {"AAA": [FakeBar(high=112.0, low=99.0), FakeBar(high=104.0, low=98.0)]}
    r = make_runner(bars, pos, {"AAA": {"mfe": 10, "mae": -1,
                                        "mfe_pct": 1.0, "mae_pct": -1.0}})
    r._refine_excursion_from_bars(pos)
    e = r._excursion["AAA"]
    assert abs(e["mfe_pct"] - 12.0) < 1e-6, e
    assert abs(e["mfe"] - 120.0) < 1e-6, e     # (112-100)*10
    assert abs(e["mae_pct"] - (-2.0)) < 1e-6, e  # low 98 -> -2%
    assert abs(e["mae"] - (-20.0)) < 1e-6, e

    # A polled peak BETTER than the bars must not be clobbered downward.
    r = make_runner(bars, pos, {"AAA": {"mfe": 200, "mae": -1,
                                        "mfe_pct": 20.0, "mae_pct": -0.5}})
    r._refine_excursion_from_bars(pos)
    assert r._excursion["AAA"]["mfe_pct"] == 20.0

    # Short mirror: favorable extreme is the LOW, adverse is the HIGH.
    spos = [FakePos("CCC", 100.0, "short")]
    sbars = {"CCC": [FakeBar(high=103.0, low=94.0)]}
    r = make_runner(sbars, spos, {"CCC": {"mfe": 0, "mae": 0,
                                          "mfe_pct": 0.0, "mae_pct": 0.0}})
    r._refine_excursion_from_bars(spos)
    e = r._excursion["CCC"]
    assert abs(e["mfe_pct"] - 6.0) < 1e-6, e   # fell to 94 = +6% for a short
    assert abs(e["mae_pct"] - (-3.0)) < 1e-6, e

    # Throttle: a second call inside the interval must not re-fetch.
    r = make_runner(bars, pos, {"AAA": {"mfe": 0, "mae": 0,
                                        "mfe_pct": 0.0, "mae_pct": 0.0}})
    r._refine_excursion_from_bars(pos)
    n = len(r.broker.calls)
    r._refine_excursion_from_bars(pos)
    assert len(r.broker.calls) == n, "throttle failed — refetched inside interval"

    # Gated off (0) = pre-v1.13 behaviour, no bar calls at all.
    r = make_runner(bars, pos, {"AAA": {"mfe": 0, "mae": 0,
                                        "mfe_pct": 0.0, "mae_pct": 0.0}},
                    interval=0)
    r._refine_excursion_from_bars(pos)
    assert r.broker.calls == []
    assert r._excursion["AAA"]["mfe_pct"] == 0.0

    # A broker failure must leave the polled numbers untouched, never raise.
    class BoomBroker(FakeBroker):
        def get_minute_bars(self, symbol, start, end):
            raise RuntimeError("data feed down")

    r = make_runner(bars, pos, {"AAA": {"mfe": 10, "mae": -1,
                                        "mfe_pct": 1.0, "mae_pct": -1.0}})
    r.broker = BoomBroker(bars, pos)
    r._refine_excursion_from_bars(pos)
    assert r._excursion["AAA"]["mfe_pct"] == 1.0

    print("PASS test_excursion_from_minute_bars")


def test_runtime_config_excursion_bars_default():
    """Defaults on, at a cadence that costs one bars call per position/minute."""
    assert RuntimeConfig().excursion_bar_seconds == 60
    print("PASS test_runtime_config_excursion_bars_default")


def run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    run_all()
