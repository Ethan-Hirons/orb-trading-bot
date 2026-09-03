"""Opening Range Breakout (ORB) strategy logic.

Pure Python (no Alpaca calls) so it can be unit-tested with synthetic bars.

Entry: price breaks out of the opening range (first N minutes) -> long/short.
Exit:  two modes (config `exits.mode`):
   - "percent": stop a fixed % from entry; take-profit scaled 3-5% by the
                stock's volatility (opening-range size as a % of price).
   - "range":   classic ORB brackets tied to the opening-range size.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor

from .config import ExitsConfig, SizingConfig, StrategyConfig


@dataclass
class OpeningRange:
    symbol: str
    high: float
    low: float

    @property
    def size(self) -> float:
        return self.high - self.low


@dataclass
class TradePlan:
    symbol: str
    side: str  # "long" or "short"
    qty: int
    entry_ref: float
    take_profit: float
    stop_loss: float
    tp_pct: float
    stop_pct: float
    # When set, the stop becomes a stop-LIMIT: triggered at stop_loss, filled
    # no worse than this price. Caps slippage on thin names.
    stop_limit: float | None = None
    # When set, the entry is a marketable LIMIT at this price instead of a
    # market order. Caps entry slippage so TP/SL distances stay as intended.
    entry_limit: float | None = None


def compute_opening_range(symbol: str, or_bars: list) -> OpeningRange | None:
    """Build the opening range from the bars within the opening window."""
    if not or_bars:
        return None
    high = max(float(b.high) for b in or_bars)
    low = min(float(b.low) for b in or_bars)
    if high <= low:
        return None
    return OpeningRange(symbol=symbol, high=high, low=low)


def check_breakout(rng: OpeningRange, price: float, cfg: StrategyConfig) -> str | None:
    """Return 'long', 'short', or None based on price vs the range."""
    if price > rng.high:
        return "long"
    if price < rng.low and cfg.allow_shorts:
        return "short"
    return None


def find_confirm_bar(
    rng: OpeningRange, side: str, completed_bars: list, cfg: StrategyConfig
):
    """Bar-close confirmation: return the most recent COMPLETED minute bar
    (within the last `confirm_lookback_bars`) that CLOSED beyond the range
    edge, or None if no bar confirms.

    A single trade print above the range can be noise (especially on the thin
    IEX feed); a full bar closing outside the range is a much stronger signal.
    v1.5: looks back over several bars instead of only the very last one —
    requiring the latest bar specifically to close outside made the bot sit
    out fast breakouts (2026-07-17: 5 targets broke out, 0 confirmed in time).
    The caller still requires the CURRENT price to be beyond the range
    (check_breakout), so a confirmed-then-collapsed move is not entered.

    When confirmation is off, returns the last completed bar (or None) so the
    volume filter still has a bar to inspect."""
    if not completed_bars:
        return None
    if not cfg.confirm_bar_close:
        return completed_bars[-1]
    lookback = max(1, getattr(cfg, "confirm_lookback_bars", 1))
    for bar in reversed(completed_bars[-lookback:]):
        close = float(bar.close)
        if side == "long" and close > rng.high:
            return bar
        if side == "short" and close < rng.low:
            return bar
    return None


def confirm_breakout(
    rng: OpeningRange, side: str, completed_bars: list, cfg: StrategyConfig
) -> bool:
    """True when the breakout is bar-close confirmed (see find_confirm_bar).
    Returns True when confirmation is off."""
    if not cfg.confirm_bar_close:
        return True
    return find_confirm_bar(rng, side, completed_bars, cfg) is not None


def passes_volume_filter(breakout_bar, or_bars: list, cfg: StrategyConfig) -> bool:
    """The confirming bar's volume must be at least `volume_filter_ratio` x
    the average opening-range bar volume. v1.5: ratio was implicitly 1.0,
    which mid-morning bars almost never beat (the OR bars right at the open
    carry the day's heaviest volume)."""
    if not cfg.require_volume_filter:
        return True
    if breakout_bar is None or not or_bars:
        return True
    avg_vol = sum(float(b.volume) for b in or_bars) / len(or_bars)
    ratio = getattr(cfg, "volume_filter_ratio", 1.0)
    return float(breakout_bar.volume) >= ratio * avg_vol


def scale_take_profit_pct(vol_pct: float, exits: ExitsConfig) -> float:
    """Map a stock's volatility (opening-range % of price) to a take-profit %
    between tp_min_pct and tp_max_pct."""
    lo, hi = exits.vol_ref_low_pct, exits.vol_ref_high_pct
    if hi <= lo:
        return exits.tp_min_pct
    frac = (vol_pct - lo) / (hi - lo)
    frac = max(0.0, min(1.0, frac))
    return exits.tp_min_pct + frac * (exits.tp_max_pct - exits.tp_min_pct)


def _size(qty_by_risk: float, price: float, sizing: SizingConfig) -> int:
    qty = qty_by_risk
    if sizing.max_position_notional and sizing.max_position_notional > 0:
        qty = min(qty, sizing.max_position_notional / price)
    if sizing.whole_shares_only:
        qty = floor(qty)
    return int(qty) if qty >= 1 else 0


def build_trade_plan(
    rng: OpeningRange,
    side: str,
    price: float,
    equity: float,
    exits: ExitsConfig,
    sizing: SizingConfig,
) -> TradePlan | None:
    """Compute quantity, take-profit and stop-loss for a breakout."""
    if exits.mode == "range":
        r = rng.size
        buffer = r * exits.stop_buffer_r
        target_dist = r * exits.take_profit_r
        if side == "long":
            stop_loss = rng.low - buffer
            take_profit = price + target_dist
        else:
            stop_loss = rng.high + buffer
            take_profit = price - target_dist
        stop_pct = abs(price - stop_loss) / price * 100.0
        tp_pct = abs(take_profit - price) / price * 100.0
    else:  # percent mode (default)
        vol_pct = (rng.size / price * 100.0) if price > 0 else 0.0
        tp_pct = scale_take_profit_pct(vol_pct, exits)
        stop_pct = exits.stop_pct
        if side == "long":
            stop_loss = price * (1 - stop_pct / 100.0)
            take_profit = price * (1 + tp_pct / 100.0)
        else:
            stop_loss = price * (1 + stop_pct / 100.0)
            take_profit = price * (1 - tp_pct / 100.0)

    per_share_risk = abs(price - stop_loss)
    if per_share_risk <= 0:
        return None

    risk_dollars = equity * (sizing.risk_per_trade_pct / 100.0)
    qty = _size(risk_dollars / per_share_risk, price, sizing)
    if qty < 1:
        return None

    # Sanity: exits must straddle entry on the correct sides.
    if side == "long" and not (stop_loss < price < take_profit):
        return None
    if side == "short" and not (take_profit < price < stop_loss):
        return None

    stop_limit = None
    if exits.stop_limit:
        off = exits.stop_limit_offset_pct / 100.0
        if side == "long":
            stop_limit = stop_loss * (1 - off)  # sell no lower than this
        else:
            stop_limit = stop_loss * (1 + off)  # buy back no higher than this
        stop_limit = round(stop_limit, 2)

    entry_limit = None
    if getattr(exits, "entry_limit_offset_pct", 0) and exits.entry_limit_offset_pct > 0:
        off = exits.entry_limit_offset_pct / 100.0
        if side == "long":
            entry_limit = price * (1 + off)  # buy no higher than this
        else:
            entry_limit = price * (1 - off)  # sell short no lower than this
        entry_limit = round(entry_limit, 2)

    return TradePlan(
        symbol=rng.symbol,
        side=side,
        qty=qty,
        entry_ref=price,
        take_profit=round(take_profit, 2),
        stop_loss=round(stop_loss, 2),
        tp_pct=round(tp_pct, 2),
        stop_pct=round(stop_pct, 2),
        stop_limit=stop_limit,
        entry_limit=entry_limit,
    )
