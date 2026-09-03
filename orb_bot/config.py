"""Configuration loading: reads config.yaml and .env into typed objects."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Project root = parent of this package directory.
ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ScreenerConfig:
    enabled: bool = True
    top_most_actives: int = 20
    top_movers: int = 20
    max_candidates: int = 40
    min_price: float = 5.0
    max_price: float = 400.0
    min_abs_gap_pct: float = 2.0
    max_abs_gap_pct: float = 75.0  # skip names gapping more than this % (0 = off)
    # v1.11 liquidity filter: previous-day share volume floor. Thin names
    # give bad fills, unfillable exits, and overnight leftovers (AHCO
    # 2026-08-04/05 bled -5.4% because its close couldn't fill). 0 = off.
    min_prev_day_volume: float = 2_000_000


@dataclass
class NewsConfig:
    enabled: bool = True
    lookback_hours: int = 24
    min_sentiment_for_bias: float = 0.20
    require_news: bool = False
    min_articles: int = 0  # require at least N recent articles to be a target (0 = off)
    # --- intraday news rescan ---
    rescan_interval_seconds: int = 60  # re-check news this often during the day (0 = off)
    exit_on_adverse: bool = True       # close a position if fresh news turns strongly against it
    adverse_exit_score: float = 0.5    # |fresh sentiment| against the position needed to exit


@dataclass
class SelectionConfig:
    max_targets: int = 5
    weight_sentiment: float = 1.0
    weight_gap: float = 1.0
    weight_volume: float = 0.5
    # Minimum |gap %| for the gap alone to set a directional bias.
    # 0 disables gap-based bias (news sentiment alone decides).
    gap_bias_min_pct: float = 2.0
    # v1.7: max targets per underlying. Leveraged single-stock ETF twins
    # (e.g. SMCX/SMCL/SNXX all track SMCI) are one bet, not three — on
    # 2026-07-23 all three stopped out together. 0 = off.
    max_per_underlying: int = 1
    # v1.14: max targets per CORRELATED GROUP. The underlying cap above sees
    # MARA/CONL/ETHA/BITO/IBIT as five different bets; the market saw one
    # (2026-08-21: all five held at once, -46.92). Measured on daily returns,
    # so it generalises to themes nobody has named. 0 = off.
    max_correlation: float = 0.0
    max_per_cluster: int = 1
    correlation_lookback_days: int = 30


@dataclass
class StrategyConfig:
    opening_range_minutes: int = 15
    entry_window_minutes: int = 90
    allow_shorts: bool = True
    require_volume_filter: bool = False
    one_trade_per_symbol: bool = True
    respect_news_bias: bool = True
    max_trades_per_day: int = 5
    # Require a COMPLETED 1-min bar to close beyond the range before
    # entering (a single trade print can be noise, esp. on the IEX feed).
    confirm_bar_close: bool = True
    # v1.5: accept a confirming close in any of the last N completed bars
    # (was: the single most recent bar only, which made the bot sit out
    # fast breakouts — on 2026-07-17 five targets broke out in 11 minutes
    # and none confirmed in time).
    confirm_lookback_bars: int = 3
    # v1.5: the breakout bar's volume must be >= this fraction of the average
    # opening-range bar volume. 1.0 (the old implicit value) is nearly
    # impossible to beat mid-morning because OR bars at the open carry the
    # day's heaviest volume.
    volume_filter_ratio: float = 0.5
    # Skip entries when the bid-ask spread is wider than this % (0 = off).
    # Wide spreads mean bad fills and understated risk on thin names.
    max_spread_pct: float = 0.5
    # v1.12: opening relative volume gate — today's opening-range volume
    # divided by this symbol's OWN average opening-range volume over the last
    # `rel_volume_lookback_days` sessions. 1.0 means "as active as usual",
    # 3.0 means "three times its normal open". 0 = OFF (measure and log only).
    #
    # Off by default deliberately: this is the highest-conviction change
    # available from the research, and it has never been swept on this
    # universe. Leaving it at 0 still writes the measured value to
    # trade_features.csv on every entry, so the data to validate a threshold
    # accumulates from the next session onward.
    min_opening_rel_volume: float = 0.0
    rel_volume_lookback_days: int = 14


@dataclass
class ExitsConfig:
    mode: str = "percent"  # "percent" or "range"
    stop_pct: float = 3.0
    tp_min_pct: float = 3.0
    tp_max_pct: float = 5.0
    vol_ref_low_pct: float = 0.5
    vol_ref_high_pct: float = 2.0
    # Stop-limit: when the stop triggers, submit a limit instead of a market
    # order, capped stop_limit_offset_pct beyond the stop price. Caps slippage
    # on thin names; the bot still flattens everything before the close.
    stop_limit: bool = True
    stop_limit_offset_pct: float = 0.5
    # Entry as a marketable LIMIT this % beyond the breakout price instead of
    # a market order. Caps entry slippage on thin gappers (0 = market entry).
    entry_limit_offset_pct: float = 0.5
    # Breakeven stop: once a position is up this % (unrealized), move the
    # stop-loss to the entry price so the trade can no longer become a full
    # loser. 0 = off.
    breakeven_trigger_pct: float = 1.5
    # v1.5: time stop — close a position still open after this many minutes.
    # A trade that has hit neither TP nor SL by then is going nowhere and is
    # just tying up the account (ATAI 2026-07-17: 5h19m for -0.3%). 0 = off.
    max_hold_minutes: int = 180
    # v1.8: trailing stop — once a position's peak favorable excursion exceeds
    # this %, ratchet the stop to trail the peak by this % (sweep 2026-07-27:
    # trailing dominated every no-trail combo; 1.0-1.5% best). The stop only
    # ever tightens, and only takes over beyond breakeven. 0 = off.
    trail_pct: float = 0.0
    # range-mode only
    take_profit_r: float = 1.0
    stop_buffer_r: float = 0.10


@dataclass
class SizingConfig:
    risk_per_trade_pct: float = 1.0
    max_position_notional: float = 700.0
    whole_shares_only: bool = True


@dataclass
class RiskConfig:
    daily_profit_target_pct: float = 6.0
    daily_max_loss_pct: float = 5.0
    flatten_on_breaker: bool = True


@dataclass
class RuntimeConfig:
    poll_interval_seconds: int = 30
    flatten_before_close_minutes: int = 10
    data_feed: str = "iex"
    premarket_scan: bool = True
    # Start the bot hours early if you like: it waits and runs the target scan
    # this many minutes before the open, when pre-market data is freshest.
    scan_minutes_before_open: int = 30
    # Intraday re-screen: rerun the movers/news scan this often (minutes)
    # during the entry window, adding new movers to the target list until
    # the pool (selection.max_targets) is full. 0 = off.
    intraday_rescreen_minutes: int = 30
    # v1.13: how often (seconds) to refine MFE/MAE from minute-bar highs/lows
    # for open positions. The 30s last-trade poll misses spike-and-retrace
    # moves, and `_check_trailing` arms off that same peak. 0 = off (polled
    # values only, the pre-v1.13 behaviour).
    excursion_bar_seconds: int = 60


@dataclass
class Credentials:
    api_key: str
    secret_key: str
    paper: bool


@dataclass
class Config:
    fallback_symbols: list[str]
    screener: ScreenerConfig
    news: NewsConfig
    selection: SelectionConfig
    strategy: StrategyConfig
    exits: ExitsConfig
    sizing: SizingConfig
    risk: RiskConfig
    runtime: RuntimeConfig
    credentials: Credentials = field(repr=False)


def _load_credentials() -> Credentials:
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("ALPACA_API_KEY", "").strip()
    secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()
    paper = os.getenv("ALPACA_PAPER", "true").strip().lower() in ("1", "true", "yes")

    if not api_key or not secret_key or api_key.startswith("your_"):
        raise SystemExit(
            "Missing Alpaca API keys. Copy .env.example to .env and fill in "
            "ALPACA_API_KEY and ALPACA_SECRET_KEY."
        )
    return Credentials(api_key=api_key, secret_key=secret_key, paper=paper)


def load_config(path: str | Path | None = None) -> Config:
    """Load config.yaml + .env and return a validated Config object."""
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    symbols = [str(s).upper() for s in raw.get("fallback_symbols", [])]
    if not symbols:
        symbols = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]

    return Config(
        fallback_symbols=symbols,
        screener=ScreenerConfig(**(raw.get("screener") or {})),
        news=NewsConfig(**(raw.get("news") or {})),
        selection=SelectionConfig(**(raw.get("selection") or {})),
        strategy=StrategyConfig(**(raw.get("strategy") or {})),
        exits=ExitsConfig(**(raw.get("exits") or {})),
        sizing=SizingConfig(**(raw.get("sizing") or {})),
        risk=RiskConfig(**(raw.get("risk") or {})),
        runtime=RuntimeConfig(**(raw.get("runtime") or {})),
        credentials=_load_credentials(),
    )
