#!/usr/bin/env python3
"""Bar-level trade replay — reconstructs the true intraday path of every trade
in state/trade_history.csv from 1-minute bars, then re-runs each trade under
alternative exit rules.

WHY THIS EXISTS
---------------
MFE/MAE summary statistics record the best and worst excursion but NOT their
order, so they cannot tell you whether a trade went +2% and then died, or died
and then recovered. That ambiguity is why the breakeven-stop question came back
as "somewhere between +207 and -280" in ANALYSIS-2026-09-29.md. Walking the
bars in order removes the ambiguity.

WHAT IT IS NOT
--------------
This is a MEASUREMENT harness, not a parameter search. It reports a grid
because seeing the shape is informative, but the project is 0-for-7 on
picking the best cell of a grid. Any parameter chosen from this output still
needs its own out-of-sample holdout, and the criteria written down first.

FILL REALISM
------------
Every counterfactual is reported under two fill assumptions:

  touch  - a level fills if the bar's high (long) / low (short) reaches it.
           OPTIMISTIC: a one-print wick high is a price you could not have sold.
  close  - a level fills only if the bar CLOSES through it.
           CONSERVATIVE: you give up the whole bar before acting.

The truth sits between them. If a change is only good under `touch`, it is a
wick artifact and should be discarded. --min-trades mirrors v1.18's
`excursion_min_trades`: a bar with fewer trades than that cannot set an extreme.

INTRABAR ORDERING — the one assumption you cannot escape
--------------------------------------------------------
A 1-minute OHLC bar does not say whether the high or the low came first. This
replay resolves it the standard way: within each bar the ADVERSE extreme is
assumed to happen first (so an existing stop gets hit), and only then is the
favourable extreme used to move the peak, arm the trail or arm a breakeven.

A stop tightened DURING a bar is therefore not re-tested against that same
bar's low. Doing so would clip every winner whose bar had a wide range, on an
ordering assumption there is no evidence for. The consequence to keep in mind:
trailing exits here are slightly OPTIMISTIC, and hard-stop exits are slightly
PESSIMISTIC. If a result hinges on that, it is too fragile to trade.

FEED
----
Defaults to SIP. The bot TRADES on IEX, but the broker fills against the
consolidated tape, so SIP is the right ground truth for "would this stop
actually have filled". Use --feed iex to see what the bot itself could see;
the gap between the two is itself worth knowing.

!!!  STATUS 2026-10-01: THIS HARNESS DOES NOT VALIDATE. DO NOT TRUST ITS GRID. !!!

    Two validation runs against the 100 booked trades:

        entry bar INCLUDED   touch -224.90   close -106.13
        entry bar EXCLUDED   touch +234.31   close +262.47
        ACTUAL                      -22.60

    One modelling choice -- whether the bar you entered on counts -- swings the
    answer by +459. The trial's whole P&L is -22.60, so the ambiguity is ~20x
    the quantity being measured, and the true value sits between the two with
    no principled way to pick.

    The cause is structural, not a bug: a 1-minute OHLC bar does not record
    whether the high or the low came first, nor where in the minute the fill
    landed. For a breakout strategy whose entry bar IS the breakout bar, that
    is the dominant term.

    Run 2 also scores the LIVE config at +2.569/trade. Live made -0.226/trade.
    A harness that flatters the live rules by +2.8/trade is not measuring them.

    DO NOT "fix" this by tuning assumptions until validation passes. That fits
    the harness to the answer and makes every row below it meaningless.

    What would actually resolve it: sub-minute data, or per-trade validation
    against the bot's own logged exit TIMES (TRAIL EXIT / TIME STOP / stop fill
    timestamps are all in logs/), so divergence can be localised to individual
    trades instead of hidden inside an aggregate that can match by accident.

    Until then E5, E6, stop width and the VWAP rows are UNANSWERED.

USAGE (run where your Alpaca keys live -- reads .env, same as the bot)
    python -u replay.py --validate            # harness sanity check FIRST
    python -u replay.py --all | Tee-Object -FilePath replay_out.txt
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".replay_cache"
HISTORY = ROOT / "state" / "trade_history.csv"
FEATURES = ROOT / "state" / "trade_features.csv"
ET = ZoneInfo("America/New_York")

# Matches config.yaml: flatten_before_close_minutes: 10
EOD_FLATTEN = dtime(15, 50)
MARKET_OPEN = dtime(9, 30)

# Live config under test (v1.14 params)
LIVE_STOP_PCT = 1.5
LIVE_TRAIL_PCT = 1.25


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def _client():
    from dotenv import load_dotenv
    from alpaca.data.historical import StockHistoricalDataClient
    load_dotenv(ROOT / ".env")
    k, s = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not k or not s:
        sys.exit("No ALPACA_API_KEY / ALPACA_SECRET_KEY in .env")
    return StockHistoricalDataClient(k, s)


def get_bars(client, symbol: str, day: str, feed: str) -> list[dict]:
    """1-minute bars for one symbol on one session, 09:30-16:00 ET. Cached."""
    CACHE.mkdir(exist_ok=True)
    cf = CACHE / f"{symbol}_{day}_{feed}_v2.json"
    if cf.exists():
        return json.loads(cf.read_text())

    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from alpaca.data.enums import DataFeed

    d = datetime.strptime(day, "%Y-%m-%d").date()
    start = datetime.combine(d, MARKET_OPEN, tzinfo=ET)
    end = datetime.combine(d, dtime(16, 0), tzinfo=ET)
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(1, TimeFrameUnit.Minute),
        start=start, end=end,
        feed=DataFeed.SIP if feed == "sip" else DataFeed.IEX,
    )
    try:
        raw = client.get_stock_bars(req)
    except Exception as e:  # noqa: BLE001
        print(f"    ! fetch failed {symbol} {day}: {e}")
        return []
    rows = []
    for b in raw.data.get(symbol, []):
        t = b.timestamp.astimezone(ET)
        rows.append({
            "t": t.strftime("%H:%M"),
            "o": float(b.open), "h": float(b.high),
            "l": float(b.low), "c": float(b.close),
            "v": int(b.volume), "n": int(getattr(b, "trade_count", 0) or 0),
            "vw": float(getattr(b, "vwap", 0) or 0),
        })
    cf.write_text(json.dumps(rows))
    return rows


# --------------------------------------------------------------------------
# replay engine
# --------------------------------------------------------------------------

@dataclass
class Trade:
    date: str
    symbol: str
    side: str
    qty: float
    entry: float
    exit: float
    pnl: float
    pnl_pct: float
    entry_time: str | None = None
    news_exit_time: str | None = None     # v1.16: ADVERSE NEWS EXIT, from logs
    bars: list[dict] = field(default_factory=list)

    @property
    def sign(self) -> int:
        return 1 if self.side == "long" else -1

    @property
    def notional(self) -> float:
        return abs(self.qty * self.entry)

    def pct(self, price: float) -> float:
        return self.sign * (price - self.entry) / self.entry * 100.0

    def dollars(self, price: float) -> float:
        return self.pct(price) / 100.0 * self.notional


def session_vwap(bars: list[dict]) -> dict[str, float]:
    """Running session VWAP keyed by bar time, accumulated from the open.

    Alpaca already returns a per-bar `vwap`; the session VWAP is the
    volume-weighted mean of those. The bot fetches this field on every bar
    today and throws it away.
    """
    out, pv, vol = {}, 0.0, 0.0
    for b in bars:
        px = b.get("vw") or ((b["h"] + b["l"] + b["c"]) / 3.0)
        pv += px * b["v"]
        vol += b["v"]
        out[b["t"]] = pv / vol if vol else px
    return out


def _reaches(bar: dict, level: float, sign: int, mode: str, min_trades: int) -> bool:
    """Did price reach `level` in this bar, in the direction `sign`?"""
    if min_trades and bar["n"] and bar["n"] < min_trades and mode == "touch":
        return False  # too few prints to trust the extreme
    if mode == "close":
        return bar["c"] >= level if sign > 0 else bar["c"] <= level
    return bar["h"] >= level if sign > 0 else bar["l"] <= level


def replay(t: Trade, rule: dict, mode: str, min_trades: int) -> tuple[float, str]:
    """Walk the bars forward from entry. Returns (exit_price, reason).

    Rules understood:
        stop_pct     hard stop, % adverse from entry
        be_at        move stop to breakeven once +be_at% is reached
        scale_at     sell half at +scale_at% (returns blended exit)
        trail_pct / trail_arm   trailing stop of trail_pct, arms at +trail_arm%
    """
    s = t.sign
    vwap = session_vwap(t.bars) if rule.get("vwap_exit") else {}
    # FIX: strictly AFTER the entry minute. The entry bar contains price
    # action from before the fill, which was tripping the stop spuriously.
    bars = [b for b in t.bars if (not t.entry_time or b["t"] > t.entry_time)]
    if not bars:
        # DO NOT return t.exit here. That is the ACTUAL answer, and scoring it
        # against itself gives a free perfect match. On 2026-10-01 that single
        # line turned 33% real agreement into a reported 66%.
        return None, "no-bars"

    # v1.16: a circuit-breaker halt makes the fill unknowable from bars (a
    # resting stop fills on the re-open gap, not at its level). Excluded, not
    # modelled - see has_trading_halt.
    if has_trading_halt(t.bars, after=t.entry_time):
        return None, "halted"

    stop_pct = rule.get("stop_pct", LIVE_STOP_PCT)
    stop = t.entry * (1 - s * stop_pct / 100.0)
    be_at = rule.get("be_at")
    scale_at = rule.get("scale_at")
    trail_pct = rule.get("trail_pct")
    trail_arm = rule.get("trail_arm")

    be_armed = False
    trail_armed = False
    peak = t.entry
    scaled_at_price = None
    scale_weight = 0.0

    for b in bars:
        hi_ex = b["h"] if s > 0 else b["l"]        # favourable extreme
        lo_ex = b["l"] if s > 0 else b["h"]        # adverse extreme
        trust = not (min_trades and b["n"] and b["n"] < min_trades)

        # --- adverse first: within one bar we cannot know the order, so we
        # --- assume the ADVERSE side hits first. Conservative by design.
        if _reaches({"h": lo_ex, "l": lo_ex, "c": b["c"], "n": b["n"]},
                    stop, -s, mode, min_trades):
            px = stop
            if scaled_at_price is not None:
                px = scale_weight * scaled_at_price + (1 - scale_weight) * stop
            reason = ("trail" if trail_armed else
                      "breakeven-stop" if be_armed else "stop")
            return px, reason

        # --- favourable
        if trust:
            if s * (hi_ex - peak) > 0:
                peak = hi_ex

            if scale_at is not None and scaled_at_price is None:
                lvl = t.entry * (1 + s * scale_at / 100.0)
                if _reaches(b, lvl, s, mode, min_trades):
                    scaled_at_price, scale_weight = lvl, 0.5

            if be_at is not None and not be_armed:
                lvl = t.entry * (1 + s * be_at / 100.0)
                if _reaches(b, lvl, s, mode, min_trades):
                    be_armed = True
                    stop = t.entry  # to cost

            if trail_pct is not None:
                # v1.15 SEMANTICS (this was wrong before 2026-10-01): the bot
                # arms off `pos.unrealized_plpc` - the CURRENT price - and only
                # if trailing from THERE still locks >= breakeven. Anchoring on
                # the bar HIGH is the pre-v1.15 bug Ethan already fixed, and it
                # made the replay exit far earlier than the real bot.
                if not trail_armed:
                    cur = b["c"]
                    locks = cur * (1 - s * trail_pct / 100.0)
                    if s * (locks - t.entry) >= 0:
                        trail_armed = True
                        stop = locks if s * (locks - stop) > 0 else stop
                elif trail_armed:
                    cand = peak * (1 - s * trail_pct / 100.0)
                    if s * (cand - stop) > 0:
                        stop = cand

        # --- VWAP trail (Zarattini/Barbon): a long is closed when the bar
        # --- CLOSES back through the session VWAP. Volatility-adaptive by
        # --- construction, and it has no dead band - it is armed from entry.
        if rule.get("vwap_exit") and trust:
            vw = vwap.get(b["t"])
            if vw is not None and s * (b["c"] - vw) < 0:
                px = b["c"]
                if scaled_at_price is not None:
                    px = scale_weight * scaled_at_price + (1 - scale_weight) * b["c"]
                return px, "vwap"

        # v1.16: the live bot's adverse-news exit (exit_on_adverse: true).
        # Checked AFTER stop/trail in the same bar: those are resting orders
        # and fill first. Unmodellable from bars; read from the logs.
        if t.news_exit_time and b["t"] >= t.news_exit_time:
            px = b["c"]
            if scaled_at_price is not None:
                px = scale_weight * scaled_at_price + (1 - scale_weight) * b["c"]
            return px, "news"

        if b["t"] >= EOD_FLATTEN.strftime("%H:%M"):
            px = b["c"]
            if scaled_at_price is not None:
                px = scale_weight * scaled_at_price + (1 - scale_weight) * b["c"]
            return px, "eod"

    px = bars[-1]["c"]
    if scaled_at_price is not None:
        px = scale_weight * scaled_at_price + (1 - scale_weight) * bars[-1]["c"]
    return px, "eod"


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def _entry_times_from_logs() -> dict[tuple[str, str], str]:
    """Entry times from the logs, CORRECTED FOR TIMEZONE.

    trade_features.csv stops at 2026-09-10, so the logs are the only source for
    the later trades. But the logs are NOT all in one timezone: sessions up to
    2026-08-26 were written while the laptop was on CEST, and from 2026-09-08 on
    Eastern. The same CEST/ET split that caused the lost sessions
    (see vps-migration) is baked into the log TEXT.

    Read naively, a CEST log puts an entry at "16:28" — after the 15:50 flatten
    — so no bars remain and the trade silently fails to replay. That hit 71 of
    100 trades on 2026-10-01 and produced a per-trade table that looked like
    66% agreement when the real figure was 33%.

    Self-calibrating fix: the bot logs "Starting equity" AT the 09:30 open, by
    construction. The offset is that line's clock time minus 09:30, rounded to
    whole hours (it is 0h or 6h here, and the line can lag a few minutes).
    """
    import glob as _glob
    import re as _re
    out: dict[tuple[str, str], str] = {}
    pat = _re.compile(r"(\d\d):(\d\d):\d\d,\d+ \[INFO\] ENTER (?:LONG|SHORT) (\w+)")
    anchor = _re.compile(r"(\d\d):(\d\d):\d\d.*Starting equity")
    for f in sorted(_glob.glob(str(ROOT / "logs" / "orb_2026-*.log"))):
        day = Path(f).stem.replace("orb_", "")
        text = open(f, errors="ignore").read()
        a = anchor.search(text)
        if a:
            mins = int(a.group(1)) * 60 + int(a.group(2)) - (9 * 60 + 30)
            shift = round(mins / 60.0) * 60          # whole hours
        else:
            shift = 0
        for ln in text.splitlines():
            m = pat.search(ln)
            if m:
                t = int(m.group(1)) * 60 + int(m.group(2)) - shift
                if 0 <= t < 24 * 60:
                    out[(day, m.group(3))] = f"{t // 60:02d}:{t % 60:02d}"
    return out


def _adverse_exits_from_logs() -> dict[tuple[str, str], str]:
    """ADVERSE NEWS EXIT times from the logs, same timezone correction as
    `_entry_times_from_logs`.

    WHY THIS EXISTS (2026-10-15). `exit_on_adverse: true` in config.yaml closes
    a position when a fresh headline scores against it. The harness had NO
    model for it, so those trades were held to the 15:50 flatten and credited
    with whatever the name did afterwards. Two of thirty holdout trades:

      NKE  2026-10-02 12:24:53  exited 33.18; replay held to 33.845  (+2.00%)
      INTC 2026-10-13 10:22:02  exited 100.41; replay held to 102.39 (+1.96%)

    Bars cannot show this - the trigger is the news feed, not price - but the
    bot logs it as a WARNING with a timestamp, so the logs can.
    """
    import glob as _glob
    import re as _re
    out: dict[tuple[str, str], str] = {}
    pat = _re.compile(
        r"(\d\d):(\d\d):\d\d,\d+ \[WARNING\] ADVERSE NEWS EXIT: "
        r"closing (?:LONG|SHORT) (\w+)"
    )
    anchor = _re.compile(r"(\d\d):(\d\d):\d\d.*Starting equity")
    for f in sorted(_glob.glob(str(ROOT / "logs" / "orb_2026-*.log"))):
        day = Path(f).stem.replace("orb_", "")
        text = open(f, errors="ignore").read()
        a = anchor.search(text)
        if a:
            mins = int(a.group(1)) * 60 + int(a.group(2)) - (9 * 60 + 30)
            shift = round(mins / 60.0) * 60
        else:
            shift = 0
        for ln in text.splitlines():
            m = pat.search(ln)
            if m:
                t = int(m.group(1)) * 60 + int(m.group(2)) - shift
                if 0 <= t < 24 * 60:
                    out[(day, m.group(3))] = f"{t // 60:02d}:{t % 60:02d}"
    return out


def has_trading_halt(bars: list[dict], after: str | None = None,
                     min_gap_min: int = 2, jump_pct: float = 3.0) -> bool:
    """True if the session shows a circuit-breaker halt: a gap in the minute
    stream with a price jump across it.

    WHY (2026-10-15). WFF 2026-10-12 is the single largest trade in the book
    (+183.42, +26.71%) and the difference between a -16.68 trial and a -200.10
    one. Its log reads:

      10:06  TRAIL ACTIVATED: peak +9.97% (locks ~+8.60% if it turns here)
      10:16  TRAIL EXIT: trailing stop filled (peak was +9.97%)
      ...Halted On Circuit Breaker ... Up 92% / 114% / 150% / 240%
      15:50  21.46 -> 27.19  PnL +183.42 (+26.71%)

    The trail fired around +8.6% and FILLED at +26.71%: the resting stop sat
    through repeated halts and filled on a re-open gap far above its level.
    That is a halt artifact, not an exit rule, and no bar-walking replay can
    reproduce it - it accounted for 71% of the harness's entire validation gap.
    Such trades are now EXCLUDED rather than mismodelled, the same treatment
    already given to trades with no bars.
    """
    prev = None
    for b in bars:
        if after is not None and b["t"] < after:
            prev = b
            continue
        if prev is not None:
            ph, pm = int(prev["t"][:2]), int(prev["t"][3:5])
            ch, cm = int(b["t"][:2]), int(b["t"][3:5])
            gap = (ch * 60 + cm) - (ph * 60 + pm)
            if gap >= min_gap_min and prev["c"]:
                if abs(b["o"] - prev["c"]) / prev["c"] * 100.0 >= jump_pct:
                    return True
        prev = b
    return False


def load_trades() -> list[Trade]:
    entry_times: dict[tuple[str, str], str] = _entry_times_from_logs()
    news_exits: dict[tuple[str, str], str] = _adverse_exits_from_logs()
    if FEATURES.exists():
        with open(FEATURES, newline="", encoding="utf-8") as f:
            for r in csv.reader(f):
                if len(r) >= 3 and r[0][:2] == "20":
                    entry_times.setdefault((r[0], r[2]), r[1][:5])
    out = []
    with open(HISTORY, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(Trade(
                date=r["date"], symbol=r["symbol"], side=r["side"],
                qty=float(r["qty"]), entry=float(r["entry_price"]),
                exit=float(r["exit_price"]), pnl=float(r["pnl"]),
                pnl_pct=float(r["pnl_pct"]),
                entry_time=entry_times.get((r["date"], r["symbol"])),
                news_exit_time=news_exits.get((r["date"], r["symbol"])),
            ))
    return out


def attach_bars(trades: list[Trade], feed: str) -> list[Trade]:
    client = _client()
    ok = []
    for i, t in enumerate(trades, 1):
        t.bars = get_bars(client, t.symbol, t.date, feed)
        if t.bars:
            ok.append(t)
        print(f"\r  bars {i}/{len(trades)}  {t.symbol} {t.date}   ", end="", flush=True)
    print(f"\n  {len(ok)} of {len(trades)} trades have bars")
    n_et = sum(1 for t in ok if t.entry_time)
    print(f"  {n_et} have a logged entry time; {len(ok)-n_et} replay from the open "
          f"(their result is less trustworthy)")
    return ok


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def actual_exit_types() -> dict[tuple[str, str], str]:
    """How each trade REALLY ended, from the bot's own logs.

    Note the asymmetry that shapes this whole diagnostic: the bot timestamps
    only TRAIL EXIT and TIME STOP. A broker stop fill is never logged — the
    position is simply gone at the next poll. So exit TIME is unavailable for
    the 42 stop-outs, and exit PRICE (in trade_history) is the only key that
    covers all 100 trades.
    """
    import glob as _glob
    import re as _re
    out: dict[tuple[str, str], str] = {}
    for f in sorted(_glob.glob(str(ROOT / "logs" / "orb_2026-*.log"))):
        day = Path(f).stem.replace("orb_", "")
        for ln in open(f, errors="ignore"):
            m = _re.search(r"TRAIL EXIT (\w+)", ln)
            if m:
                out[(day, m.group(1))] = "trail"
            m = _re.search(r"TIME STOP (\w+)", ln)
            if m:
                out.setdefault((day, m.group(1)), "time")
            # v1.16: the adverse-news exit IS logged, as a WARNING. Without
            # this it was being labelled "eod" by the pnl_pct fallback and the
            # harness then "agreed" with itself about the wrong exit rule.
            m = _re.search(
                r"ADVERSE NEWS EXIT: closing (?:LONG|SHORT) (\w+)", ln)
            if m:
                out.setdefault((day, m.group(1)), "news")
    return out


def per_trade(trades, min_trades, mode="touch"):
    """Does the replay reproduce each trade INDIVIDUALLY?

    The aggregate can match by accident — offsetting errors cancel. This asks
    the sharper question, and segments by how the trade really ended, because
    the exit types are not equally hard:

      eod   - should be TRIVIAL to reproduce (just the 15:50 close). If these
              do not match, there is a plain bug, not an ambiguity.
      trail - moderately hard.
      stop  - dominated by intra-bar sequence, which minute bars cannot resolve.

    If eod reproduces and stop does not, the failure is localised to exactly
    the thing I claimed it was, and the harness may still be usable for the
    subset of trades that do not stop out.
    """
    import statistics as _st
    real = actual_exit_types()
    rule = {"stop_pct": LIVE_STOP_PCT, "trail_pct": LIVE_TRAIL_PCT,
            "trail_arm": LIVE_TRAIL_PCT}
    rows = []
    unreachable = 0
    for t in trades:
        # v1.16: was hardcoded "close" while `touch` is the decision mode
        # (validated 2026-10-01). The dollar sum of the printed per-trade
        # errors reconciled to the CLOSE validation gap, not the touch one,
        # which is how the mismatch surfaced.
        px, why = replay(t, rule, mode, min_trades)
        kind = real.get((t.date, t.symbol))
        if kind is None:
            kind = ("news" if t.news_exit_time
                    else "stop" if t.pnl_pct <= -1.35 else "eod")
        if px is None:                 # never score an unreplayable trade
            unreachable += 1
            continue
        err = (px - t.exit) / t.entry * 100.0        # in % of entry price
        rows.append((kind, why, err, t))
    if unreachable:
        print(f"\n  !! {unreachable} trade(s) had no bars after entry and are "
              f"EXCLUDED from everything below.")

    print(f"\n=== PER-TRADE AGREEMENT (exit price, % of entry) [mode={mode}] ===")
    print(f"  the aggregate can match by accident. This cannot.")
    print(f"  scored on {len(rows)} of {len(trades)} trades.\n")
    print(f"  {'actual exit':>12} {'n':>4} {'match<0.1%':>11} {'<0.5%':>7} "
          f"{'>0.5%':>7} {'median err':>11}")
    for kind in ("eod", "news", "trail", "time", "stop"):
        g = [r for r in rows if r[0] == kind]
        if not g:
            continue
        e = [abs(r[2]) for r in g]
        print(f"  {kind:>12} {len(g):4d} {sum(1 for x in e if x < 0.1):11d} "
              f"{sum(1 for x in e if x < 0.5):7d} {sum(1 for x in e if x >= 0.5):7d} "
              f"{_st.median(e):10.2f}%")
    e = [abs(r[2]) for r in rows]
    print(f"  {'ALL':>12} {len(rows):4d} {sum(1 for x in e if x < 0.1):11d} "
          f"{sum(1 for x in e if x < 0.5):7d} {sum(1 for x in e if x >= 0.5):7d} "
          f"{_st.median(e):10.2f}%")

    print("\n=== WHAT THE REPLAY CALLS IT vs WHAT IT REALLY WAS ===")
    import collections as _c
    cm = _c.Counter((r[0], r[1]) for r in rows)
    print(f"  {'actual':>10} -> {'replay':<16} n")
    for (a, b), n in sorted(cm.items(), key=lambda x: -x[1]):
        flag = "" if a == b else "   <-- disagrees"
        print(f"  {a:>10} -> {b:<16} {n:3d}{flag}")

    print("\n=== THE 10 WORST DISAGREEMENTS ===")
    for kind, why, err, t in sorted(rows, key=lambda r: -abs(r[2]))[:10]:
        print(f"  {t.date} {t.symbol:6s} actual {kind:5s} {t.pnl_pct:+6.2f}%  "
              f"replay {why:14s} err {err:+7.2f}%")
    print("\n  Read `eod` first. If those do not reproduce, fix that before")
    print("  believing anything about stops.")


def total(trades, rule, mode, min_trades):
    """Sum over trades the replay could actually evaluate. Trades it cannot
    reach are EXCLUDED, never filled in with their real result."""
    tot = 0.0
    n = 0
    for t in trades:
        px, _ = replay(t, rule, mode, min_trades)
        if px is None:
            continue
        tot += t.dollars(px)
        n += 1
    return tot, n


def validate(trades, min_trades):
    """Can the harness reproduce what actually happened? If not, stop."""
    print("\n=== HARNESS VALIDATION ===")
    print("Replaying the LIVE rules (stop 1.5, trail 1.25 arming at 1.25) and")
    print("comparing with the P&L the bot actually booked.\n")
    rule = {"stop_pct": LIVE_STOP_PCT, "trail_pct": LIVE_TRAIL_PCT,
            "trail_arm": LIVE_TRAIL_PCT}
    for mode in ("touch", "close"):
        sim, n = total(trades, rule, mode, min_trades)
        ev = [t for t in trades
              if replay(t, rule, mode, min_trades)[0] is not None]
        actual = sum(t.pnl for t in ev)
        print(f"  {mode:6s}  replay {sim:+9.2f}   actual {actual:+9.2f}   "
              f"diff {sim-actual:+8.2f}   (on {n} of {len(trades)} trades)")
    skipped = len(trades) - n
    if skipped:
        print(f"\n  {skipped} trade(s) could NOT be replayed and are EXCLUDED, not")
        print("  filled in with their real result. If this number is large the")
        print("  comparison above is on a biased subset and means little.")
    print("\n  A large gap here means the replay does not model what the bot does")
    print("  (entry timing, partial fills, the IEX/SIP difference) and NOTHING")
    print("  below should be believed until it is explained.")


def grid(trades, min_trades):
    actual = sum(t.pnl for t in trades)
    n = len(trades)

    def row(label, rule):
        a, _ = total(trades, rule, "touch", min_trades)
        b, _ = total(trades, rule, "close", min_trades)
        print(f"  {label:24s} {a:+9.2f} {b:+9.2f}   {(a-actual)/n:+7.3f} "
              f"{(b-actual)/n:+7.3f}")

    hdr = f"  {'rule':24s} {'touch':>9s} {'close':>9s}   {'d/trade':>7s} {'d/trade':>7s}"

    print(f"\n=== ACTUAL: {actual:+.2f} over {n} trades ({actual/n:+.3f}/trade) ===")

    print("\n=== THE TRAIL ITSELF  ***  the source paper uses NO trailing stop  ***")
    print("  Zarattini/Barbon/Aziz hold to the close or the stop, nothing else, and")
    print("  report that adding a PROFIT TARGET is detrimental. A trailing stop is a")
    print("  soft profit target: it caps every winner at (peak - trail). This bot's")
    print("  trail is its own invention, validated in-sample only, by the same sweep")
    print("  method that is 0-for-7 out of sample.")
    print(hdr)
    row("stop 1.5, NO TRAIL (paper)", {"stop_pct": LIVE_STOP_PCT})
    row("stop 1.5 + trail (LIVE)", {"stop_pct": LIVE_STOP_PCT,
                                    "trail_pct": LIVE_TRAIL_PCT,
                                    "trail_arm": LIVE_TRAIL_PCT})
    print("  ^ THE most important two rows in this output. If no-trail wins, the")
    print("    give-back measured in ANALYSIS-2026-09-29 has a one-line fix.")

    print("\n=== E6  BREAKEVEN STOP (the +207 / -280 question) ===")
    print(hdr)
    for T in (0.75, 1.0, 1.25, 1.5, 2.0):
        row(f"stop 1.5 + BE at +{T}", {"stop_pct": LIVE_STOP_PCT, "be_at": T,
                                       "trail_pct": LIVE_TRAIL_PCT,
                                       "trail_arm": LIVE_TRAIL_PCT})

    print("\n=== E5  SCALE OUT HALF ===")
    print(hdr)
    for X in (1.0, 1.25, 1.5, 2.0):
        row(f"stop 1.5 + half at +{X}", {"stop_pct": LIVE_STOP_PCT, "scale_at": X,
                                         "trail_pct": LIVE_TRAIL_PCT,
                                         "trail_arm": LIVE_TRAIL_PCT})

    print("\n=== STOP WIDTH  (1.25 vs 1.50 vs wider) ===")
    print(hdr)
    for W in (1.0, 1.25, 1.5, 2.0, 2.5):
        row(f"stop {W}", {"stop_pct": W, "trail_pct": LIVE_TRAIL_PCT,
                          "trail_arm": LIVE_TRAIL_PCT})

    print("\n=== TRAIL WIDTH / ARMING  (does arming earlier close the dead band?) ===")
    print(hdr)
    for W in (0.5, 0.75, 1.0, 1.25):
        row(f"trail {W} arms at +{W}", {"stop_pct": LIVE_STOP_PCT,
                                        "trail_pct": W, "trail_arm": W})

    print("\n=== VWAP TRAIL  (the literature's exit; the bot has no VWAP at all) ===")
    print(hdr)
    row("stop 1.5 + VWAP exit", {"stop_pct": LIVE_STOP_PCT, "vwap_exit": True})
    row("stop 1.5 + VWAP + trail", {"stop_pct": LIVE_STOP_PCT, "vwap_exit": True,
                                    "trail_pct": LIVE_TRAIL_PCT, "trail_arm": LIVE_TRAIL_PCT})
    row("VWAP only (no hard stop)", {"stop_pct": 99.0, "vwap_exit": True})

    print("\n  touch = optimistic (wick fills). close = conservative.")
    print("  A change that only works under `touch` is a wick artifact. Discard it.")
    print("  Nothing here is a decision. Pick a mechanism, pre-register the")
    print("  criteria, then test it out of sample.")


def robust(trades, min_trades):
    """The tests that have killed nine findings in this project.

    A single grid number means nothing on n=38. A candidate must survive:
      - dropping the 2 and 3 best trades (concentration)
      - both halves of the sample separately (stability)
      - BOTH fill modes (not a wick artifact)
    """
    import statistics as _st
    LIVE = {"stop_pct": LIVE_STOP_PCT, "trail_pct": LIVE_TRAIL_PCT,
            "trail_arm": LIVE_TRAIL_PCT}
    cands = [
        ("LIVE (stop 1.5 + trail)", LIVE),
        ("NO TRAIL (paper)", {"stop_pct": LIVE_STOP_PCT}),
        ("trail 0.75 arm 0.75", {"stop_pct": LIVE_STOP_PCT,
                                 "trail_pct": 0.75, "trail_arm": 0.75}),
        ("stop 1.0 + trail", {"stop_pct": 1.0, "trail_pct": LIVE_TRAIL_PCT,
                              "trail_arm": LIVE_TRAIL_PCT}),
        ("stop 1.0, NO TRAIL", {"stop_pct": 1.0}),
    ]

    def pnl_list(ts, rule, mode):
        out = []
        for t in ts:
            px, _ = replay(t, rule, mode, min_trades)
            if px is not None:
                out.append(t.dollars(px))
        return out

    print("\n" + "=" * 78)
    print("ROBUSTNESS — the tests that killed the last nine findings")
    print("=" * 78)
    base, base2, base3 = {}, {}, {}
    for m in ("touch", "close"):
        v = sorted(pnl_list(trades, LIVE, m))
        base[m], base2[m], base3[m] = sum(v), sum(v[:-2]), sum(v[:-3])
    half = len(trades) // 2
    for name, rule in cands:
        print(f"\n  {name}")
        for mode in ("touch", "close"):
            v = pnl_list(trades, rule, mode)
            d = sum(v) - base[mode]
            v2 = sorted(v)[:-2]
            v3 = sorted(v)[:-3]
            a = pnl_list(trades[:half], rule, mode)
            b = pnl_list(trades[half:], rule, mode)
            ab = sum(a) - sum(pnl_list(trades[:half], LIVE, mode))
            bb = sum(b) - sum(pnl_list(trades[half:], LIVE, mode))
            sd = _st.stdev(v) if len(v) > 1 else 0
            t = (sum(v) / len(v)) / (sd / len(v) ** 0.5) if sd else 0
            # the DELTA vs live is what matters. Raw ex-topN totals invite
            # misreading: a candidate can look like it collapses when live
            # collapsed just as hard.
            print(f"    {mode:6s} delta vs live: full {d:+7.2f}  "
                  f"ex-top2 {sum(v2)-base2[mode]:+7.2f}  "
                  f"ex-top3 {sum(v3)-base3[mode]:+7.2f}   t={t:+5.2f}")
            print(f"           half1 {ab:+7.2f}   half2 {bb:+7.2f}  "
                  f"{'BOTH SAME SIGN' if ab * bb > 0 else '** SIGN FLIPS **'}")
    print("\n  A candidate is only interesting if it beats LIVE in BOTH modes,")
    print("  survives ex-top2 AND ex-top3, and does not flip sign across halves.")
    print(f"  Remember n={len(trades)}. Two trades can carry anything here.")


def dump(trades, min_trades, path="replay_trades.csv"):
    rule = {"stop_pct": LIVE_STOP_PCT, "trail_pct": LIVE_TRAIL_PCT,
            "trail_arm": LIVE_TRAIL_PCT}
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "symbol", "side", "entry_time", "actual_pnl",
                    "actual_pct", "replay_pnl", "replay_reason",
                    "true_mfe_pct", "true_mae_pct", "mfe_first",
                    "vwap_dist_at_entry_pct", "or_pct", "stop_in_range_units"])
        for t in trades:
            px, why = replay(t, rule, "touch", min_trades)
            bars = [b for b in t.bars
                    if (not t.entry_time or b["t"] > t.entry_time)]
            if not bars:
                continue
            mfe = max(t.pct(b["h"] if t.sign > 0 else b["l"]) for b in bars)
            mae = min(t.pct(b["l"] if t.sign > 0 else b["h"]) for b in bars)
            i_mfe = max(range(len(bars)),
                        key=lambda i: t.pct(bars[i]["h"] if t.sign > 0 else bars[i]["l"]))
            i_mae = min(range(len(bars)),
                        key=lambda i: t.pct(bars[i]["l"] if t.sign > 0 else bars[i]["h"]))
            vw = session_vwap(t.bars).get(bars[0]["t"])
            vd = t.sign * (t.entry - vw) / t.entry * 100 if vw else ""
            day = [b for b in t.bars if b["t"] < "09:45"]
            orw = ((max(b["h"] for b in day) - min(b["l"] for b in day))
                   / t.entry * 100) if day else 0
            w.writerow([t.date, t.symbol, t.side, t.entry_time or "",
                        f"{t.pnl:.2f}", f"{t.pnl_pct:.3f}",
                        f"{t.dollars(px):.2f}", why,
                        f"{mfe:.3f}", f"{mae:.3f}", int(i_mfe < i_mae),
                        f"{vd:.3f}" if vd != "" else "",
                        f"{orw:.3f}", f"{LIVE_STOP_PCT/orw:.2f}" if orw else ""])
    print(f"\n  per-trade detail -> {path}")
    print("  `mfe_first` is the column the summary stats never had: 1 means the")
    print("  high came before the low, so the trade was winnable and gave it back.")
    print("  `vwap_dist_at_entry_pct` answers the open question about the VWAP exit:")
    print("     if it is mostly > 1.5, VWAP is a WIDER stop than the current one;")
    print("     if mostly < 1.5, it is tighter. Nobody knows which yet.")
    print("  `stop_in_range_units` = 1.5% / opening-range width. The live sample")
    print("     spans 0.08 to 57 - the same stop means wildly different risk.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--feed", default="sip", choices=["sip", "iex"])
    p.add_argument("--min-trades", type=int, default=3,
                   help="a bar with fewer prints cannot set an extreme (v1.18 guard)")
    p.add_argument("--validate", action="store_true", help="harness check only")
    p.add_argument("--robust", action="store_true",
                   help="drop-top-N and split-half on the leading candidates")
    p.add_argument("--per-trade", action="store_true",
                   help="per-trade agreement, segmented by real exit type")
    p.add_argument("--since", default="2026-08-19",
                   help="only replay trades from this date (default 2026-08-19, "
                        "when the trail first actually worked). The 100-trade "
                        "history spans several configs; replaying today's rules "
                        "against July's strategy compares different things. "
                        "Pass --since 1970-01-01 to include everything.")
    p.add_argument("--all", action="store_true", help="validation + full grid + dump")
    a = p.parse_args()

    trades = load_trades()
    alln = len(trades)
    trades = [t for t in trades if t.date >= a.since]
    print(f"{alln} trades in {HISTORY.name}; feed={a.feed}, "
          f"min-trades={a.min_trades}")
    print(f"replaying {len(trades)} from {a.since} onward "
          f"({alln - len(trades)} excluded as a different config regime)")
    if a.since >= "2026-10-02":
        print(f"  E12 HOLDOUT PROGRESS: {len(trades)} of 30 trades "
              f"({'ready' if len(trades) >= 30 else 'NOT READY — do not read the result yet'})")
    print("""
  WHY THE FILTER: the 100-trade history is not one strategy. Up to 2026-07-31
  the bot ran stop 3.0 with a 3-5% take-profit and a 180-minute time stop;
  stop became 1.5 and the target came off on 08-03; the entry window dropped
  180 -> 60 around 08-06; and the trail did not actually work until 08-19.
  Replaying TODAY's rules against those trades compares two different bots.

  WARNING, and it cuts against me: P&L by start date is
     from 08-03  n=62  -0.85/trade
     from 08-19  n=38  -0.12/trade
     from 08-25  n=23  +0.84/trade
  Picking the window with the best number is the subset trap this project has
  been burned by seven times. 08-19 is chosen because that is when the trail
  began working - a MECHANICAL reason - not because of its P&L. The P&L of any
  chosen window is NOT evidence of an edge; n=38 at -0.12 is still zero.""")
    if not trades:
        NEEDED = 30          # E12's pre-registered holdout size
        print(f"\n  HOLDOUT IS EMPTY — 0 trades on/after {a.since}.")
        print(f"  E12 needs {NEEDED}. At ~1-2 trades/session that is 20-30 sessions.\n")
        print("  Two things must both happen before they show up here:")
        print("    1. the bot has to actually take trades, and")
        print("    2. you must SYNC state/trade_history.csv AND logs/ from the VPS —")
        print("       this script reads the LOCAL copies, which lag the live bot.")
        print("\n  Nothing is wrong. Come back when the sessions have run.")
        sys.exit(0)

    trades = attach_bars(trades, a.feed)
    if not trades:
        sys.exit("Bars could not be fetched for any trade in the window.")

    validate(trades, a.min_trades)
    if a.per_trade or a.all:
        per_trade(trades, a.min_trades)
    if a.robust or a.all:
        robust(trades, a.min_trades)
    if a.all:
        grid(trades, a.min_trades)
        dump(trades, a.min_trades)


if __name__ == "__main__":
    main()
