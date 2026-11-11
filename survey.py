"""E19: survey of published daily strategies. See PREREG-SURVEY-2026-11-11.md.

    python -u survey.py check     # data checks the PREREG lists
    python -u survey.py report    # the seven pre-registered tests and their verdicts
    python -u survey.py posthoc   # variants outside the PREREG, labelled as such

Standalone research: places no orders, touches no account. Data comes from survey_data.py.
Writes survey_results.json and survey_curves.csv next to the printed report.
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(".survey_cache")
SCORED_FROM = pd.Timestamp("2016-01-01")
END = pd.Timestamp("2026-11-10")            # the latest complete close when the data was fetched
C6_FROM = pd.Timestamp("2017-01-01")
T_BAR = 2.45                                # one-sided 0.05, Bonferroni over seven tests
DD_BAR = -0.25
NW_LAGS = 5
COSTS = {"etf": (0.0002, 0.0005), "vixy": (0.0005, 0.0010), "stock": (0.0005, 0.0010),
         "btc": (0.0030, 0.0060)}
BORROW = (0.03, 0.06)                       # per year on short VIXY notional: scored, stress
PUBLISHED = {"C1a": "2008-11-01", "C1b": "2008-11-01", "C2": "2024-05-20", "C3": "2025-06-25",
             "C4": "2016-03-01", "C5": "2025-04-08", "C6": "2008-11-01"}


# ---------------------------------------------------------------- data

def load_y(t: str) -> pd.DataFrame:
    """Yahoo daily bars with open/high/low scaled onto the adjusted-close basis."""
    df = pd.read_pickle(CACHE / f"y_{t.replace('^', '_').replace('-', '_')}.pkl")
    if t.endswith("-USD"):                  # Yahoo stamps crypto bars at 00:00 UTC of their own day
        df.index = df.index + pd.Timedelta(days=1)
    df = df[df.index <= END]
    k = df["adj"] / df["close"]
    out = pd.DataFrame({"o": df["open"] * k, "h": df["high"] * k, "l": df["low"] * k,
                        "c": df["adj"], "raw": df["close"]})
    out["o"] = out["o"].fillna(out["c"].shift(1))
    return out


def cash_returns(index: pd.DatetimeIndex) -> pd.Series:
    """BIL's daily return; ^IRX/252 before BIL has a previous close; 0 on non-trading days."""
    bil = load_y("BIL")["c"].pct_change()
    irx = load_y("^IRX")["raw"].shift(1) / 100 / 252
    rf = bil.reindex(index)
    rf = rf.where(rf.notna() | (index >= bil.index[1]), irx.reindex(index).ffill())
    return rf.fillna(0.0)


def rsi_wilder(close: pd.Series, n: int = 2) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan)).fillna(np.inf)


# ---------------------------------------------------------------- signals (weights at each close)

def _hold_until(entry: pd.Series, exit_: pd.Series) -> pd.Series:
    """1 from an entry close to an exit close. Exits are checked only on days after entry."""
    w = np.zeros(len(entry))
    on = False
    e, x = entry.to_numpy(), exit_.to_numpy()
    for i in range(len(w)):
        if on and x[i]:
            on = False
        elif not on and e[i]:
            on = True
        w[i] = 1.0 if on else 0.0
    return pd.Series(w, index=entry.index)


def sig_rsi2(df: pd.DataFrame) -> pd.Series:
    c = df["c"]
    entry = (rsi_wilder(c, 2) < 10) & (c > c.rolling(200).mean())
    return _hold_until(entry.fillna(False), (c > c.rolling(5).mean()).fillna(False))


def sig_ibs(df: pd.DataFrame, sma_exit: int = 0, sma_filter: int = 0) -> pd.Series:
    c, h, l = df["c"], df["h"], df["l"]
    ibs = (c - l) / (h - l).replace(0, np.nan)
    band = h.rolling(10).max() - 2.5 * (h - l).rolling(25).mean()
    entry = (c < band) & (ibs < 0.3)
    exit_ = c > h.shift(1)
    if sma_filter:
        entry &= c > c.rolling(sma_filter).mean()
    if sma_exit:
        exit_ |= c < c.rolling(sma_exit).mean()
    return _hold_until(entry.fillna(False), exit_.fillna(False))


def sig_trend200(spy: pd.DataFrame) -> pd.Series:
    """1 when SPY closed above its 200-day SMA; the caller lags it one day (next-close fill)."""
    return (spy["c"] > spy["c"].rolling(200).mean()).astype(float)


def vrp_targets(spy: pd.DataFrame, vix: pd.Series, vix3m: pd.Series) -> pd.Series:
    erv = spy["c"].pct_change().rolling(10).std(ddof=1) * np.sqrt(252) * 100
    evrp = vix - erv
    t = pd.Series(0.0, index=spy.index)
    contango, back = vix < vix3m, vix > vix3m
    t[(evrp > 0) & contango] = -vix / 100
    t[(evrp < 0) & contango] = -0.5 * vix / 100
    t[(evrp < 0) & back] = vix / 100
    t[erv.isna() | vix.isna() | vix3m.isna()] = 0.0
    return t


def donchian_targets(close: pd.Series) -> pd.Series:
    vol = close.pct_change().rolling(90).std() * np.sqrt(365)
    size = (0.25 / vol).clip(upper=1.0)
    c = close.to_numpy()
    total = np.zeros(len(c))
    for n in (5, 10, 20, 30, 60, 90, 150, 250, 360):
        hi = close.rolling(n).max().to_numpy()
        lo = close.rolling(n).min().to_numpy()
        mid = (hi + lo) / 2
        on, stop = False, np.nan
        state = np.zeros(len(c))
        for i in range(len(c)):
            if np.isnan(hi[i]):
                continue
            if on:
                stop = max(stop, mid[i])
                if c[i] < stop:
                    on = False
            elif c[i] >= hi[i]:
                on, stop = True, mid[i]
            state[i] = 1.0 if on else 0.0
        total += state
    return (pd.Series(total / 9, index=close.index) * size).fillna(0.0)


def apply_threshold(target: pd.Series, ret_next: pd.Series, band: float, sign_rule: bool):
    """Actual post-trade weight at each close, letting the position drift between trades.

    Returns (weights, turnover). ret_next[i] is the asset's return from close i to close i+1.
    """
    tg, r = target.to_numpy(), ret_next.fillna(0.0).to_numpy()
    w = np.zeros(len(tg))
    turn = np.zeros(len(tg))
    cur = 0.0
    for i in range(len(tg)):
        flip = sign_rule and np.sign(tg[i]) != np.sign(cur)
        if flip or abs(tg[i] - cur) > band:
            turn[i] = abs(tg[i] - cur)
            cur = tg[i]
        w[i] = cur
        cur = cur * (1 + r[i]) / (1 + cur * r[i]) if (1 + cur * r[i]) > 0 else 0.0
    return pd.Series(w, index=target.index), pd.Series(turn, index=target.index)


# ---------------------------------------------------------------- simulation

def simulate(w: pd.Series, turn: pd.Series, px: pd.DataFrame, rf: pd.Series, cost: float,
             fill: str = "close", borrow: float = 0.0) -> pd.DataFrame:
    """Daily raw and excess returns for weights `w` decided at each close.

    fill="close": w[t] is held from close t to close t+1.
    fill="open":  the trade decided at close t fills at the open of t+1, so the overnight leg
                  still carries w[t-1] and the intraday leg carries w[t].
    Trading costs are charged on the day the trade is decided.
    """
    c, o = px["c"], px["o"]
    rc = c.pct_change()
    held = w.shift(1).fillna(0.0)
    if fill == "close":
        gross = held * rc
    else:
        ron, rid = o / c.shift(1) - 1, c / o - 1
        gross = w.shift(2).fillna(0.0) * ron + held * rid
    carry = (-held.clip(upper=0)) * borrow / 252
    raw = gross.fillna(0.0) - carry - cost * turn
    ex = raw - held * rf
    return pd.DataFrame({"raw": raw, "ex": ex, "w": held})


def nw_alpha(y: np.ndarray, x: np.ndarray, lags: int = NW_LAGS):
    """OLS of y on [1, x] with Newey-West errors. Returns (alpha, beta, t_alpha)."""
    X = np.column_stack([np.ones(len(x)), x])
    xtx_inv = np.linalg.inv(X.T @ X)
    b = xtx_inv @ X.T @ y
    u = y - X @ b
    xu = X * u[:, None]
    S = xu.T @ xu
    for l in range(1, lags + 1):
        g = xu[l:].T @ xu[:-l]
        S += (1 - l / (lags + 1)) * (g + g.T)
    V = xtx_inv @ S @ xtx_inv
    return b[0], b[1], b[0] / np.sqrt(V[0, 0])


def trades_from_weights(w: pd.Series, px: pd.DataFrame, cost: float, fill: str = "close"):
    """Round trips for a 0/1 weight series: (entry day, exit day, net return)."""
    out, start = [], None
    ww = w.to_numpy()
    c, o = px["c"].to_numpy(), px["o"].to_numpy()
    idx = w.index
    for i in range(len(ww)):
        if ww[i] == 1 and start is None:
            start = i
        elif ww[i] == 0 and start is not None:
            if fill == "close":
                r = c[i] / c[start] - 1
            elif i + 1 < len(c):
                r = o[i + 1] / o[start + 1] - 1
            else:
                r = c[i] / o[start + 1] - 1
            out.append((idx[start], idx[i], r - 2 * cost))
            start = None
    return out


def drawdown(raw: pd.Series) -> float:
    eq = (1 + raw).cumprod()
    return float((eq / eq.cummax() - 1).min())


def summarise(name, sim, bench_ex, ann, trades=None, sim_stress=None, sim_open=None,
              start=SCORED_FROM) -> dict:
    """Every number the PREREG asks for, over [start, END]."""
    s = sim[(sim.index >= start) & (sim.index <= END)]
    b = bench_ex.reindex(s.index).fillna(0.0)
    mid = start + (END - start) / 2
    a, beta, t = nw_alpha(s["ex"].to_numpy(), b.to_numpy())
    halves = []
    for m in (s.index < mid, s.index >= mid):
        ah, _, th = nw_alpha(s["ex"][m].to_numpy(), b[m].to_numpy())
        halves.append((ah * ann, th))
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    eq = (1 + s["raw"]).cumprod()
    res = {"name": name, "from": str(s.index[0].date()), "to": str(s.index[-1].date()),
           "alpha_ann": a * ann, "beta": beta, "alpha_t": t,
           "alpha_h1": halves[0][0], "alpha_h2": halves[1][0],
           "t_h1": halves[0][1], "t_h2": halves[1][1],
           "mean_ex_ann": s["ex"].mean() * ann,
           "sharpe": s["ex"].mean() / s["ex"].std() * np.sqrt(ann) if s["ex"].std() else np.nan,
           "cagr": eq.iloc[-1] ** (1 / yrs) - 1, "vol": s["raw"].std() * np.sqrt(ann),
           "mdd": drawdown(s["raw"]), "exposure": float((s["w"].abs() > 1e-9).mean()),
           "avg_weight": float(s["w"].abs().mean()),
           "corr_bench": float(np.corrcoef(s["ex"], b)[0, 1]),
           "yearly": {str(y): float((1 + g).prod() - 1) for y, g in s["raw"].groupby(s.index.year)}}
    if sim_stress is not None:
        res["stress_mean_ex_ann"] = sim_stress["ex"].reindex(s.index).mean() * ann
    if sim_open is not None:
        so = sim_open.reindex(s.index)
        res["open_mean_ex_ann"] = so["ex"].mean() * ann
        res["open_cagr"] = (1 + so["raw"]).prod() ** (1 / yrs) - 1
    if trades is not None:
        tr = [x for x in trades if start <= x[0] <= END]
        r = np.array([x[2] for x in tr])
        k = int(np.ceil(0.05 * len(r)))
        res.update({"n_trades": len(r), "trades_per_year": len(r) / yrs,
                    "win_rate": float((r > 0).mean()) if len(r) else np.nan,
                    "mean_trade": float(r.mean()) if len(r) else np.nan,
                    "median_trade": float(np.median(r)) if len(r) else np.nan,
                    "worst_trade": float(r.min()) if len(r) else np.nan,
                    "avg_days": float(np.mean([(x[1] - x[0]).days for x in tr])) if tr else np.nan,
                    "robust_total": float(np.sort(r)[:len(r) - k].sum()) if len(r) else np.nan,
                    "robust_rule": f"sum of trade returns without the best {k} of {len(r)}"})
    else:
        r = s["raw"].to_numpy()
        res.update({"robust_total": float(np.sort(r)[:-10].sum()),
                    "robust_rule": "sum of daily returns without the best 10 days"})
    return res


def verdict(res: dict) -> dict:
    c = {"1 alpha t >= 2.45": res["alpha_t"] >= T_BAR,
         "2 alpha > 0 both halves": res["alpha_h1"] > 0 and res["alpha_h2"] > 0,
         "3 stress costs and next-open > 0": res.get("stress_mean_ex_ann", -1) > 0
                                             and res.get("open_mean_ex_ann", -1) > 0,
         "4 survives without best trades": res["robust_total"] > 0,
         "5 max drawdown >= -25%": res["mdd"] >= DD_BAR}
    return {"criteria": c, "pass": all(c.values())}


# ---------------------------------------------------------------- the candidates

def _single(name, w, px, rf, bench_ex, kind, lag_fill=False, trades=True, ann=252,
            start=SCORED_FROM):
    """A 0/1 single-asset candidate at scored costs, stress costs and next-open fills."""
    c0, c1 = COSTS[kind]
    turn = w.diff().abs().fillna(w.abs())
    sim = simulate(w, turn, px, rf, c0)
    stress = simulate(w, turn, px, rf, c1)
    opn = simulate(w, turn, px, rf, c0, fill="open")
    tr = trades_from_weights(w, px, c0) if trades else None
    res = summarise(name, sim, bench_ex, ann, tr, stress, opn, start)
    return res, sim, tr


def run_all():
    spy, qqq, sso, vixy = load_y("SPY"), load_y("QQQ"), load_y("SSO"), load_y("VIXY")
    btc = load_y("BTC-USD")
    rf = cash_returns(spy.index)
    spy_ex = spy["c"].pct_change() - rf
    qqq_ex = (qqq["c"].pct_change() - rf.reindex(qqq.index)).fillna(0.0)
    out, sims, trades = {}, {}, {}

    out["C1a"], sims["C1a"], trades["C1a"] = _single("RSI(2) pullback, SPY", sig_rsi2(spy), spy,
                                                     rf, spy_ex, "etf")
    rfq = rf.reindex(qqq.index).fillna(0.0)
    out["C1b"], sims["C1b"], trades["C1b"] = _single("RSI(2) pullback, QQQ", sig_rsi2(qqq), qqq,
                                                     rfq, qqq_ex, "etf")
    out["C2"], sims["C2"], trades["C2"] = _single("IBS band reversion, QQQ", sig_ibs(qqq), qqq,
                                                  rfq, qqq_ex, "etf")

    # C3: dual-signal VIX premium on VIXY
    vix = load_y("^VIX")["raw"].reindex(spy.index).ffill()
    vix3m = load_y("^VIX3M")["raw"].reindex(spy.index)
    vx = vixy.reindex(spy.index)
    tg = vrp_targets(spy, vix, vix3m).where(vx["c"].notna(), 0.0)
    r_next = vx["c"].pct_change().shift(-1)
    w3, turn3 = apply_threshold(tg, r_next, 0.02, sign_rule=True)
    c0, c1 = COSTS["vixy"]
    sims["C3"] = simulate(w3, turn3, vx, rf, c0, borrow=BORROW[0])
    out["C3"] = summarise("Dual-signal VIX premium, VIXY", sims["C3"], spy_ex, 252, None,
                          simulate(w3, turn3, vx, rf, c1, borrow=BORROW[1]),
                          simulate(w3, turn3, vx, rf, c0, fill="open", borrow=BORROW[0]))
    out["C3"]["n_rebalances"] = int((turn3[turn3.index >= SCORED_FROM] > 0).sum())
    out["C3"]["sign_changes_per_year"] = float(
        (np.sign(w3[w3.index >= SCORED_FROM]).diff().abs() > 0).sum() /
        ((END - SCORED_FROM).days / 365.25))

    # C4: leveraged 200-day trend; the signal fills at the next close
    w4 = sig_trend200(spy).shift(1).fillna(0.0)
    so = sso.reindex(spy.index)
    out["C4"], sims["C4"], trades["C4"] = _single("Leveraged 200-day trend, SSO",
                                                  w4.where(so["c"].notna(), 0.0), so, rf, spy_ex,
                                                  "etf")

    # C5: crypto Donchian ensemble on BTC (365-day year; "next open" = a one-day delay)
    rfb = cash_returns(btc.index)
    btc_ex = btc["c"].pct_change() - rfb
    tg5 = donchian_targets(btc["c"])
    w5, turn5 = apply_threshold(tg5, btc["c"].pct_change().shift(-1), 0.05, sign_rule=False)
    c0, c1 = COSTS["btc"]
    sims["C5"] = simulate(w5, turn5, btc, rfb, c0)
    w5d, turn5d = w5.shift(1).fillna(0.0), turn5.shift(1).fillna(0.0)
    out["C5"] = summarise("Crypto Donchian ensemble, BTC", sims["C5"], btc_ex, 365, None,
                          simulate(w5, turn5, btc, rfb, c1),
                          simulate(w5d, turn5d, btc, rfb, c0))
    out["C5"]["n_rebalances"] = int((turn5[turn5.index >= SCORED_FROM] > 0).sum())

    bench = {"SPY": spy_ex, "QQQ": qqq_ex, "BTC": btc_ex}
    return out, sims, trades, bench, {"spy": spy, "qqq": qqq, "btc": btc, "rf": rf}


# ---------------------------------------------------------------- C6: large-cap stock portfolio

def run_c6(cost: float, fill: str = "close", rsi_max: float = 10.0, slots: int = 10,
           max_days: int = 10):
    """RSI(2) pullback across the point-in-time top-200 universe. Returns (sim, trades, notes)."""
    panel = pickle.load(open(CACHE / "stocks_daily.pkl", "rb"))
    uni = json.load(open(CACHE / "universe.json"))["members"]
    C, O = panel["c"], panel["o"]
    C, O = C[C.index <= END], O[O.index <= END]
    Cf = C.ffill(limit=5)
    sma200, sma5 = Cf.rolling(200).mean(), Cf.rolling(5).mean()
    rsi = Cf.apply(lambda s: rsi_wilder(s, 2))
    days, syms = C.index, list(C.columns)
    col = {s: j for j, s in enumerate(syms)}
    c, o, cf = C.to_numpy(), O.to_numpy(), Cf.to_numpy()
    s200, s5, rs = sma200.to_numpy(), sma5.to_numpy(), rsi.to_numpy()
    cash, pos = 1.0, {}                     # pos: j -> [shares, entry price, entry i, last price]
    equity, invested, trades = [], [], []
    pending_exit, pending_entry = [], []
    stale = 0
    for i in range(len(days)):
        if fill == "open":
            for j in pending_exit:
                if j in pos:
                    p = o[i, j] if not np.isnan(o[i, j]) else pos[j][3]
                    sh, ep, ei, _ = pos.pop(j)
                    cash += sh * p * (1 - cost)
                    trades.append((days[ei], days[i], p / ep - 1 - 2 * cost, syms[j]))
            eq_now = cash + sum(v[0] * v[3] for v in pos.values())
            for j in pending_entry:
                if len(pos) >= slots or j in pos or np.isnan(o[i, j]):
                    continue
                notional = min(eq_now / slots, cash / (1 + cost))
                if notional <= 0:
                    break
                pos[j] = [notional / o[i, j], o[i, j], i, o[i, j]]
                cash -= notional * (1 + cost)
            pending_exit, pending_entry = [], []
        for j, v in pos.items():
            if not np.isnan(c[i, j]):
                v[3] = c[i, j]
        exits = []
        for j, v in pos.items():
            gone = np.isnan(cf[i, j])        # no print for more than 5 days: treat as delisted
            if gone or cf[i, j] > s5[i, j] or i - v[2] >= max_days:
                exits.append(j)
                stale += gone
        month = str(days[i])[:7]
        members = uni.get(month, [])
        cand = []
        for s in members:
            j = col.get(s)
            if j is None or np.isnan(c[i, j]) or np.isnan(s200[i, j]):
                continue
            if c[i, j] > s200[i, j] and rs[i, j] < rsi_max and (j not in pos or j in exits):
                cand.append((rs[i, j], j))
        cand = [j for _, j in sorted(cand)]
        if fill == "close":
            for j in exits:
                sh, ep, ei, lp = pos.pop(j)
                cash += sh * lp * (1 - cost)
                trades.append((days[ei], days[i], lp / ep - 1 - 2 * cost, syms[j]))
            eq_now = cash + sum(v[0] * v[3] for v in pos.values())
            for j in cand:
                if len(pos) >= slots:
                    break
                if j in pos:
                    continue
                notional = min(eq_now / slots, cash / (1 + cost))
                if notional <= 0:
                    break
                pos[j] = [notional / c[i, j], c[i, j], i, c[i, j]]
                cash -= notional * (1 + cost)
        else:
            pending_exit = exits
            free = slots - (len(pos) - len(exits))
            pending_entry = [j for j in cand if j not in pos or j in exits][:max(free, 0)]
        held = sum(v[0] * v[3] for v in pos.values())
        equity.append(cash + held)
        invested.append(held / (cash + held))
    eq = pd.Series(equity, index=days)
    raw = eq.pct_change().fillna(0.0)
    w = pd.Series(invested, index=days).shift(1).fillna(0.0)
    return pd.DataFrame({"raw": raw, "w": w}), trades, {"stale_exits": int(stale)}


def c6_result(spy_ex, rf):
    c0, c1 = COSTS["stock"]
    sims = {}
    for key, cost, fill in (("scored", c0, "close"), ("stress", c1, "close"), ("open", c0, "open")):
        sim, tr, notes = run_c6(cost, fill)
        sim["ex"] = sim["raw"] - sim["w"] * rf.reindex(sim.index).fillna(0.0)
        sims[key] = (sim, tr, notes)
    sim, tr, notes = sims["scored"]
    res = summarise("RSI(2) pullback, top-200 stocks", sim, spy_ex, 252,
                    [(a, b, r) for a, b, r, _ in tr], sims["stress"][0], sims["open"][0],
                    start=C6_FROM)
    res["stale_exits"] = notes["stale_exits"]
    return res, sim, tr


# ---------------------------------------------------------------- reports

def _line(k, r):
    v = verdict(r)
    crit = "".join("Y" if x else "n" for x in v["criteria"].values())
    return (f"{k:4} {r['name']:34} alpha {r['alpha_ann']:+7.2%} t {r['alpha_t']:+5.2f}  "
            f"beta {r['beta']:5.2f}  halves {r['alpha_h1']:+6.2%}/{r['alpha_h2']:+6.2%}  "
            f"Sharpe {r['sharpe']:5.2f}  CAGR {r['cagr']:+6.2%}  maxDD {r['mdd']:+6.1%}  "
            f"in-mkt {r['exposure']:4.0%}  [{crit}] {'PASS' if v['pass'] else 'FAIL'}")


def check() -> None:
    meta = json.loads((CACHE / "yahoo_meta.json").read_text())
    print(f"Yahoo downloaded {meta['downloaded_at']}; scored through {END.date()}")
    for t in ("SPY", "QQQ", "SSO", "VIXY", "BIL", "^VIX", "^VIX3M", "^IRX", "BTC-USD"):
        df = load_y(t)
        r = df["c"].pct_change()
        lim = 0.25 if t in ("BTC-USD", "VIXY", "^VIX", "^VIX3M", "^IRX") else 0.20
        big = r[r.abs() > lim]
        print(f"{t:8} {df.index[0].date()} -> {df.index[-1].date()}  {len(df)} rows  "
              f"|ret| > {lim:.0%}: " + (", ".join(f"{d.date()} {v:+.0%}" for d, v in big.items())
                                         if t not in ("^IRX",) and len(big) else "none/n.a."))
    if (CACHE / "stocks_daily.pkl").exists():
        panel = pickle.load(open(CACHE / "stocks_daily.pkl", "rb"))
        u = json.load(open(CACHE / "universe.json"))
        C = panel["c"]
        r = C.pct_change(fill_method=None)
        big = (r.abs() > 0.25).sum().sum()
        print(f"stocks: {C.shape[1]} names x {C.shape[0]} days, {C.index[0].date()} -> "
              f"{C.index[-1].date()}; universe counts {u['counts']}; daily |ret| > 25%: {big} "
              f"of {r.notna().sum().sum()} ({big / r.notna().sum().sum():.4%})")
        ends = C.apply(lambda s: s.last_valid_index())
        print(f"  names whose data ends before {END.date()}: {(ends < END - pd.Timedelta(days=10)).sum()}")


def report() -> None:
    out, sims, trades, bench, px = run_all()
    if (CACHE / "stocks_daily.pkl").exists():
        out["C6"], sims["C6"], tr6 = c6_result(bench["SPY"], px["rf"])
        trades["C6"] = tr6
    print(f"E19 survey. Scored {SCORED_FROM.date()} -> {END.date()} (C6 from {C6_FROM.date()}). "
          f"Bar: alpha t >= {T_BAR}, five criteria [1..5].\n")
    for k in sorted(out):
        print(_line(k, out[k]))
    print("\n== detail")
    for k in sorted(out):
        r = out[k]
        print(f"{k} {r['name']}: mean excess {r['mean_ex_ann']:+.2%}/yr, stress costs "
              f"{r.get('stress_mean_ex_ann', float('nan')):+.2%}, next-open "
              f"{r.get('open_mean_ex_ann', float('nan')):+.2%}; half t {r['t_h1']:+.2f}/{r['t_h2']:+.2f}; "
              f"vol {r['vol']:.1%}; corr with benchmark {r['corr_bench']:+.2f}; "
              f"{r['robust_rule']}: {r['robust_total']:+.2%}")
        if "n_trades" in r:
            print(f"     {r['n_trades']} trades ({r['trades_per_year']:.1f}/yr), win rate "
                  f"{r['win_rate']:.0%}, mean {r['mean_trade']:+.2%}, median {r['median_trade']:+.2%}, "
                  f"worst {r['worst_trade']:+.1%}, avg hold {r['avg_days']:.1f} calendar days")
    print("\n== year by year, cash-earns-nothing basis")
    years = sorted({y for r in out.values() for y in r["yearly"]})
    print("  year " + "".join(f"{k:>9}" for k in sorted(out)))
    for y in years:
        print(f"  {y} " + "".join(f"{out[k]['yearly'].get(y, float('nan')):+9.1%}" for k in sorted(out)))
    print("\n== descriptive only: before 2016, and after each source's publication")
    for k in sorted(sims):
        s = sims[k]
        b = bench["BTC" if k == "C5" else "QQQ" if k in ("C1b", "C2") else "SPY"]
        ann = 365 if k == "C5" else 252
        pre = s[s.index < SCORED_FROM]
        pre = pre[pre["w"].abs().cumsum() > 0]
        post = s[(s.index >= pd.Timestamp(PUBLISHED[k])) & (s.index <= END)]
        txt = []
        for label, seg in (("pre-2016", pre), (f"since {PUBLISHED[k]}", post)):
            if len(seg) > 250:
                a, be, t = nw_alpha(seg["ex"].to_numpy(), b.reindex(seg.index).fillna(0).to_numpy())
                txt.append(f"{label} ({seg.index[0].date()}..): alpha {a * ann:+.2%} t {t:+.2f} "
                           f"Sharpe {seg['ex'].mean() / seg['ex'].std() * np.sqrt(ann):.2f}")
            else:
                txt.append(f"{label}: too short")
        print(f"  {k}: " + ";  ".join(txt))
    json.dump({k: {**v, **verdict(v)} for k, v in out.items()},
              open("survey_results.json", "w"), indent=1, default=str)
    curves = pd.DataFrame({k: (1 + s["raw"][(s.index >= SCORED_FROM) & (s.index <= END)]).cumprod()
                           for k, s in sims.items() if k != "C5"})
    for name, key in (("SPY", "spy"), ("QQQ", "qqq")):
        r = px[key]["c"].pct_change()
        curves[name] = (1 + r[(r.index >= SCORED_FROM) & (r.index <= END)]).cumprod()
    c5 = sims["C5"]["raw"]
    curves = curves.join((1 + c5[(c5.index >= SCORED_FROM) & (c5.index <= END)]).cumprod()
                         .rename("C5"), how="left")
    curves.to_csv("survey_curves.csv", float_format="%.5f")
    rows = []
    for k, tr in trades.items():
        for t in tr or []:
            if t[0] >= (C6_FROM if k == "C6" else SCORED_FROM):
                rows.append((k, t[0].date(), t[1].date(), round(t[2], 5), t[3] if len(t) > 3 else ""))
    pd.DataFrame(rows, columns=["candidate", "entry", "exit", "net_return", "symbol"]) \
        .to_csv("survey_trades.csv", index=False)


def monthly_momentum(lookback: int, skip: int, top: int = 20, cost: float = 0.0005):
    """POST HOC helper: each month hold the `top` universe names with the highest return over
    the `lookback` months ending `skip` months before the last month-end, equal weight, traded at the first close of the month.

    (1, 0) is Medhat & Schmeling's (2022) short-term momentum among high-turnover stocks;
    (12, 1) is Jegadeesh & Titman's (1993) 12-1 momentum. Specs were written down before this
    function was first run. Long-only, 5 bp a side. Returns a simulate()-shaped frame.
    """
    panel = pickle.load(open(CACHE / "stocks_daily.pkl", "rb"))
    uni = json.load(open(CACHE / "universe.json"))["members"]
    C = panel["c"][panel["c"].index <= END].ffill(limit=5)
    R = C.pct_change(fill_method=None).fillna(0.0)
    days = C.index
    ym = days.strftime("%Y-%m")
    last = pd.Series(np.arange(len(days)), index=ym).groupby(level=0).max()   # month-end row
    months = list(last.index)
    w = pd.DataFrame(0.0, index=days, columns=C.columns)
    turn = pd.Series(0.0, index=days)
    prev = pd.Series(0.0, index=C.columns)
    for k in range(lookback + skip + 1, len(months)):
        m = months[k]
        # formation window ends at the close of month k-1-skip: nothing from month k is used
        a, b = last[months[k - 1 - skip - lookback]], last[months[k - 1 - skip]]
        names = [n for n in uni.get(m, []) if n in C.columns]
        mom = (C.iloc[b][names] / C.iloc[a][names] - 1).dropna()
        pick = list(mom.sort_values(ascending=False).head(top).index)
        tgt = pd.Series(0.0, index=C.columns)
        tgt[pick] = 1.0 / top
        first = last[months[k - 1]] + 1                       # first trading day of month m
        end = last[m]
        w.iloc[first:end + 1] = tgt.to_numpy()                # held from that close onward
        turn.iloc[first] = (tgt - prev).abs().sum()
        prev = tgt
    raw = (w.shift(1).fillna(0.0) * R).sum(axis=1) - cost * turn
    held = w.shift(1).fillna(0.0).sum(axis=1)
    return pd.DataFrame({"raw": raw, "w": held})


def _donchian_variant(close: pd.Series, lookbacks, vol_target: float) -> pd.Series:
    """POST HOC only: donchian_targets with other lookbacks or volatility target. The frozen
    rule (E20) is donchian_targets itself and is not routed through here."""
    vol = close.pct_change().rolling(90).std() * np.sqrt(365)
    size = (vol_target / vol).clip(upper=1.0)
    c = close.to_numpy()
    total = np.zeros(len(c))
    for n in lookbacks:
        hi = close.rolling(n).max().to_numpy()
        mid = (hi + close.rolling(n).min().to_numpy()) / 2
        on, stop = False, np.nan
        for i in range(len(c)):
            if np.isnan(hi[i]):
                continue
            if on:
                stop = max(stop, mid[i])
                if c[i] < stop:
                    on = False
            elif c[i] >= hi[i]:
                on, stop = True, mid[i]
            total[i] += 1.0 if on else 0.0
    return (pd.Series(total / len(lookbacks), index=close.index) * size).fillna(0.0)


def posthoc() -> None:
    """Outside the PREREG. Nothing here can produce a pass; it only helps read the results."""
    out, sims, trades, bench, px = run_all()
    spy, qqq, btc, rf = px["spy"], px["qqq"], px["btc"], px["rf"]
    rfq = rf.reindex(qqq.index).fillna(0.0)
    rfb = cash_returns(btc.index)
    print("POST HOC. None of this was pre-registered and none of it can produce a pass.")
    print()

    print("== C5: where does the BTC result come from?")
    vol = btc["c"].pct_change().rolling(90).std() * np.sqrt(365)
    vt = (0.25 / vol).clip(upper=1.0).fillna(0.0)
    c0 = COSTS["btc"][0]
    wv, tv = apply_threshold(vt, btc["c"].pct_change().shift(-1), 0.05, False)
    r = summarise("vol-targeted buy and hold BTC", simulate(wv, tv, btc, rfb, c0), bench["BTC"], 365)
    print("  " + _line("ph", r))
    s5 = sims["C5"]
    for a, b in (("2016-01-01", "2018-12-31"), ("2019-01-01", "2021-12-31"),
                 ("2022-01-01", "2024-12-31"), ("2025-01-01", str(END.date()))):
        seg = s5[(s5.index >= a) & (s5.index <= b)]
        al, be, t = nw_alpha(seg["ex"].to_numpy(), bench["BTC"].reindex(seg.index).fillna(0).to_numpy())
        bh = (1 + btc["c"].pct_change().reindex(seg.index).fillna(0)).prod() - 1
        print(f"  {a[:4]}-{b[:4]}: C5 total {(1 + seg['raw']).prod() - 1:+7.1%}  Sharpe "
              f"{seg['ex'].mean() / seg['ex'].std() * np.sqrt(365):5.2f}  alpha {al * 365:+6.1%} "
              f"t {t:+5.2f}  maxDD {drawdown(seg['raw']):+6.1%}   (BTC buy and hold {bh:+8.1%}, "
              f"maxDD {drawdown(btc['c'].pct_change().reindex(seg.index).fillna(0)):+6.1%})")
    eth = load_y("ETH-USD")
    rfe = cash_returns(eth.index)
    we, te = apply_threshold(donchian_targets(eth["c"]), eth["c"].pct_change().shift(-1), 0.05, False)
    r = summarise("Donchian ensemble, ETH (2019+)", simulate(we, te, eth, rfe, c0),
                  eth["c"].pct_change() - rfe, 365, start=pd.Timestamp("2019-01-01"))
    print("  " + _line("ph", r))
    for name, lbs in (("short lookbacks 5-30", (5, 10, 20, 30)), ("long lookbacks 60-360", (60, 90, 150, 250, 360))):
        pass

    print()
    print("== C5: is the pass a knife-edge? Neighbouring settings, same costs (alpha t; scored rule marked *)")
    ALL = (5, 10, 20, 30, 60, 90, 150, 250, 360)
    ret_next = btc["c"].pct_change().shift(-1)
    assert np.allclose(_donchian_variant(btc["c"], ALL, 0.25), donchian_targets(btc["c"]))
    grid = []
    for label, lbs, vt_, band in (
            [("all nine, vol 25%, band 0.05 *", ALL, 0.25, 0.05)] +
            [(f"all nine, vol {int(v * 100)}%, band {b:.2f}", ALL, v, b)
             for v in (0.20, 0.25, 0.30) for b in (0.02, 0.05, 0.10) if (v, b) != (0.25, 0.05)] +
            [("short lookbacks 5-30 only", ALL[:4], 0.25, 0.05),
             ("long lookbacks 60-360 only", ALL[4:], 0.25, 0.05),
             ("without the 5-day model", ALL[1:], 0.25, 0.05),
             ("without the 360-day model", ALL[:-1], 0.25, 0.05)]):
        wg, tg_ = apply_threshold(_donchian_variant(btc["c"], lbs, vt_), ret_next, band, False)
        r = summarise(label, simulate(wg, tg_, btc, rfb, c0), bench["BTC"], 365)
        grid.append(r["alpha_t"])
        print(f"  {label:34} alpha {r['alpha_ann']:+6.2%}  t {r['alpha_t']:+5.2f}  halves "
              f"{r['alpha_h1']:+6.2%}/{r['alpha_h2']:+6.2%}  maxDD {r['mdd']:+6.1%}")
    print(f"  {len(grid)} settings: alpha t from {min(grid):.2f} to {max(grid):.2f}, "
          f"{sum(t >= T_BAR for t in grid)} at or above {T_BAR}")
    print("  paper's own Table 1 (2015-01 to 2025-03, no costs, 200% cap): CAGR 30%, vol 17%, "
          "Sharpe 1.58, MDD 19%, alpha 14%, beta 0.17")

    print()
    print("== C2: the article's tuned variants, and the same rule elsewhere")
    for name, w, pxx, rff, bx in (
            ("C2 + exit below 300-day SMA, QQQ", sig_ibs(qqq, sma_exit=300), qqq, rfq, bench["QQQ"]),
            ("C2 + only above 300-day SMA, QQQ", sig_ibs(qqq, sma_filter=300), qqq, rfq, bench["QQQ"]),
            ("C2 base rule, SPY", sig_ibs(spy), spy, rf, bench["SPY"])):
        r, _, _ = _single(name, w, pxx, rff, bx, "etf")
        print("  " + _line("ph", r) + f"  trades/yr {r['trades_per_year']:.1f}")
    iwm = load_y("IWM")
    rfi = rf.reindex(iwm.index).fillna(0.0)
    r, _, _ = _single("C2 base rule, IWM", sig_ibs(iwm), iwm, rfi, iwm["c"].pct_change() - rfi, "etf")
    print("  " + _line("ph", r) + f"  trades/yr {r['trades_per_year']:.1f}")
    tq = load_y("TQQQ")
    wq = sig_ibs(qqq).reindex(tq.index).fillna(0.0)
    r, _, _ = _single("C2 signal from QQQ, held in TQQQ", wq, tq, rf.reindex(tq.index).fillna(0.0),
                      bench["QQQ"], "etf")
    print("  " + _line("ph", r))

    print()
    print("== monthly return correlations, scored period (cash-earns-nothing)")
    if (CACHE / "stocks_daily.pkl").exists():
        _, sims["C6"], _ = c6_result(bench["SPY"], rf)
    m = pd.DataFrame({k: (1 + s["raw"][(s.index >= C6_FROM) & (s.index <= END)])
                     .groupby(lambda d: (d.year, d.month)).prod() - 1 for k, s in sims.items()})
    m["SPY"] = (1 + spy["c"].pct_change()[(spy.index >= C6_FROM)]).groupby(lambda d: (d.year, d.month)).prod() - 1
    print(m.corr().round(2).to_string())
    m.corr().round(3).to_csv("survey_corr.csv")

    if (CACHE / "stocks_daily.pkl").exists():
        print()
        print("== C6's universe, the opposite trade: monthly momentum among the top-200 names")
        print("   (C6 bought dips there and lost to SPY; Medhat & Schmeling 2022 predict continuation)")
        for name, lb, sk in (("1-month momentum, top 20 of 200", 1, 0),
                             ("12-1 momentum, top 20 of 200", 12, 1)):
            sim = monthly_momentum(lb, sk)
            sim["ex"] = sim["raw"] - sim["w"] * rf.reindex(sim.index).fillna(0.0)
            start = pd.Timestamp("2017-03-01")
            r = summarise(name, sim, bench["SPY"], 252, start=start)
            print("  " + _line("ph", r))
            print("       yearly " + "  ".join(f"{y} {v:+.0%}" for y, v in r["yearly"].items()))


if __name__ == "__main__":
    {"check": check, "report": report, "posthoc": posthoc}[sys.argv[1] if len(sys.argv) > 1 else "report"]()
