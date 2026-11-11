"""survey.py: the signals use no future data, and the simulator's arithmetic is right.

Synthetic prices only: no cache, no network.

    python -m pytest tests/test_survey.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import survey  # noqa: E402


def bars(n=900, seed=0, drift=0.0004, vol=0.012) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(drift + vol * rng.standard_normal(n)))
    o = np.r_[c[0], c[:-1]] * (1 + 0.003 * rng.standard_normal(n))
    h = np.maximum(o, c) * (1 + np.abs(0.004 * rng.standard_normal(n)))
    l = np.minimum(o, c) * (1 - np.abs(0.004 * rng.standard_normal(n)))
    idx = pd.bdate_range("2019-01-01", periods=n)
    return pd.DataFrame({"o": o, "h": h, "l": l, "c": c, "raw": c}, index=idx)


@pytest.mark.parametrize("seed", range(4))
def test_signals_do_not_change_when_the_future_is_removed(seed):
    df = bars(seed=seed)
    cut = df.iloc[:-60]
    for fn in (survey.sig_rsi2, survey.sig_ibs, survey.sig_trend200,
               lambda d: survey.donchian_targets(d["c"])):
        full, part = fn(df), fn(cut)
        assert np.allclose(full.iloc[:-60].to_numpy(), part.to_numpy(), equal_nan=True)
    vix = pd.Series(18 + 4 * np.sin(np.arange(len(df)) / 9), index=df.index)
    v3 = pd.Series(19 + 2 * np.cos(np.arange(len(df)) / 13), index=df.index)
    full = survey.vrp_targets(df, vix, v3)
    part = survey.vrp_targets(cut, vix.iloc[:-60], v3.iloc[:-60])
    assert np.allclose(full.iloc[:-60].to_numpy(), part.to_numpy())


def test_rsi2_rule_enters_and_exits_where_the_rule_says():
    df = bars(seed=5)
    w = survey.sig_rsi2(df)
    c = df["c"]
    rsi, sma200, sma5 = survey.rsi_wilder(c, 2), c.rolling(200).mean(), c.rolling(5).mean()
    entries = w[(w == 1) & (w.shift(1) == 0)].index
    exits = w[(w == 0) & (w.shift(1) == 1)].index
    assert len(entries) > 3
    assert all(rsi[d] < 10 and c[d] > sma200[d] for d in entries)
    assert all(c[d] > sma5[d] for d in exits)


def test_ibs_rule_enters_and_exits_where_the_rule_says():
    df = bars(seed=6, vol=0.02)
    w = survey.sig_ibs(df)
    ibs = (df["c"] - df["l"]) / (df["h"] - df["l"])
    band = df["h"].rolling(10).max() - 2.5 * (df["h"] - df["l"]).rolling(25).mean()
    entries = w[(w == 1) & (w.shift(1) == 0)].index
    exits = w[(w == 0) & (w.shift(1) == 1)].index
    assert len(entries) > 3
    assert all(df["c"][d] < band[d] and ibs[d] < 0.3 for d in entries)
    assert all(df["c"][d] > df["h"].shift(1)[d] for d in exits)


def test_vrp_states():
    df = bars(n=60, vol=0.001)                       # realised vol about 1.6: eVRP > 0
    calm = pd.Series(15.0, index=df.index)
    assert survey.vrp_targets(df, calm, calm + 2).iloc[-1] == pytest.approx(-0.15)   # contango
    assert survey.vrp_targets(df, calm, calm - 2).iloc[-1] == 0.0        # eVRP > 0, backwardation
    wild = bars(n=60, vol=0.05)                      # realised vol about 80: eVRP < 0
    assert survey.vrp_targets(wild, calm, calm + 2).iloc[-1] == pytest.approx(-0.075)
    assert survey.vrp_targets(wild, calm, calm - 2).iloc[-1] == pytest.approx(0.15)


def test_simulate_close_fill_arithmetic():
    idx = pd.bdate_range("2024-01-01", periods=4)
    px = pd.DataFrame({"c": [100.0, 110.0, 99.0, 99.0], "o": [100.0, 104.0, 108.0, 99.0]}, index=idx)
    w = pd.Series([1.0, 1.0, 0.0, 0.0], index=idx)          # decided at each close
    turn = w.diff().abs().fillna(w.abs())
    rf = pd.Series(0.0, index=idx)
    sim = survey.simulate(w, turn, px, rf, cost=0.001)
    # day 0: pay to enter. day 1: +10%. day 2: -10%, then pay to exit. day 3: flat.
    assert sim["raw"].tolist() == pytest.approx([-0.001, 0.10, -0.10 - 0.001, 0.0])


def test_simulate_open_fill_moves_the_entry_and_exit_to_the_next_open():
    idx = pd.bdate_range("2024-01-01", periods=4)
    px = pd.DataFrame({"c": [100.0, 110.0, 99.0, 99.0], "o": [100.0, 104.0, 108.0, 99.0]}, index=idx)
    w = pd.Series([1.0, 1.0, 0.0, 0.0], index=idx)
    sim = survey.simulate(w, w.diff().abs().fillna(w.abs()), px, pd.Series(0.0, index=idx), 0.0,
                          fill="open")
    # enters at day 1's open (104 -> 110), holds overnight and day 2 (110 -> 99),
    # then exits at day 3's open (99 -> 99).
    assert sim["raw"].tolist() == pytest.approx([0.0, 110 / 104 - 1, (108 / 110 - 1) + (99 / 108 - 1), 0.0])


def test_threshold_only_trades_outside_the_band_and_drifts_between():
    idx = pd.bdate_range("2024-01-01", periods=5)
    tg = pd.Series([0.30, 0.32, 0.32, 0.40, 0.0], index=idx)
    r = pd.Series([0.0, 0.0, 0.0, 0.0, 0.0], index=idx)
    w, turn = survey.apply_threshold(tg, r, 0.05, sign_rule=False)
    assert w.tolist() == pytest.approx([0.30, 0.30, 0.30, 0.40, 0.0])
    assert turn.tolist() == pytest.approx([0.30, 0.0, 0.0, 0.10, 0.40])
    w2, _ = survey.apply_threshold(pd.Series([0.5, 0.5], index=idx[:2]),
                                   pd.Series([0.2, 0.0], index=idx[:2]), 0.1, False)
    assert w2.iloc[1] == pytest.approx(0.5 * 1.2 / 1.1)      # drifted to 0.545, inside the band


def test_alpha_regression_recovers_a_planted_alpha():
    rng = np.random.default_rng(7)
    x = 0.01 * rng.standard_normal(5000)
    y = 0.0004 + 0.5 * x + 0.002 * rng.standard_normal(5000)
    a, b, t = survey.nw_alpha(y, x)
    assert a == pytest.approx(0.0004, abs=0.0001) and b == pytest.approx(0.5, abs=0.02) and t > 8
    a0, _, t0 = survey.nw_alpha(0.5 * x + 0.002 * rng.standard_normal(5000), x)
    assert abs(t0) < 3


def test_a_pure_beta_strategy_has_no_alpha():
    """Holding the benchmark at half weight must score zero alpha, whatever it returned."""
    df = bars(n=2000, seed=9, drift=0.001)
    rf = pd.Series(0.0, index=df.index)
    w = pd.Series(0.5, index=df.index)
    sim = survey.simulate(w, pd.Series(0.0, index=df.index), df, rf, 0.0)
    a, b, t = survey.nw_alpha(sim["ex"].iloc[1:].to_numpy(), df["c"].pct_change().iloc[1:].to_numpy())
    assert b == pytest.approx(0.5) and abs(a) < 1e-12


def test_trades_and_drawdown():
    idx = pd.bdate_range("2024-01-01", periods=6)
    px = pd.DataFrame({"c": [100.0, 105.0, 103.0, 103.0, 90.0, 99.0], "o": [100.0] * 6}, index=idx)
    w = pd.Series([1.0, 1.0, 0.0, 1.0, 1.0, 0.0], index=idx)
    tr = survey.trades_from_weights(w, px, 0.0)
    assert [round(t[2], 4) for t in tr] == [0.03, round(99 / 103 - 1, 4)]
    assert survey.drawdown(pd.Series([0.1, -0.2, 0.05])) == pytest.approx(-0.2)
