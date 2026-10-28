"""Market-data adapters.

Thin wrappers over the Alpaca SDK that return PLAIN DICTS, never SDK objects.
Two reasons: the store is JSON-only (no pickle, no version coupling), and the
bar schema here is byte-identical to replay.py's so anything cached by the
pipeline can be handed straight to the existing replay/backtest code.

Keys are read from .env exactly as the bot reads them - nothing is ever
passed in or printed.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from . import REPO

ET = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)


def client():
    """Alpaca historical data client, keys from .env (same as the bot)."""
    from dotenv import load_dotenv
    from alpaca.data.historical import StockHistoricalDataClient
    load_dotenv(REPO / ".env")
    k, s = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not k or not s:
        sys.exit("No ALPACA_API_KEY / ALPACA_SECRET_KEY in .env")
    return StockHistoricalDataClient(k, s)


def _feed(feed: str):
    from alpaca.data.enums import DataFeed
    return DataFeed.SIP if feed == "sip" else DataFeed.IEX


def minute_bars(cli, symbol: str, day: str, feed: str = "sip") -> list[dict]:
    """1-minute bars for one session, 09:30-16:00 ET.

    Schema matches replay.get_bars exactly: t/o/h/l/c/v/n/vw.
    `n` (trade count) matters - it drives the v1.18 thin-bar guard.
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    d = datetime.strptime(day, "%Y-%m-%d").date()
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(1, TimeFrameUnit.Minute),
        start=datetime.combine(d, MARKET_OPEN, tzinfo=ET),
        end=datetime.combine(d, MARKET_CLOSE, tzinfo=ET),
        feed=_feed(feed),
    )
    raw = cli.get_stock_bars(req)
    out = []
    for b in raw.data.get(symbol, []):
        t = b.timestamp.astimezone(ET)
        out.append({"t": t.strftime("%H:%M"),
                    "o": float(b.open), "h": float(b.high),
                    "l": float(b.low), "c": float(b.close),
                    "v": int(b.volume),
                    "n": int(getattr(b, "trade_count", 0) or 0),
                    "vw": float(getattr(b, "vwap", 0) or 0)})
    return out


def daily_bars(cli, symbol: str, start: str, end: str,
               feed: str = "sip") -> list[dict]:
    """Daily bars - what the screener needs for gaps and volume baselines."""
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(1, TimeFrameUnit.Day),
        start=datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=ET),
        end=datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=ET),
        feed=_feed(feed),
    )
    raw = cli.get_stock_bars(req)
    return [{"d": b.timestamp.astimezone(ET).strftime("%Y-%m-%d"),
             "o": float(b.open), "h": float(b.high), "l": float(b.low),
             "c": float(b.close), "v": int(b.volume)}
            for b in raw.data.get(symbol, [])]


def news(symbol: str, day: str, limit: int = 50) -> list[dict]:
    """Headlines published in the 24h before the open on `day`.

    NOTE ON LOOKAHEAD: this is for DESCRIBING a past session, not for
    scoring one. Any model that reads a headline from a past date already
    knows what happened next, so news features must never be fed to a
    backtested decision rule. See ANALYSIS-2026-10-25 §4 and the 2026-09-29
    note on LLM agents in the trade decision.
    """
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    cli = NewsClient(os.getenv("ALPACA_API_KEY"),
                     os.getenv("ALPACA_SECRET_KEY"))
    d = datetime.strptime(day, "%Y-%m-%d").date()
    req = NewsRequest(symbols=symbol,
                      start=datetime.combine(d - timedelta(days=1),
                                             MARKET_OPEN, tzinfo=ET),
                      end=datetime.combine(d, MARKET_OPEN, tzinfo=ET),
                      limit=limit)
    raw = cli.get_news(req)
    items = getattr(raw, "data", {}).get("news", []) or []
    return [{"t": str(getattr(n, "created_at", "")),
             "headline": getattr(n, "headline", ""),
             "source": getattr(n, "source", "")} for n in items]
