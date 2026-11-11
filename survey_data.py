"""Data for the E19 strategy survey. See PREREG-SURVEY-2026-11-11.md.

    python -u survey_data.py yahoo      # daily OHLC + adjclose for the ETFs, indices and BTC
    python -u survey_data.py universe   # Alpaca asset list + monthly bars -> point-in-time top 200
    python -u survey_data.py daily      # Alpaca daily bars for every name ever in the universe

Read-only: market data and the asset list. Places no orders and reads no account state.
Everything lands in .survey_cache/ (gitignored).
"""

from __future__ import annotations

import json
import os
import pickle
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

CACHE = Path(".survey_cache")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{t}"
YAHOO_TICKERS = ("SPY", "QQQ", "SSO", "VIXY", "BIL", "^VIX", "^VIX3M", "^IRX", "BTC-USD",
                 # descriptive / post-hoc only, never scored:
                 "TQQQ", "ETH-USD", "SVXY", "IWM")
BARS = "https://data.alpaca.markets/v2/stocks/bars"
ASSETS = "https://paper-api.alpaca.markets/v2/assets"
START = "2016-01-01"
TOP_N = 200
MIN_PRICE = 5.0
FUND_WORDS = re.compile(
    r"\b(ETF|ETN|ETNs|Fund|Funds|Trust|Shares|ProShares|Direxion|iShares|SPDR|Invesco|Vanguard|"
    r"VanEck|WisdomTree|Index|Portfolio|Notes|Warrant|Warrants|Unit|Units|Rights|Preferred|"
    r"Depositary|2X|3X|Bull|Bear|Leveraged|Inverse|Daily|YieldMax|GraniteShares|Tradr|"
    r"Defiance|Roundhill|T-Rex|REX|Acquisition)\b", re.I)
SYMBOL_OK = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def _fname(t: str) -> str:
    return t.replace("^", "_").replace("-", "_")


# ---------------------------------------------------------------- Yahoo

def yahoo() -> None:
    CACHE.mkdir(exist_ok=True)
    meta = {"downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for t in YAHOO_TICKERS:
        r = requests.get(YAHOO.format(t=t), timeout=60,
                         headers={"User-Agent": "Mozilla/5.0 (research script)"},
                         params={"period1": 0, "period2": int(time.time()), "interval": "1d",
                                 "events": "div,split", "includeAdjustedClose": "true"})
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose") or q["close"]
        df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"],
                           "close": q["close"], "adj": adj, "volume": q["volume"]},
                          index=pd.to_datetime(res["timestamp"], unit="s", utc=True)
                          .tz_convert("America/New_York").normalize().tz_localize(None))
        df = df.dropna(subset=["close", "adj"])
        df = df[~df.index.duplicated(keep="last")]
        df.to_pickle(CACHE / f"y_{_fname(t)}.pkl")
        print(f"  {t:8} {len(df):6} rows {df.index[0].date()} -> {df.index[-1].date()}")
        time.sleep(0.4)
    (CACHE / "yahoo_meta.json").write_text(json.dumps(meta))


# ---------------------------------------------------------------- Alpaca

def _keys() -> dict:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True))
    return {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
            "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}


def _get(sess, url, params, tries=6):
    for k in range(tries):
        r = sess.get(url, params=params, timeout=90)
        if r.status_code == 429:
            time.sleep(3 + 2 * k)
            continue
        if r.status_code >= 500:
            time.sleep(2 + k)
            continue
        return r
    return r


def _bars(sess, symbols: list[str], timeframe: str, start: str) -> dict[str, list]:
    """All bars for a batch. A 4xx on the batch is bisected so one bad symbol can't sink it."""
    out: dict[str, list] = {}
    params = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start,
              "adjustment": "all", "feed": "sip", "limit": 10000}
    token = None
    while True:
        if token:
            params["page_token"] = token
        r = _get(sess, BARS, params)
        if r.status_code != 200:
            if len(symbols) == 1:
                return {}
            mid = len(symbols) // 2
            a = _bars(sess, symbols[:mid], timeframe, start)
            a.update(_bars(sess, symbols[mid:], timeframe, start))
            return a
        js = r.json()
        for s, rows in (js.get("bars") or {}).items():
            out.setdefault(s, []).extend(rows)
        token = js.get("next_page_token")
        if not token:
            return out


def universe() -> None:
    CACHE.mkdir(exist_ok=True)
    sess = requests.Session()
    sess.headers.update(_keys())
    assets = []
    for status in ("active", "inactive"):
        r = _get(sess, ASSETS, {"asset_class": "us_equity", "status": status})
        r.raise_for_status()
        assets += r.json()
    keep, dropped_fund, dropped_exch, dropped_sym = {}, 0, 0, 0
    for a in assets:
        if a.get("exchange") not in ("NYSE", "NASDAQ"):
            dropped_exch += 1
        elif not SYMBOL_OK.match(a["symbol"]):
            dropped_sym += 1
        elif FUND_WORDS.search(a.get("name") or ""):
            dropped_fund += 1
        else:
            keep.setdefault(a["symbol"], {"name": a.get("name"), "status": a.get("status"),
                                          "exchange": a.get("exchange")})
    n_inactive = sum(1 for v in keep.values() if v["status"] == "inactive")
    print(f"assets {len(assets)}: kept {len(keep)} ({n_inactive} inactive); dropped "
          f"{dropped_exch} other-exchange, {dropped_sym} odd-symbol, {dropped_fund} fund-like")
    syms = sorted(keep)
    monthly: dict[str, list] = {}
    for i in range(0, len(syms), 100):
        monthly.update(_bars(sess, syms[i:i + 100], "1Month", START))
        if i % 2000 == 0:
            print(f"  monthly bars {i}/{len(syms)}  ({len(monthly)} with data)", flush=True)
    rows = [(s, b["t"][:7], b["c"], b["v"] * b["vw"]) for s, bs in monthly.items() for b in bs]
    m = pd.DataFrame(rows, columns=["symbol", "month", "close", "dollar"])
    dv = m.pivot_table(index="month", columns="symbol", values="dollar", aggfunc="sum")
    px = m.pivot_table(index="month", columns="symbol", values="close", aggfunc="last")
    roll = dv.rolling(3, min_periods=3).sum()
    members = {}
    months = list(dv.index)
    for k in range(3, len(months)):
        prev = months[k - 1]
        score = roll.loc[prev].where(px.loc[prev] >= MIN_PRICE).dropna()
        members[months[k]] = list(score.sort_values(ascending=False).head(TOP_N).index)
    union = sorted({s for v in members.values() for s in v})
    inactive_in = sorted(s for s in union if keep[s]["status"] == "inactive")
    json.dump({"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "members": members, "union": union, "assets": {s: keep[s] for s in union},
               "counts": {"assets": len(assets), "kept": len(keep), "kept_inactive": n_inactive,
                          "with_monthly_bars": len(monthly), "union": len(union),
                          "union_inactive": len(inactive_in)}},
              open(CACHE / "universe.json", "w"))
    print(f"universe months {months[3]} -> {months[-1]}; union {len(union)} names, "
          f"{len(inactive_in)} now inactive: {inactive_in[:40]}")


def daily() -> None:
    sess = requests.Session()
    sess.headers.update(_keys())
    u = json.load(open(CACHE / "universe.json"))
    syms = u["union"] + ["SPY"]
    frames = {}
    for i in range(0, len(syms), 3):         # 3 names x ~2,730 days fits one 10,000-bar page
        got = _bars(sess, syms[i:i + 3], "1Day", START)
        for s, bs in got.items():
            df = pd.DataFrame(bs)
            df.index = pd.to_datetime(df["t"]).dt.tz_convert("America/New_York").dt.normalize() \
                .dt.tz_localize(None)
            frames[s] = df[["o", "h", "l", "c", "v"]].astype(float)
        if i % 150 == 0:
            print(f"  daily bars {i}/{len(syms)}", flush=True)
    idx = frames["SPY"].index
    panel = {f: pd.DataFrame({s: d[f] for s, d in frames.items()}).reindex(idx)
             for f in ("o", "h", "l", "c")}
    pickle.dump(panel, open(CACHE / "stocks_daily.pkl", "wb"))
    print(f"saved {len(frames)} names x {len(idx)} days, {idx[0].date()} -> {idx[-1].date()}; "
          f"missing: {sorted(set(syms) - set(frames))}")


if __name__ == "__main__":
    {"yahoo": yahoo, "universe": universe, "daily": daily}[sys.argv[1]]()
