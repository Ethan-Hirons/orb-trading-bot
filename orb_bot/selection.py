"""Combine screener candidates + news into the day's ranked target list.

Produces up to `selection.max_targets` targets, each with a directional bias:
  - "long"  : news and/or gap point up  -> only take long breakouts
  - "short" : news and/or gap point down -> only take short breakouts
  - "any"   : no strong signal -> take the breakout in whichever direction it goes
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Config
from .logutil import get_logger
from .news import NewsSignal
from .screener import Candidate

log = get_logger()


@dataclass
class Target:
    symbol: str
    bias: str  # "long" | "short" | "any"
    score: float
    gap_pct: float
    sentiment: float
    n_articles: int
    reason: str
    rel_volume: float = 0.0

    def as_state_dict(self) -> dict:
        """Serializable form for DailyState.targets (features logged at entry)."""
        return {
            "symbol": self.symbol, "bias": self.bias, "score": self.score,
            "gap_pct": self.gap_pct, "sentiment": self.sentiment,
            "n_articles": self.n_articles, "rel_volume": self.rel_volume,
        }


# ---------- same-underlying grouping (v1.7) ----------
# 2026-07-23: SMCX + SMCL + SNXX (all leveraged SMCI trackers) reversed
# together for 3 correlated stops. Leveraged/inverse single-stock ETPs name
# their underlying ticker in the asset title, so we group by that.

# Marks a name as a leveraged/inverse/tracker product (not a common stock).
_LEVERAGED_NAME = re.compile(
    r"\b\d+(?:\.\d+)?X\b|\b(?:BULL|BEAR|INVERSE|LEVERAGED|LONG|SHORT|ULTRA\w*)\b",
    re.I,
)
# All-caps tokens in ETP titles that are never the underlying ticker.
_UNDERLYING_STOPWORDS = {
    "ETF", "ETN", "ETP", "ETFS", "FUND", "TRUST", "SHARES", "INDEX", "DAILY",
    "TARGET", "LONG", "SHORT", "BULL", "BEAR", "ULTRA", "PRO", "MAX", "REX",
    "INC", "CORP", "LTD", "PLC", "US", "USD", "NYSE", "AMEX", "II", "III",
}


def underlying_key(symbol: str, asset_name: str | None) -> str:
    """Best-effort group key: leveraged/inverse ETPs map to the underlying
    ticker named in their asset title (SMCX/SMCL/SNXX -> SMCI, TQQQ -> QQQ);
    anything else (or no name available) is its own key. Fail-safe: an
    unrecognized name just means no grouping, same as today."""
    name = (asset_name or "").strip()
    if not name or not _LEVERAGED_NAME.search(name):
        return symbol
    for tok in re.findall(r"\b[A-Z]{2,5}\b", name):
        if tok not in _UNDERLYING_STOPWORDS:
            return tok
    return symbol


def cap_per_underlying(
    targets: list[Target],
    asset_names: dict[str, str],
    cap: int,
    taken: set[str] | None = None,
) -> list[Target]:
    """Keep at most `cap` targets per underlying (targets must be score-sorted;
    the strongest survives). `taken` = underlying keys already claimed by
    existing targets/positions (those count as full). cap <= 0 disables."""
    if cap <= 0:
        return targets
    counts: dict[str, int] = {k: cap for k in (taken or set())}
    kept: list[Target] = []
    for t in targets:
        key = underlying_key(t.symbol, asset_names.get(t.symbol))
        if counts.get(key, 0) >= cap:
            log.info(
                "DUP-UNDERLYING %s: same underlying %s as a stronger target; skipping",
                t.symbol, key,
            )
            continue
        counts[key] = counts.get(key, 0) + 1
        kept.append(t)
    return kept


# ---------- correlated-theme grouping (v1.14) ----------
# 2026-08-21 and 08-24: the same-underlying cap above did its job and still let
# through five simultaneous crypto bets (MARA, CONL, ETHA, BITO, IBIT on 08-21;
# BMNR, ETHA, IBIT, BITO on 08-24). By `underlying_key` those are five different
# underlyings; by any economic reading they are ONE position. Over those two
# days crypto-linked names lost 62.72 across nine trades while everything else
# made +11.98 across three.
#
# Deliberately NOT a keyword list of themes ("BITCOIN", "ETHER", ...): that
# catches the theme that already hurt us and silently rots for the next one.
# Measured co-movement generalises to themes nobody has named yet.
#
# v1.11.1 lesson applies throughout: a symbol with unknown/short history is
# NEVER blocked. An absent measurement must not become an implicit veto.

_MIN_CORR_OBS = 10  # fewer overlapping daily returns than this => don't judge
# Total squared deviation below this counts as "flat". Daily returns are ~1e-2,
# so a real series lands near 1e-3..1e-2 over 30 days; 1e-12 is far below any
# genuine signal and far above floating-point residue on a constant series.
_FLAT_VAR_EPS = 1e-12


def pearson(a: list[float], b: list[float]) -> float | None:
    """Pearson correlation, or None when it is not meaningfully defined
    (too few points, or either series is flat)."""
    n = min(len(a), len(b))
    if n < _MIN_CORR_OBS:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    # Guard with a tolerance, not `<= 0`: a genuinely flat series (a pegged or
    # barely-traded name) leaves a residual variance around 1e-36 from
    # floating-point means, which would sail past a zero check and yield a
    # meaningless correlation from pure rounding noise.
    if va < _FLAT_VAR_EPS or vb < _FLAT_VAR_EPS:
        return None
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / (va ** 0.5 * vb ** 0.5)


def aligned_returns(
    ra: dict[str, float], rb: dict[str, float]
) -> tuple[list[float], list[float]]:
    """Two return series aligned on the dates they share.

    Aligning on DATE rather than list position matters: candidates are often
    recent listings or newly-created ETPs, so their histories start on
    different days and positional alignment would silently compare Tuesday to
    Thursday and produce a meaningless correlation.
    """
    shared = sorted(set(ra) & set(rb))
    return [ra[d] for d in shared], [rb[d] for d in shared]


def cap_correlated(
    targets: list[Target],
    returns: dict[str, dict[str, float]],
    max_corr: float,
    cap: int = 1,
    taken_returns: dict[str, dict[str, float]] | None = None,
) -> list[Target]:
    """Keep at most `cap` targets from any group of mutually correlated names.

    Greedy in score order, mirroring `cap_per_underlying`: walk the (already
    sorted) targets and drop one when it already correlates >= `max_corr` with
    `cap` names that survived — including names already held or targeted
    (`taken_returns`), which is what the intraday re-screen needs.

    max_corr <= 0 disables. A target with no usable return history is kept
    (unknown never blocks), and so is one whose correlation cannot be computed.
    """
    if max_corr <= 0 or cap <= 0:
        return targets
    kept: list[Target] = []
    # (label, series) pairs that a new candidate must be checked against.
    kept_series: list[tuple[str, dict[str, float]]] = [
        (sym, r) for sym, r in (taken_returns or {}).items() if r
    ]
    for t in targets:
        mine = returns.get(t.symbol)
        if not mine:
            kept.append(t)
            kept_series.append((t.symbol, {}))
            continue
        peers: list[tuple[str, float]] = []
        for sym, other in kept_series:
            if not other:
                continue
            a, b = aligned_returns(mine, other)
            c = pearson(a, b)
            if c is not None and c >= max_corr:
                peers.append((sym, c))
        if len(peers) >= cap:
            worst = max(peers, key=lambda p: p[1])
            log.info(
                "CORRELATED %s: %.2f correlation with %s (already taken); skipping",
                t.symbol, worst[1], worst[0],
            )
            continue
        kept.append(t)
        kept_series.append((t.symbol, mine))
    return kept


def _bias(
    gap_pct: float,
    sentiment: float,
    sent_threshold: float,
    gap_threshold: float = 2.0,
) -> str:
    if abs(sentiment) >= sent_threshold:
        return "long" if sentiment > 0 else "short"
    if gap_threshold > 0 and abs(gap_pct) >= gap_threshold:
        return "long" if gap_pct > 0 else "short"
    return "any"


def build_targets(
    cfg: Config,
    candidates: list[Candidate],
    news: dict[str, NewsSignal],
    asset_names: dict[str, str] | None = None,
    taken_underlyings: set[str] | None = None,
    daily_returns: dict[str, dict[str, float]] | None = None,
    taken_returns: dict[str, dict[str, float]] | None = None,
) -> list[Target]:
    sel = cfg.selection
    max_abs_gap = max((abs(c.gap_pct) for c in candidates), default=1.0) or 1.0

    targets: list[Target] = []
    for c in candidates:
        sig = news.get(c.symbol)
        sentiment = sig.score if sig else 0.0
        n_articles = sig.n_articles if sig else 0

        if cfg.news.require_news and n_articles == 0:
            continue
        if cfg.news.min_articles and n_articles < cfg.news.min_articles:
            continue

        norm_gap = abs(c.gap_pct) / max_abs_gap
        score = (
            sel.weight_sentiment * abs(sentiment)
            + sel.weight_gap * norm_gap
            + sel.weight_volume * c.rel_volume
        )
        bias = _bias(
            c.gap_pct, sentiment,
            cfg.news.min_sentiment_for_bias, sel.gap_bias_min_pct,
        )
        # v1.9 dead-slot fix: with shorts disabled and respect_news_bias on,
        # a short-biased target can never trade (bias blocks longs, shorts are
        # off) — it would only occupy a slot. Skip it at selection time so the
        # slot goes to a tradeable candidate. Applies to the pre-market scan,
        # the intraday re-screen and the backtest alike (all call this).
        if (
            bias == "short"
            and not cfg.strategy.allow_shorts
            and cfg.strategy.respect_news_bias
        ):
            log.info(
                "DEAD SLOT %s: short bias but shorts are disabled; skipping",
                c.symbol,
            )
            continue
        reason = (
            f"gap {c.gap_pct:+.1f}%, sentiment {sentiment:+.2f} "
            f"({n_articles} articles), bias {bias}"
        )
        targets.append(
            Target(
                symbol=c.symbol,
                bias=bias,
                score=round(score, 4),
                gap_pct=c.gap_pct,
                sentiment=sentiment,
                n_articles=n_articles,
                reason=reason,
                rel_volume=c.rel_volume,
            )
        )

    targets.sort(key=lambda t: t.score, reverse=True)
    # v1.7: one bet per underlying — drop leveraged-ETF twins of the same
    # stock (dedupe BEFORE the max_targets cut so dups don't eat slots).
    targets = cap_per_underlying(
        targets, asset_names or {}, sel.max_per_underlying, taken_underlyings
    )
    # v1.14: one bet per correlated THEME. Runs after the same-underlying cap
    # (so exact twins are already gone and don't consume the theme's slot) and
    # before the max_targets cut (so a dropped name frees a slot for an
    # uncorrelated candidate rather than shrinking the day's target list).
    targets = cap_correlated(
        targets, daily_returns or {}, sel.max_correlation,
        sel.max_per_cluster, taken_returns,
    )
    chosen = targets[: sel.max_targets]
    for t in chosen:
        log.info("TARGET %s | %s | score %.3f", t.symbol, t.reason, t.score)
    return chosen


def fallback_targets(cfg: Config) -> list[Target]:
    """When the screener/news feed isn't available, trade the watchlist with no bias."""
    log.warning("Using fallback watchlist (screener/news unavailable).")
    return [
        Target(s, "any", 0.0, 0.0, 0.0, 0, "fallback watchlist")
        for s in cfg.fallback_symbols[: cfg.selection.max_targets]
    ]
