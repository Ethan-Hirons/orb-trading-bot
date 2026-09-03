"""Pre-market screener: find volatile movers worth trading today.

Pulls Alpaca's most-active and top-mover lists, then keeps names that fit the
account (price band) and are actually moving (gap %). If the screener data feed
isn't available on your plan, callers fall back to the configured watchlist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

from .config import Config
from .logutil import get_logger

if TYPE_CHECKING:  # only for type hints; keeps pure-logic tests alpaca-free
    from .broker import Broker

log = get_logger()

try:
    from alpaca.data.historical.screener import ScreenerClient
    from alpaca.data.requests import MarketMoversRequest, MostActivesRequest

    _SCREENER_AVAILABLE = True
except Exception:  # older/newer SDK layout
    _SCREENER_AVAILABLE = False


# Symbol suffixes that mark warrants, units, and rights (e.g. NVA.WS). These
# have terrible liquidity and often can't be traded/shorted at all; class
# shares like BRK.B are unaffected.
NON_COMMON_SUFFIXES = {"WS", "W", "WT", "U", "R", "RT"}


def is_common_stock_symbol(symbol: str) -> bool:
    if "." in symbol:
        suffix = symbol.rsplit(".", 1)[1].upper()
        if suffix in NON_COMMON_SUFFIXES:
            return False
    return True


@dataclass
class Candidate:
    symbol: str
    price: float
    prev_close: float
    gap_pct: float
    rel_volume: float
    source: str


class Screener:
    def __init__(self, cfg: Config, broker: Broker):
        self.cfg = cfg
        self.broker = broker
        self.client = None
        if _SCREENER_AVAILABLE:
            creds = cfg.credentials
            try:
                self.client = ScreenerClient(creds.api_key, creds.secret_key)
            except Exception as e:  # noqa: BLE001
                log.warning("Screener client unavailable: %s", e)

    # ----- raw symbol collection -----

    def _most_actives(self) -> dict[str, float]:
        """Return {symbol: volume} from the most-actives list."""
        if not self.client:
            return {}
        try:
            try:
                res = self.client.get_most_actives(
                    MostActivesRequest(top=self.cfg.screener.top_most_actives)
                )
            except TypeError:
                res = self.client.get_most_actives()
            out = {}
            for item in getattr(res, "most_actives", []) or []:
                out[item.symbol] = float(getattr(item, "volume", 0) or 0)
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("get_most_actives failed (feed/plan?): %s", e)
            return {}

    def _movers(self) -> dict[str, float]:
        """Return {symbol: percent_change} from gainers + losers."""
        if not self.client:
            return {}
        try:
            res = self.client.get_market_movers(
                MarketMoversRequest(top=self.cfg.screener.top_movers)
            )
            out = {}
            for group in ("gainers", "losers"):
                for item in getattr(res, group, []) or []:
                    out[item.symbol] = float(getattr(item, "percent_change", 0) or 0)
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("get_market_movers failed (feed/plan?): %s", e)
            return {}

    # ----- public -----

    def build_candidates(self) -> list[Candidate]:
        """Return filtered, gap-ranked candidates. Empty list => use fallback."""
        if not self.cfg.screener.enabled:
            return []
        # v1.13: names flagged illiquid EARLIER TODAY stay out for the rest of
        # the day. NEBX on 2026-08-13 was on the 01:35 illiquid list, was absent
        # from the 15:00 list (the prev-day volume bar had rolled over), became
        # the top-ranked target on a +68.5% gap, and lost -10.76 on a quote that
        # flickered between a 3.46% and a 0.44% spread within 33 seconds.
        # invariants.py already FAILs the session when a once-illiquid name gets
        # traded; this makes the bot obey the rule the checker enforces.
        if not hasattr(self, "_illiquid_today"):
            self._illiquid_today: set[str] = set()
            self._illiquid_day = None

        actives = self._most_actives()
        movers = self._movers()
        symbols = list({*actives.keys(), *movers.keys()})
        if not symbols:
            log.warning("Screener returned no symbols; will use fallback watchlist.")
            return []

        prices = self.broker.get_latest_prices(symbols)
        prev_closes = self.broker.get_prev_closes(symbols)
        max_vol = max(actives.values()) if actives else 1.0

        sc = self.cfg.screener
        # v1.11 liquidity floor: previous-day share volume. Thin names give
        # bad fills and unfillable exits (AHCO 2026-08-04/05: overnight
        # leftover bled -5.4% because its close order couldn't fill).
        prev_volumes: dict[str, float] = {}
        if sc.min_prev_day_volume > 0:
            prev_volumes = self.broker.get_prev_day_volumes(symbols)

        # v1.11.1 (2026-08-12): the floor is in CONSOLIDATED shares, but the
        # free IEX feed's daily bars carry IEX-venue volume only (~4% of the
        # tape) — on 2026-08-11 the unscaled floor rejected 44/46 candidates
        # (incl. RIOT and BYND) and left ONE target all day. Prefer the
        # most-actives list's market-wide volume when we have it; otherwise
        # scale the floor down for IEX bar volume.
        IEX_TAPE_FRACTION = 25.0  # consolidated ≈ ~25x IEX-venue volume
        candidates: list[Candidate] = []
        illiquid: list[str] = []
        # Reset the sticky set on a new session date (the bot runs across
        # midnight local time; the pre-market scan and the 09:30 scan are the
        # same trading day).
        today = datetime.now(ET).date()
        if today != self._illiquid_day:
            self._illiquid_day = today
            self._illiquid_today = set()

        sticky_skips: list[str] = []
        for sym in symbols:
            if not is_common_stock_symbol(sym):
                continue  # warrants/units/rights: illiquid, often untradeable
            if sym in self._illiquid_today:
                sticky_skips.append(sym)
                continue
            if sc.min_prev_day_volume > 0:
                vol = actives.get(sym, 0.0)  # market-wide (screener API)
                floor = sc.min_prev_day_volume
                if vol <= 0:
                    v = prev_volumes.get(sym)
                    vol = -1.0 if v is None else v
                    if self.cfg.runtime.data_feed == "iex":
                        floor = floor / IEX_TAPE_FRACTION
                # Unknown volume on a screened mover = treat as thin; the
                # cost of skipping is a missed trade, not a stuck position.
                if vol < floor:
                    illiquid.append(sym)
                    continue
            price = prices.get(sym)
            prev = prev_closes.get(sym)
            if not price or not prev or prev <= 0:
                continue
            if price < sc.min_price or price > sc.max_price:
                continue
            gap_pct = (price - prev) / prev * 100.0
            # Prefer the mover's reported change when we have it.
            if sym in movers:
                gap_pct = movers[sym]
            if abs(gap_pct) < sc.min_abs_gap_pct:
                continue
            # Skip extreme gappers (+200% names): untradeable spreads/halts,
            # and they crowd out realistic candidates.
            if sc.max_abs_gap_pct and abs(gap_pct) > sc.max_abs_gap_pct:
                continue
            rel_volume = (actives.get(sym, 0.0) / max_vol) if max_vol else 0.0
            candidates.append(
                Candidate(
                    symbol=sym,
                    price=price,
                    prev_close=prev,
                    gap_pct=gap_pct,
                    rel_volume=rel_volume,
                    source="mover" if sym in movers else "active",
                )
            )

        candidates.sort(key=lambda c: abs(c.gap_pct), reverse=True)
        candidates = candidates[: sc.max_candidates]
        if illiquid:
            self._illiquid_today.update(illiquid)
            log.info(
                "ILLIQUID skipped %d candidate(s) (prev-day volume < %s): %s",
                len(illiquid), f"{sc.min_prev_day_volume:,.0f}",
                ", ".join(sorted(illiquid)),
            )
        if sticky_skips:
            log.info(
                "ILLIQUID (sticky) skipped %d name(s) flagged earlier today: %s",
                len(sticky_skips), ", ".join(sorted(sticky_skips)),
            )
        log.info("Screener kept %d candidates.", len(candidates))
        return candidates
