"""Thin wrapper around alpaca-py: account, market data, and orders.

Keeps all Alpaca-specific calls in one place so the strategy/runner code is
clean and the SDK can be swapped or mocked for testing.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestQuoteRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    ReplaceOrderRequest,
    StopLimitOrderRequest,
    StopLossRequest,
    StopOrderRequest,
    TakeProfitRequest,
    TrailingStopOrderRequest,
)

from .config import Config
from .logutil import get_logger


class Broker:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = get_logger()
        creds = cfg.credentials
        self.trading = TradingClient(
            creds.api_key, creds.secret_key, paper=creds.paper
        )
        self.data = StockHistoricalDataClient(creds.api_key, creds.secret_key)
        self._feed = cfg.runtime.data_feed

    def _retry(self, fn, what: str, attempts: int = 6, base_delay: float = 2.0):
        """Call `fn` with exponential backoff. Only used for idempotent reads
        (clock/account), where a transient 504/timeout at the open should cost
        seconds, not crash the bot. Re-raises after the last attempt."""
        for i in range(1, attempts + 1):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                if i == attempts:
                    raise
                delay = min(60.0, base_delay * 2 ** (i - 1))
                self.log.warning(
                    "%s failed (attempt %d/%d): %s. Retrying in %.0fs.",
                    what, i, attempts, e, delay,
                )
                time.sleep(delay)

    # ----- Account / clock -----

    def get_clock(self):
        """Returns Alpaca clock: .is_open, .timestamp, .next_open, .next_close."""
        return self._retry(self.trading.get_clock, "get_clock")

    def is_market_open(self) -> bool:
        return bool(self.get_clock().is_open)

    def get_equity(self) -> float:
        return float(self.get_account().equity)

    def get_buying_power(self) -> float | None:
        """v1.15: cash actually available to open a new position.

        Returned so the runner can decline a trade it cannot afford instead of
        discovering it via three rejected submissions and a day-block (see
        2026-09-09 SNXX). None means the value could not be read, in which case
        callers should proceed rather than block on a telemetry failure."""
        try:
            return float(self.get_account().buying_power)
        except Exception as e:  # noqa: BLE001
            self.log.warning("could not read buying power: %s", e)
            return None

    def get_last_equity(self) -> float:
        """Equity at the previous trading day's close (Alpaca last_equity).
        This is the baseline Alpaca's dashboard uses for 'daily change'."""
        return float(self.get_account().last_equity or 0)

    def get_account(self):
        return self._retry(self.trading.get_account, "get_account")

    # ----- Assets -----

    def get_asset(self, symbol: str):
        try:
            return self.trading.get_asset(symbol)
        except Exception:  # noqa: BLE001
            return None

    def is_shortable(self, symbol: str) -> bool:
        """True if Alpaca marks the asset shortable (and tradable) right now."""
        asset = self.get_asset(symbol)
        if asset is None:
            return False
        return bool(getattr(asset, "tradable", False)) and bool(
            getattr(asset, "shortable", False)
        )

    # ----- Market data -----

    def get_minute_bars(self, symbol: str, start: datetime, end: datetime):
        """Return a list of minute bars (objects with .timestamp/.open/.high/
        .low/.close/.volume) for [start, end]."""
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(1, TimeFrameUnit.Minute),
            start=start,
            end=end,
            feed=self._feed,
        )
        bars = self.data.get_stock_bars(req)
        # bars.data is a dict {symbol: [Bar, ...]}; may be missing if no data.
        return bars.data.get(symbol, [])

    def get_avg_opening_volume(
        self, symbol: str, or_minutes: int, lookback_days: int = 14
    ) -> float | None:
        """Average share volume in the first `or_minutes` of the session, over
        the last `lookback_days` COMPLETED sessions (v1.12).

        This is the denominator of opening relative volume — the filter that
        Zarattini, Barbon & Aziz (2024) found did almost all the work in an
        ORB strategy: an unfiltered ORB across 7,000 US stocks returned 29%
        over 2016-2023 (Sharpe 0.48, 41.4% hit ratio), while restricting to
        the day's most unusually active names returned 1,637% (Sharpe 2.81).
        Their gradient: below 100% relative volume averaged -0.02R per trade,
        above 100% +0.08R, above 3000% +0.38R.

        Note this is a RATIO of the same feed against itself, so the IEX
        tape-fraction problem that forced the v1.11.1 liquidity-floor scaling
        does not apply here — numerator and denominator are both IEX volume.

        Returns None when there is not enough history to judge; callers must
        treat that as "unknown", never as zero.
        """
        if or_minutes <= 0 or lookback_days <= 0:
            return None
        # Calendar span wide enough to contain `lookback_days` sessions.
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=int(lookback_days * 1.9) + 5)
        try:
            bars = self.get_minute_bars(symbol, start, end)
        except Exception as e:  # noqa: BLE001
            self.log.warning("get_avg_opening_volume failed for %s: %s", symbol, e)
            return None
        if not bars:
            return None

        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        today_et = datetime.now(et).date()
        per_day: dict[object, float] = {}
        for b in bars:
            ts = b.timestamp.astimezone(et)
            if ts.date() >= today_et:
                continue  # only completed sessions
            minutes_in = (ts.hour - 9) * 60 + (ts.minute - 30)
            if 0 <= minutes_in < or_minutes:
                per_day[ts.date()] = per_day.get(ts.date(), 0.0) + float(
                    b.volume or 0
                )
        if not per_day:
            return None
        recent = [per_day[d] for d in sorted(per_day)[-lookback_days:]]
        # A couple of sessions is not an average; refuse rather than mislead.
        if len(recent) < 3:
            return None
        avg = sum(recent) / len(recent)
        return avg if avg > 0 else None

    def get_latest_price(self, symbol: str) -> float | None:
        """Most recent trade price; falls back to last minute close on error."""
        try:
            req = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self._feed)
            trade = self.data.get_stock_latest_trade(req)
            return float(trade[symbol].price)
        except Exception:
            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=15)
            bars = self.get_minute_bars(symbol, start, end)
            return float(bars[-1].close) if bars else None

    def get_spread_pct(self, symbol: str) -> float | None:
        """Current bid-ask spread as a % of the midpoint. None when no valid
        quote is available (don't block trades on missing data)."""
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=self._feed)
            q = self.data.get_stock_latest_quote(req)[symbol]
            bid = float(q.bid_price)
            ask = float(q.ask_price)
        except Exception:  # noqa: BLE001
            return None
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        mid = (ask + bid) / 2.0
        return (ask - bid) / mid * 100.0 if mid > 0 else None

    def get_latest_prices(self, symbols: list[str]) -> dict[str, float]:
        """Batch latest trade prices for many symbols."""
        if not symbols:
            return {}
        try:
            req = StockLatestTradeRequest(symbol_or_symbols=symbols, feed=self._feed)
            trades = self.data.get_stock_latest_trade(req)
            return {s: float(t.price) for s, t in trades.items()}
        except Exception:
            return {}

    def get_prev_closes(self, symbols: list[str]) -> dict[str, float]:
        """Most recent completed daily close per symbol (yesterday's close)."""
        if not symbols:
            return {}
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)
        try:
            req = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=TimeFrame(1, TimeFrameUnit.Day),
                start=start,
                end=end,
                feed=self._feed,
            )
            bars = self.data.get_stock_bars(req)
            out: dict[str, float] = {}
            today = end.date()
            for s in symbols:
                blist = bars.data.get(s, [])
                if not blist:
                    continue
                prev = None
                for b in blist:
                    if b.timestamp.date() < today:
                        prev = b
                chosen = prev if prev is not None else blist[-1]
                out[s] = float(chosen.close)
            return out
        except Exception:
            return {}

    def get_daily_returns(
        self, symbols: list[str], lookback_days: int = 30
    ) -> dict[str, dict[str, float]]:
        """Close-to-close daily returns per symbol, keyed by ISO date.

        Feeds the v1.14 correlated-theme cap. Keyed by DATE rather than
        returned as a bare list because candidates are often recent listings
        whose histories start on different days — positional alignment would
        compare mismatched sessions.

        Returns are a RATIO, so the IEX feed's partial-tape problem largely
        cancels (unlike the v1.11 absolute volume floor, which it broke).
        Any symbol that errors or lacks history is simply absent, and an
        absent symbol is never blocked downstream.
        """
        if not symbols or lookback_days <= 0:
            return {}
        end = datetime.now(timezone.utc)
        # Calendar days needed for `lookback_days` sessions, plus slack for
        # holidays; the extra is harmless, it is one batched request either way.
        start = end - timedelta(days=int(lookback_days * 1.6) + 10)
        try:
            req = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=TimeFrame(1, TimeFrameUnit.Day),
                start=start,
                end=end,
                feed=self._feed,
            )
            bars = self.data.get_stock_bars(req)
        except Exception as e:  # noqa: BLE001
            self.log.warning("daily returns unavailable (%s); correlation cap inactive", e)
            return {}
        out: dict[str, dict[str, float]] = {}
        for s in symbols:
            blist = list(bars.data.get(s, []))[-(lookback_days + 1):]
            series: dict[str, float] = {}
            prev = None
            for b in blist:
                close = float(b.close or 0)
                if prev is not None and prev > 0 and close > 0:
                    series[b.timestamp.date().isoformat()] = close / prev - 1.0
                prev = close if close > 0 else prev
            if series:
                out[s] = series
        return out

    def get_prev_day_volumes(self, symbols: list[str]) -> dict[str, float]:
        """Most recent completed daily share volume per symbol (v1.11
        liquidity filter — AHCO 2026-08-04/05: a thin name's close order
        couldn't fill at the open and the leftover bled -5.4%)."""
        if not symbols:
            return {}
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)
        try:
            req = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=TimeFrame(1, TimeFrameUnit.Day),
                start=start,
                end=end,
                feed=self._feed,
            )
            bars = self.data.get_stock_bars(req)
            out: dict[str, float] = {}
            today = end.date()
            for s in symbols:
                blist = bars.data.get(s, [])
                prev = None
                for b in blist:
                    if b.timestamp.date() < today:
                        prev = b
                if prev is not None:
                    out[s] = float(prev.volume or 0)
            return out
        except Exception:  # noqa: BLE001
            return {}

    # ----- Positions / orders -----

    def list_positions(self):
        return self.trading.get_all_positions()

    def get_position(self, symbol: str):
        for p in self.list_positions():
            if p.symbol == symbol:
                return p
        return None

    def has_position(self, symbol: str) -> bool:
        return self.get_position(symbol) is not None

    def submit_bracket(
        self,
        symbol: str,
        qty: int,
        side: str,  # "long" or "short"
        take_profit_price: float,
        stop_loss_price: float,
        stop_limit_price: float | None = None,
        entry_limit_price: float | None = None,
    ):
        """Submit an entry with attached take-profit and stop-loss.

        Entry is a marketable LIMIT when entry_limit_price is given (caps entry
        slippage), otherwise a market order. If stop_limit_price is given, the
        stop leg is a stop-LIMIT (slippage capped at that price) instead of a
        stop-market.
        """
        order_side = OrderSide.BUY if side == "long" else OrderSide.SELL
        stop_kwargs = {"stop_price": round(stop_loss_price, 2)}
        if stop_limit_price is not None:
            stop_kwargs["limit_price"] = round(stop_limit_price, 2)
        common = dict(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
            stop_loss=StopLossRequest(**stop_kwargs),
        )
        if entry_limit_price is not None:
            req = LimitOrderRequest(
                limit_price=round(entry_limit_price, 2), **common
            )
        else:
            req = MarketOrderRequest(**common)
        return self.trading.submit_order(order_data=req)

    def wait_for_fill(self, order_id, timeout_s: float = 20.0) -> float | None:
        """Poll an order until it fills; return the fill price (or None on
        timeout/cancel/reject)."""
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                o = self.trading.get_order_by_id(order_id)
            except Exception:  # noqa: BLE001
                return None
            status = getattr(o.status, "value", str(o.status)).lower()
            price = float(getattr(o, "filled_avg_price", 0) or 0)
            if status == "filled" and price > 0:
                return price
            if status in ("canceled", "cancelled", "expired", "rejected"):
                return None
            if time.monotonic() >= deadline:
                return None
            time.sleep(1)

    def get_order(self, order_id):
        """Fetch an order by id; None on API error."""
        try:
            return self.trading.get_order_by_id(order_id)
        except Exception:  # noqa: BLE001
            return None

    def cancel_order(self, order_id) -> bool:
        """Cancel one order by id. Returns False if the cancel was rejected
        (e.g. the order already filled or is already canceled)."""
        try:
            self.trading.cancel_order_by_id(order_id)
            return True
        except Exception:  # noqa: BLE001
            return False

    def adjust_bracket_legs(
        self,
        parent_order_id,
        take_profit_price: float,
        stop_loss_price: float,
        stop_limit_price: float | None = None,
    ) -> bool:
        """Re-price the TP/SL legs of a filled bracket order. Returns True if
        both legs were updated."""
        try:
            parent = self.trading.get_order_by_id(parent_order_id)
        except Exception:  # noqa: BLE001
            return False
        ok = True
        for leg in getattr(parent, "legs", None) or []:
            leg_type = getattr(leg.type, "value", str(leg.type)).lower()
            try:
                if "stop" in leg_type:
                    kwargs = {"stop_price": round(stop_loss_price, 2)}
                    if stop_limit_price is not None and "limit" in leg_type:
                        kwargs["limit_price"] = round(stop_limit_price, 2)
                    self.trading.replace_order_by_id(
                        leg.id, ReplaceOrderRequest(**kwargs)
                    )
                else:  # take-profit (limit) leg
                    self.trading.replace_order_by_id(
                        leg.id,
                        ReplaceOrderRequest(limit_price=round(take_profit_price, 2)),
                    )
            except Exception:  # noqa: BLE001
                ok = False
        return ok

    def move_stop(
        self, symbol: str, stop_price: float, limit_price: float | None = None
    ) -> bool:
        """Re-price the open stop order(s) for a symbol (e.g. breakeven move).
        Returns True if at least one stop order was updated.

        v1.11: failures are LOGGED. Through Aug 3-6 every replace attempt from
        the trailing-stop ratchet failed silently (`except: pass`) and the
        trail never moved a single stop in live trading — invisible in the
        logs. Never swallow order-management errors again."""
        try:
            open_orders = self.trading.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
            )
        except Exception as e:  # noqa: BLE001
            self.log.warning("move_stop %s: could not list orders: %s", symbol, e)
            return False
        stop_orders = [
            o for o in open_orders or []
            if "stop" in getattr(o.type, "value", str(o.type)).lower()
        ]
        if not stop_orders:
            self.log.warning(
                "move_stop %s: no open stop order found to re-price.", symbol
            )
            return False
        moved = False
        for o in stop_orders:
            otype = getattr(o.type, "value", str(o.type)).lower()
            kwargs = {"stop_price": round(stop_price, 2)}
            if limit_price is not None and "limit" in otype:
                kwargs["limit_price"] = round(limit_price, 2)
            try:
                self.trading.replace_order_by_id(o.id, ReplaceOrderRequest(**kwargs))
                moved = True
            except Exception as e:  # noqa: BLE001
                self.log.warning(
                    "move_stop %s: replace of %s order %s FAILED: %s",
                    symbol, otype, o.id, e,
                )
        return moved

    def activate_trailing_stop(
        self,
        symbol: str,
        qty: int,
        side: str,  # "long" or "short" (the POSITION side)
        trail_percent: float,
        fallback_stop: float | None = None,
        fallback_limit: float | None = None,
    ) -> bool:
        """v1.11: hand the trailing stop to Alpaca's servers.

        Cancels the symbol's existing exit orders (bracket TP + stop legs),
        then submits a native trailing-stop order (trail_percent off the
        high-water mark, tracked server-side tick by tick — no more 30s poll
        lag, no fragile replace_order ratchet). Alpaca seeds the high-water
        mark from the current price, so activating once the position is past
        breakeven+trail preserves the arm-at-breakeven semantics.

        If the trailing submit fails, RESTORES a plain stop at fallback_stop
        so the position is never left unprotected, and logs loudly either way.
        """
        try:
            open_orders = self.trading.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
            )
        except Exception as e:  # noqa: BLE001
            self.log.warning(
                "TRAIL %s: could not list exit orders to cancel: %s", symbol, e
            )
            return False
        for o in open_orders or []:
            try:
                self.trading.cancel_order_by_id(o.id)
            except Exception as e:  # noqa: BLE001
                self.log.warning(
                    "TRAIL %s: cancel of order %s failed: %s", symbol, o.id, e
                )
        order_side = OrderSide.SELL if side == "long" else OrderSide.BUY
        req = TrailingStopOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            trail_percent=round(trail_percent, 2),
        )
        # Canceled legs can take a moment to release held qty; retry briefly.
        deadline = time.monotonic() + 30
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.trading.submit_order(order_data=req)
                return True
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(3)
        self.log.error(
            "TRAIL %s: trailing-stop submit failed after retries: %s",
            symbol, last_err,
        )
        if fallback_stop is not None:
            try:
                kwargs = dict(
                    symbol=symbol, qty=qty, side=order_side,
                    time_in_force=TimeInForce.DAY,
                    stop_price=round(fallback_stop, 2),
                )
                if fallback_limit is not None:
                    fb = StopLimitOrderRequest(
                        limit_price=round(fallback_limit, 2), **kwargs
                    )
                else:
                    fb = StopOrderRequest(**kwargs)
                self.trading.submit_order(order_data=fb)
                self.log.warning(
                    "TRAIL %s: restored plain stop at %.2f after trailing "
                    "submit failed.", symbol, fallback_stop,
                )
            except Exception as e:  # noqa: BLE001
                self.log.error(
                    "TRAIL %s: FAILED TO RESTORE STOP — POSITION UNPROTECTED, "
                    "close manually! %s", symbol, e,
                )
        return False

    def exit_position(self, symbol: str) -> bool:
        """Close one position at market, cancelling its bracket legs first."""
        try:
            open_orders = self.trading.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
            )
            for o in open_orders or []:
                try:
                    self.trading.cancel_order_by_id(o.id)
                except Exception:  # noqa: BLE001
                    pass
            self.trading.close_position(symbol)
            return True
        except Exception:  # noqa: BLE001
            return False

    def get_todays_fills(self, since: datetime) -> list[dict]:
        """All filled orders since `since`, as plain dicts for the report:
        {symbol, side ('buy'/'sell'), qty, price, time}."""
        try:
            orders = self.trading.get_orders(
                GetOrdersRequest(
                    status=QueryOrderStatus.CLOSED, after=since, limit=500
                )
            )
        except Exception:  # noqa: BLE001
            return []
        fills = []
        for o in orders or []:
            qty = float(getattr(o, "filled_qty", 0) or 0)
            price = float(getattr(o, "filled_avg_price", 0) or 0)
            if qty <= 0 or price <= 0:
                continue
            side = getattr(o, "side", None)
            side = getattr(side, "value", str(side)).lower()
            fills.append(
                {
                    "symbol": o.symbol,
                    "side": "buy" if "buy" in side else "sell",
                    "qty": qty,
                    "price": price,
                    "time": getattr(o, "filled_at", None),
                }
            )
        fills.sort(key=lambda f: (f["time"] is None, f["time"]))
        return fills


    def cancel_all_orders(self):  # go flat helper
        return self.trading.cancel_orders()

    def close_all_positions(self):
        """Cancel open orders and liquidate all positions (go flat)."""
        return self.trading.close_all_positions(cancel_orders=True)
