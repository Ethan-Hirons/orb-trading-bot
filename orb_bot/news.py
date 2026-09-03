"""News scanning: pre-market scoring + intraday rescans.

Honest note: this is a lightweight keyword-based sentiment gauge, not an AI
analyst. It reliably flags clearly positive/negative headlines (beats, upgrades,
guidance cuts, investigations) and is used as a *filter and tie-breaker*, not a
guarantee of direction. The scoring is deliberately simple so you can read and
tune the word lists yourself.

Two jobs:
  1. Pre-market: score the last `lookback_hours` of headlines per candidate to
     set each target's directional bias (see selection.py).
  2. Intraday: every `rescan_interval_seconds`, fetch only *new* articles for
     the day's targets and open positions. Fresh news can update a target's
     bias, and strongly adverse fresh news can trigger an early exit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import Config, NewsConfig
from .logutil import get_logger

log = get_logger()

try:
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest

    _NEWS_AVAILABLE = True
except Exception:
    _NEWS_AVAILABLE = False

# Simple finance sentiment lexicons. Extend these freely.
# Regular words count 1; STRONG words count 2 (they move small-caps hard).
POSITIVE = {
    "beat", "beats", "surge", "surges", "soar", "soars", "jump", "jumps",
    "rally", "rallies", "upgrade", "upgraded", "raises", "raised", "record",
    "strong", "growth", "profit", "outperform", "buy", "bullish", "approval",
    "approved", "wins", "win", "gains", "gain", "expands", "boost", "boosted",
    "tops", "top", "positive", "breakthrough", "partnership", "acquires",
    "guidance", "dividend", "buyback", "awarded", "surpasses", "exceeds",
    "milestone", "patent", "clearance", "expansion", "upbeat", "beats",
}
STRONG_POSITIVE = {
    "acquisition", "merger", "acquired", "takeover", "fda", "blockbuster",
}
NEGATIVE = {
    "miss", "misses", "plunge", "plunges", "drop", "drops", "fall", "falls",
    "slump", "downgrade", "downgraded", "cuts", "cut", "warning", "warns",
    "weak", "loss", "losses", "lawsuit", "probe", "recall",
    "sell", "bearish", "decline", "declines", "slashes",
    "slash", "halts", "halt", "delay", "delayed", "layoffs", "resign",
    "resigns", "negative", "disappointing", "shortfall", "default",
    "downturn", "misled", "restatement", "writedown", "impairment",
}
STRONG_NEGATIVE = {
    "bankruptcy", "fraud", "investigation", "delisting", "delisted",
    "offering", "dilution", "subpoena", "insolvency", "scandal",
}

_WORD = re.compile(r"[a-z']+")


@dataclass
class NewsSignal:
    symbol: str
    score: float  # -1 (very negative) .. +1 (very positive)
    n_articles: int
    latest_headline: str


def score_text(text: str) -> float:
    words = _WORD.findall(text.lower())
    if not words:
        return 0.0
    pos = sum(1 for w in words if w in POSITIVE) + 2 * sum(
        1 for w in words if w in STRONG_POSITIVE
    )
    neg = sum(1 for w in words if w in NEGATIVE) + 2 * sum(
        1 for w in words if w in STRONG_NEGATIVE
    )
    if pos == 0 and neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg)


def should_exit_on_news(position_side: str, fresh_score: float, cfg: NewsConfig) -> bool:
    """Pure decision: does strongly adverse fresh news warrant an early exit?

    position_side: "long" or "short". fresh_score: avg score of *new* articles.
    """
    if not cfg.exit_on_adverse:
        return False
    threshold = abs(cfg.adverse_exit_score)
    if threshold <= 0:
        return False
    if position_side == "long":
        return fresh_score <= -threshold
    if position_side == "short":
        return fresh_score >= threshold
    return False


class NewsScanner:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = None
        self._seen_ids: set[str] = set()  # article ids already scored intraday
        if _NEWS_AVAILABLE and cfg.news.enabled:
            creds = cfg.credentials
            try:
                self.client = NewsClient(creds.api_key, creds.secret_key)
            except Exception as e:  # noqa: BLE001
                log.warning("News client unavailable: %s", e)

    def _fetch(self, symbol: str, start: datetime) -> list:
        try:
            req = NewsRequest(symbols=symbol, start=start, limit=15)
            res = self.client.get_news(req)
        except Exception as e:  # noqa: BLE001
            log.warning("get_news failed for %s: %s", symbol, e)
            return []
        raw = getattr(res, "data", None)
        if isinstance(raw, dict) and "news" in raw:
            return raw["news"] or []
        return getattr(res, "news", []) or []

    @staticmethod
    def _score_articles(symbol: str, articles: list) -> NewsSignal:
        if not articles:
            return NewsSignal(symbol, 0.0, 0, "")
        scores = []
        latest = ""
        for a in articles:
            headline = getattr(a, "headline", "") or ""
            summary = getattr(a, "summary", "") or ""
            scores.append(score_text(f"{headline}. {summary}"))
            if not latest:
                latest = headline
        avg = sum(scores) / len(scores) if scores else 0.0
        return NewsSignal(symbol, round(avg, 3), len(articles), latest)

    # ----- pre-market -----

    def score_symbol(self, symbol: str) -> NewsSignal:
        if not self.client:
            return NewsSignal(symbol, 0.0, 0, "")
        start = datetime.now(timezone.utc) - timedelta(
            hours=self.cfg.news.lookback_hours
        )
        return self._score_articles(symbol, self._fetch(symbol, start))

    def score_symbols(self, symbols: list[str]) -> dict[str, NewsSignal]:
        return {s: self.score_symbol(s) for s in symbols}

    # ----- intraday -----

    def mark_seen(self, symbols: list[str]) -> None:
        """Record current articles as seen so the first intraday rescan only
        reacts to genuinely new news (call once after the pre-market scan)."""
        if not self.client:
            return
        start = datetime.now(timezone.utc) - timedelta(
            hours=self.cfg.news.lookback_hours
        )
        for s in symbols:
            for a in self._fetch(s, start):
                aid = str(getattr(a, "id", "") or getattr(a, "headline", ""))
                if aid:
                    self._seen_ids.add(aid)

    def fresh_signal(self, symbol: str, since: datetime) -> NewsSignal:
        """Score only articles we have not seen before, published since `since`."""
        if not self.client:
            return NewsSignal(symbol, 0.0, 0, "")
        fresh = []
        for a in self._fetch(symbol, since):
            aid = str(getattr(a, "id", "") or getattr(a, "headline", ""))
            if aid and aid in self._seen_ids:
                continue
            if aid:
                self._seen_ids.add(aid)
            fresh.append(a)
        return self._score_articles(symbol, fresh)
