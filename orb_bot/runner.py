"""Main runner: orchestrates the trading day.

Flow each day:
  1. Refuse to trade unless ARMED for today (your daily permission switch).
  2. PRE-MARKET SCAN: screen for volatile movers, read their news, and pick the
     day's target list (up to `selection.max_targets`) each with a long/short bias.
  3. Wait for the US market open (9:30 ET).
  4. Record starting equity (basis for the daily profit/loss circuit breaker).
  5. Let each target's opening range form, then watch for breakouts (only in the
     news-implied direction when `respect_news_bias` is on).
  6. On a breakout, submit a bracket order: entry + volatility-scaled take-profit
     (3-5%) + ~3% stop-loss (stop-limit, slippage-capped).
  7. INTRADAY NEWS RESCAN: every `news.rescan_interval_seconds`, check for fresh
     headlines on targets and open positions. Fresh news updates biases; strongly
     adverse news closes the affected position early.
  8. Continuously check the daily circuit breaker; stop (and optionally flatten)
     at the profit target or max loss.
  9. Flatten everything before the close; never hold overnight. Then write the
     end-of-day summary (win rate, expectancy, drawdown) to the log and
     state/trade_history.csv.
"""

from __future__ import annotations

import time
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from .broker import Broker
from .config import Config
from .logutil import get_logger
from .news import NewsScanner, should_exit_on_news
from .risk import BreakerStatus, RiskManager
from .screener import Screener
from .selection import (
    Target, build_targets, fallback_targets, underlying_key,
)
from .state import DailyState, load_state, save_state
from .strategy import (
    build_trade_plan,
    check_breakout,
    compute_opening_range,
    find_confirm_bar,
    passes_volume_filter,
)
from . import report

ET = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)

# Crash resilience: tolerate this many consecutive loop errors before giving up.
MAX_CONSECUTIVE_ERRORS = 10


def _bar_is_liquid(bar, min_trades: int) -> bool:
    """True if `bar` carries enough trades for its high/low to be a real price.

    Missing trade_count means the feed did not say, so the bar is kept -- this
    filter only ever acts on evidence, never on an absence of it."""
    n = getattr(bar, "trade_count", None)
    if n is None:
        return True
    try:
        return int(n) >= min_trades
    except (TypeError, ValueError):
        return True


def _bar_at_or_after(bar, cutoff: datetime) -> bool:
    """True if `bar` opened at or after `cutoff` (v1.17 excursion clamp).

    A bar with no usable timestamp is kept: the caller has already clamped the
    fetch window, so keeping it is the same behaviour as before this filter."""
    ts = getattr(bar, "timestamp", None)
    if ts is None:
        return True
    try:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts >= cutoff
    except (AttributeError, TypeError):
        return True


class Runner:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.broker = Broker(cfg)
        self.log = get_logger()
        self.news = NewsScanner(cfg)
        self._last_news_check: datetime | None = None
        self._bias_skips_logged: set[str] = set()
        self._shortable_cache: dict[str, bool] = {}
        self._order_failures: dict[str, int] = {}
        self._last_equity_logged: float | None = None
        self._last_equity_log_ts: float = 0.0
        # Entries submitted but not yet confirmed filled:
        # symbol -> {"order_id", "plan", "submitted" (monotonic), "cancel_requested"}
        self._pending_entries: dict[str, dict] = {}
        # Intraday re-screen pacing (first run ~interval after startup).
        self._last_rescreen: float = time.monotonic()
        # Symbols whose stop has already been moved to breakeven today.
        self._breakeven_done: set[str] = set()
        # v1.8: last trailing-stop price we set per symbol (ratchet memory).
        self._trail_stops: dict[str, float] = {}
        # v1.11: symbols whose exit has been handed to a native Alpaca
        # trailing-stop order (server-side; replaces the replace_order
        # ratchet that failed silently on every attempt Aug 3-6).
        self._trail_active: set[str] = set()
        # v1.15: symbols whose peak qualified for the trail but whose current
        # price had already retraced past breakeven+trail, so arming was held
        # back. Dedup only — one "TRAIL DEFERRED" line per symbol per day.
        self._trail_deferred: set[str] = set()
        # Dedup for confirmation/spread skip logs (once per symbol per day).
        self._filter_skips_logged: set[str] = set()
        # Dedup for unshortable short-breakout skips (once per day/symbol).
        self._short_skips_logged: set[str] = set()
        # Per-symbol intraday excursion tracking (instrumentation only, v1.6):
        # symbol -> {"mfe","mae","mfe_pct","mae_pct"} peak favorable / worst
        # adverse unrealized $ and % seen during the hold. In-memory only (not
        # persisted); used to evaluate whether a trailing stop would help.
        self._excursion: dict[str, dict] = {}
        # v1.13: minute-bar refinement of the above. `_last_bar_excursion` is a
        # monotonic throttle (one sweep per `runtime.excursion_bar_seconds`);
        # `_bar_scan_from` is the per-symbol watermark so each sweep only pulls
        # bars since the previous one, with a 2-minute overlap.
        self._last_bar_excursion: float = 0.0
        self._bar_scan_from: dict[str, datetime] = {}
        # v1.17: when this runner first saw each open position, used as the
        # excursion lookback floor when `state.entry_times` has no row for it
        # (an overnight leftover, or a fill this process did not place).
        self._first_seen: dict[str, datetime] = {}
        # v1.17: set by a SIGTERM/SIGINT handler (see run.py). The loop checks
        # it and exits through the normal flatten path, so `systemctl stop`
        # can no longer leave a position open overnight.
        self._stop_requested = False
        # v1.18: log each excursion data-quality complaint once per symbol per
        # session; the loop runs every 30s and would otherwise bury the log.
        self._thin_bar_warned: set[str] = set()
        self._impossible_mae_warned: set[str] = set()
        # v1.12: opening relative volume. `_or_relvol_avg` is the per-symbol
        # BASELINE (own average opening-window volume), warmed outside the
        # entry path by `_prefetch_rel_volume`. `_or_relvol` is the computed
        # ratio for today. None in either = not enough history to judge.
        self._or_relvol_avg: dict[str, float | None] = {}
        self._or_relvol: dict[str, float | None] = {}

    # Give up on a symbol for the day after this many failed order submissions.
    MAX_ORDER_FAILURES = 3
    # Log the unchanged equity heartbeat at most this often (seconds).
    EQUITY_LOG_INTERVAL = 300
    # Cancel an entry order that still has no fill after this many seconds
    # (a marketable limit that hasn't filled by then means price moved away).
    ENTRY_FILL_GRACE_S = 180
    # How long to wait at EOD for closing fills to pair into round trips.
    # (60s proved too short on 2026-07-16: INTC's close missed the CSV.)
    EOD_PAIRING_WAIT_S = 180
    # How long to keep verifying that a flatten actually zeroed all positions
    # (close_all_positions is fire-and-forget; on 2026-07-16 an INTC close
    # was lost and the short carried overnight).
    FLATTEN_VERIFY_S = 120

    def _is_shortable(self, symbol: str) -> bool:
        if symbol not in self._shortable_cache:
            self._shortable_cache[symbol] = self.broker.is_shortable(symbol)
        return self._shortable_cache[symbol]

    def _block_symbol(self, state: DailyState, symbol: str, reason: str) -> None:
        if symbol not in state.blocked_symbols:
            state.blocked_symbols.append(symbol)
            save_state(state)
            self.log.warning("BLOCKED %s for the day: %s", symbol, reason)

    # ---------- time helpers ----------

    def _now_et(self) -> datetime:
        return self.broker.get_clock().timestamp.astimezone(ET)

    def _market_open_et(self, now_et: datetime) -> datetime:
        return now_et.replace(
            hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0
        )

    # ---------- lifecycle ----------

    def run(self) -> None:
        mode = "PAPER" if self.cfg.credentials.paper else "LIVE"
        self.log.info("=" * 60)
        self.log.info("ORB bot starting in %s mode.", mode)

        # Reconcile trade history against recent broker fills: recovers round
        # trips whose closing fills landed after a previous EOD pairing gave
        # up (keeps the trial stats honest).
        try:
            added = report.backfill_history(self.broker)
            if added:
                self.log.info(
                    "BACKFILL: recovered %d missing trade(s) from recent fills.",
                    added,
                )
        except Exception as e:  # noqa: BLE001
            self.log.error("Trade-history backfill failed: %s", e)

        state = load_state()
        today = self._now_et().date()

        if not (state.is_for_today(today) and state.armed):
            self.log.warning(
                "Bot is NOT armed for %s. Run `python arm.py` first. Exiting.",
                today.isoformat(),
            )
            return

        # Pre-market scan (once per day). If started early (e.g. before leaving
        # for work), nap until shortly before the open so the movers/news scan
        # uses the freshest pre-market data instead of early-morning noise.
        if not state.scanned:
            self._wait_for_scan_window()
            if self._stop_requested:
                self.log.warning("SHUTDOWN requested before the scan; exiting.")
                return
            targets = self._premarket_scan()
            state.targets = [t.as_state_dict() for t in targets]
            state.scanned = True
            save_state(state)
            # Baseline: mark current articles as seen so the intraday rescan
            # only reacts to genuinely NEW news.
            self.news.mark_seen([t["symbol"] for t in state.targets])
        self.log.info(
            "Today's targets: %s",
            ", ".join(f"{t['symbol']}({t['bias']})" for t in state.targets) or "(none)",
        )
        # v1.12: warm the relative-volume baselines before the open, so the
        # entry path never makes this call.
        self._prefetch_rel_volume([t["symbol"] for t in state.targets])

        self._wait_for_open()
        if self._stop_requested:
            self.log.warning("SHUTDOWN requested before the open; nothing held.")
            return
        # v1.5: the bot should NEVER be holding at the open (it flattens
        # before every close). If a position slipped through — like INTC on
        # 2026-07-16, whose close fill was lost — close it immediately
        # instead of dragging it around all day.
        self._close_overnight_leftovers()
        # Pace the intraday re-screen from the open, not from bot startup.
        self._last_rescreen = time.monotonic()

        if state.start_equity <= 0:
            # Baseline on Alpaca's previous-close equity so the logged daily
            # P&L matches the dashboard's "daily change" number. Falls back
            # to the current equity if last_equity is unavailable.
            baseline = 0.0
            try:
                baseline = self.broker.get_last_equity()
            except Exception:  # noqa: BLE001
                pass
            state.start_equity = baseline if baseline > 0 else self.broker.get_equity()
            save_state(state)
        self.log.info("Starting equity: %.2f", state.start_equity)

        session_start = datetime.now(timezone.utc)
        risk = RiskManager(self.cfg.risk, state.start_equity)
        self._trading_loop(state, risk)
        self._final_report(state, session_start)

    # ---------- pre-market ----------

    def _premarket_scan(self) -> list[Target]:
        if not self.cfg.runtime.premarket_scan or not self.cfg.screener.enabled:
            return fallback_targets(self.cfg)

        self.log.info("Running pre-market screener + news scan...")
        candidates = Screener(self.cfg, self.broker).build_candidates()
        if not candidates:
            return fallback_targets(self.cfg)

        news = self.news.score_symbols([c.symbol for c in candidates])
        syms = [c.symbol for c in candidates]
        targets = build_targets(
            self.cfg, candidates, news,
            asset_names=self._asset_names(syms),
            daily_returns=self._daily_returns(syms),
        )

        # Don't waste a target slot on a short we can't actually take.
        kept = []
        for t in targets:
            if t.bias == "short" and not self._is_shortable(t.symbol):
                self.log.info(
                    "Dropping %s: bias short but asset is not shortable.", t.symbol
                )
                continue
            kept.append(t)
        return kept or fallback_targets(self.cfg)

    def _daily_returns(self, symbols: list[str]) -> dict[str, dict[str, float]]:
        """Daily return history for the v1.14 correlated-theme cap.

        One batched call, skipped entirely when the cap is off. Any failure
        degrades to {} — i.e. no correlation capping — never to a veto.
        """
        sel = self.cfg.selection
        if sel.max_correlation <= 0 or not symbols:
            return {}
        try:
            rets = self.broker.get_daily_returns(
                symbols, sel.correlation_lookback_days
            )
        except Exception as e:  # noqa: BLE001
            self.log.warning("daily returns failed (%s); correlation cap inactive", e)
            return {}
        # Visibility: a silently-empty history set would make the cap a no-op
        # without anyone noticing — the v1.11 liquidity-floor failure mode in
        # reverse. invariants.py can assert on this line.
        usable = sum(1 for s in symbols if len(rets.get(s, {})) >= 10)
        self.log.info(
            "CORRELATION DATA: %d/%d candidates have >=10 daily returns "
            "(cap %s at corr>=%.2f, max %d per group)",
            usable, len(symbols), "ON", sel.max_correlation, sel.max_per_cluster,
        )
        return rets

    def _asset_names(self, symbols: list[str]) -> dict[str, str]:
        """Asset titles from Alpaca, for same-underlying grouping (v1.7).
        Missing names are simply absent — grouping degrades to no-op."""
        out: dict[str, str] = {}
        for s in symbols:
            a = self.broker.get_asset(s)
            name = getattr(a, "name", None) if a is not None else None
            if name:
                out[s] = str(name)
        return out

    def _wait_for_scan_window(self) -> None:
        """Sleep until `scan_minutes_before_open` before the next open (or
        return immediately if the market is already open / window reached)."""
        lead = timedelta(minutes=max(0, self.cfg.runtime.scan_minutes_before_open))
        while True:
            clock = self.broker.get_clock()
            if clock.is_open:
                return
            scan_at = clock.next_open - lead
            remaining = (scan_at - clock.timestamp).total_seconds()
            if remaining <= 0:
                return
            self.log.info(
                "Waiting to scan at %s (%d min before the open). Sleeping %ds.",
                scan_at.astimezone(ET).strftime("%H:%M ET"),
                int(lead.total_seconds() // 60),
                int(min(300, remaining)),
            )
            self._sleep(min(300, remaining))
            if self._stop_requested:
                return

    def _wait_for_open(self) -> None:
        while not self.broker.is_market_open():
            clock = self.broker.get_clock()
            wait_s = max(
                30, min(300, (clock.next_open - clock.timestamp).total_seconds())
            )
            self.log.info(
                "Market closed. Next open %s. Sleeping %ds.",
                clock.next_open.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"),
                int(wait_s),
            )
            self._sleep(wait_s)
            if self._stop_requested:
                return

    # ---------- shutdown ----------

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def request_stop(self) -> None:
        """Ask the session to end at the next safe point and flatten.

        Signal-handler safe: this only sets a flag. All broker work happens on
        the main loop, so a signal can never land in the middle of an order
        submission."""
        self._stop_requested = True

    def _sleep(self, seconds: float) -> None:
        """time.sleep, but wakes within a second of a stop request."""
        deadline = time.monotonic() + max(0.0, seconds)
        while not self._stop_requested:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(1.0, remaining))

    # ---------- main loop ----------

    def _trading_loop(self, state: DailyState, risk: RiskManager) -> None:
        poll = self.cfg.runtime.poll_interval_seconds
        errors = 0
        while True:
            if self._stop_requested:
                self.log.warning(
                    "SHUTDOWN requested: flattening all positions before exit."
                )
                try:
                    self._flatten_all_verified()
                except Exception as e:  # noqa: BLE001
                    self.log.error(
                        "SHUTDOWN flatten FAILED: %s. CLOSE POSITIONS BY HAND "
                        "in the Alpaca dashboard.", e
                    )
                break
            try:
                if self._loop_once(state, risk):
                    break
                errors = 0
            except Exception as e:  # noqa: BLE001
                errors += 1
                self.log.error(
                    "Loop error (%d/%d): %s", errors, MAX_CONSECUTIVE_ERRORS, e
                )
                if errors >= MAX_CONSECUTIVE_ERRORS:
                    self.log.error(
                        "Too many consecutive errors; flattening and stopping."
                    )
                    try:
                        self._flatten_all_verified()
                    except Exception as e2:  # noqa: BLE001
                        self.log.error("Flatten failed: %s", e2)
                    break
                self._sleep(min(60, poll * errors))  # back off and retry
                continue
            self._sleep(poll)

    def _loop_once(self, state: DailyState, risk: RiskManager) -> bool:
        """One iteration of the trading loop. Returns True when the session
        should end."""
        strat = self.cfg.strategy
        flatten_before = timedelta(
            minutes=self.cfg.runtime.flatten_before_close_minutes
        )

        clock = self.broker.get_clock()
        if not clock.is_open:
            self.log.info("Market is closed. Ending session.")
            return True

        now_et = clock.timestamp.astimezone(ET)
        open_et = self._market_open_et(now_et)
        or_end = open_et + timedelta(minutes=strat.opening_range_minutes)
        entry_cutoff = open_et + timedelta(minutes=strat.entry_window_minutes)

        if clock.next_close - clock.timestamp <= flatten_before:
            self.log.info("Approaching close. Flattening all positions.")
            self._flatten_all_verified()
            return True

        equity = self.broker.get_equity()
        assessment = risk.assess(equity)
        if assessment.status != BreakerStatus.OK and not state.halted:
            state.halted = True
            state.halt_reason = assessment.message
            save_state(state)
            self.log.warning("CIRCUIT BREAKER: %s", assessment.message)
            if self.cfg.risk.flatten_on_breaker:
                self._flatten_all_verified()
                return True
        else:
            # Heartbeat: log when equity changes, else at most every 5 min.
            now_mono = time.monotonic()
            if (
                equity != self._last_equity_logged
                or now_mono - self._last_equity_log_ts >= self.EQUITY_LOG_INTERVAL
            ):
                self.log.info("Equity %.2f | %s", equity, assessment.message)
                self._last_equity_logged = equity
                self._last_equity_log_ts = now_mono

        # Entries whose fill was never confirmed: re-anchor late fills,
        # cancel + free the trade slot when they never fill.
        self._reconcile_pending_entries(state)

        # Breakeven: winners past the trigger can no longer become losers.
        self._check_breakeven()

        # Excursion tracking (instrumentation only): record peak favorable /
        # worst adverse unrealized before any exit fires this iteration (v1.6).
        self._track_excursion(state)

        # Trailing stop (v1.8, off unless exits.trail_pct > 0): ratchet stops
        # toward the peak recorded above. Runs right after excursion tracking
        # so it always trails the freshest peak.
        self._check_trailing()

        # Time stop: positions that hit neither TP nor SL for hours are dead
        # money; close them and free the slot/notional (v1.5).
        self._check_time_stop(state)

        # Intraday news rescan: update biases, exit on strongly adverse news.
        self._news_recheck(state)

        # Intraday re-screen: discover new movers while entries are allowed.
        self._maybe_rescreen(state, now_et, entry_cutoff)

        can_enter = (
            not state.halted
            and now_et >= or_end
            and now_et <= entry_cutoff
            and state.trades_opened < strat.max_trades_per_day
        )
        if can_enter:
            self._scan_for_entries(state, open_et, or_end)

        return False

    # ---------- flatten helpers (v1.5) ----------

    def _flatten_all_verified(self) -> None:
        """Go flat and VERIFY it. `close_all_positions` is fire-and-forget;
        on 2026-07-16 an INTC close was lost and the short carried overnight.
        Poll until no positions remain, re-issuing closes for stragglers, and
        log loudly if anything is still open when the wait expires."""
        try:
            self.broker.close_all_positions()
        except Exception as e:  # noqa: BLE001
            self.log.error("close_all_positions failed: %s", e)
        deadline = time.monotonic() + self.FLATTEN_VERIFY_S
        while time.monotonic() < deadline:
            time.sleep(5)
            try:
                remaining = self.broker.list_positions()
            except Exception:  # noqa: BLE001
                continue  # transient API error; check again
            if not remaining:
                self.log.info("Flatten verified: no open positions.")
                return
            for p in remaining:
                self.log.warning(
                    "Flatten: %s still open (qty %s); re-closing.",
                    p.symbol, p.qty,
                )
                self.broker.exit_position(p.symbol)
        try:
            remaining = self.broker.list_positions()
        except Exception:  # noqa: BLE001
            remaining = None
        if remaining:
            self.log.error(
                "STILL HOLDING after %ds: %s — close manually in the Alpaca "
                "dashboard! The bot must never hold overnight.",
                self.FLATTEN_VERIFY_S,
                ", ".join(f"{p.symbol}(qty {p.qty})" for p in remaining),
            )

    def _close_overnight_leftovers(self) -> None:
        """Close any position already open at the start of the session.

        v1.9 fix (2026-08-10): Add retry logic with verification. On 2026-08-05
        AHCO wasn't closed on 8/4, carried overnight, then exit_position failed to
        fill at market open (no liquidity), and the position went -37.37 before EOD.
        Now we retry with waits and verify the position is actually gone before
        proceeding.
        """
        try:
            leftovers = self.broker.list_positions()
        except Exception as e:  # noqa: BLE001
            self.log.error("Could not check for overnight positions: %s", e)
            return
        if not leftovers:
            return
        self.log.warning(
            "OVERNIGHT LEFTOVER(S) found at the open: %s. Yesterday's flatten "
            "must have failed; closing them now (the round trip is recovered "
            "into trade_history by the cross-day backfill).",
            ", ".join(f"{p.symbol}(qty {p.qty})" for p in leftovers),
        )

        # Retry closing each position with waits to allow fills and backoff.
        for p in leftovers:
            deadline = time.monotonic() + 120  # 2-minute timeout per symbol
            attempt = 0
            while time.monotonic() < deadline:
                attempt += 1
                if not self.broker.exit_position(p.symbol):
                    self.log.warning(
                        "Failed to close overnight %s (attempt %d); retrying...",
                        p.symbol, attempt,
                    )
                    time.sleep(5 + attempt * 2)  # Exponential backoff: 7s, 9s, 11s...
                    continue

                # Order submitted; wait for it to actually fill and position to disappear.
                verify_deadline = time.monotonic() + 60
                while time.monotonic() < verify_deadline:
                    time.sleep(3)
                    try:
                        current_positions = self.broker.list_positions()
                        if not any(pos.symbol == p.symbol for pos in (current_positions or [])):
                            self.log.info("Overnight %s closed successfully.", p.symbol)
                            break
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    self.log.error(
                        "OVERNIGHT %s STILL OPEN after close attempts. "
                        "Must close manually in Alpaca dashboard!",
                        p.symbol,
                    )
                break
            else:
                self.log.error(
                    "OVERNIGHT %s close timed out after multiple retries. "
                    "Must close manually in Alpaca dashboard!",
                    p.symbol,
                )

    def _track_excursion(self, state=None) -> None:
        """v1.13: update per-symbol peak favorable (MFE) and worst adverse
        (MAE) for each open position, from BOTH the polled unrealized P&L and
        the minute-bar highs/lows since the last check.

        No longer instrumentation only. `_check_trailing` arms off `mfe_pct`,
        so a peak this misses is a trail that never arms. The 30s poll samples
        last-trade prices and demonstrably missed real moves: every stopped-out
        trade through 2026-08-14 recorded a MAE milder than its realized loss
        (Jul 31 NBIZ MAE -1.07% vs -3.05% closed), and MFE was understated too
        (Aug 3 SNXX peak recorded +11.53%, closed +12.17%). Minute bars carry
        the true high/low of every interval, so a spike-and-retrace inside one
        poll gap is now caught."""
        try:
            positions = self.broker.list_positions()
        except Exception:  # noqa: BLE001
            return
        for pos in positions:
            upl = float(getattr(pos, "unrealized_pl", 0) or 0)
            upl_pct = float(getattr(pos, "unrealized_plpc", 0) or 0) * 100.0
            e = self._excursion.get(pos.symbol)
            if e is None:
                self._first_seen.setdefault(
                    pos.symbol, datetime.now(timezone.utc)
                )
                self._excursion[pos.symbol] = {
                    "mfe": upl, "mae": upl,
                    "mfe_pct": upl_pct, "mae_pct": upl_pct,
                }
            else:
                if upl > e["mfe"]:
                    e["mfe"], e["mfe_pct"] = upl, upl_pct
                if upl < e["mae"]:
                    e["mae"], e["mae_pct"] = upl, upl_pct
        self._refine_excursion_from_bars(positions, state)

    def _entry_dt(self, symbol: str, state) -> datetime | None:
        """Fill time for `symbol` as an aware UTC datetime, or None.

        Prefers the recorded entry time; falls back to when this runner first
        saw the position, so a leftover with no `entry_times` row still never
        scans back before we knew we held it."""
        iso = None
        if state is not None:
            iso = getattr(state, "entry_times", {}).get(symbol)
        if iso:
            try:
                dt = datetime.fromisoformat(iso)
            except (TypeError, ValueError):
                dt = None
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
        return self._first_seen.get(symbol)

    def _refine_excursion_from_bars(self, positions, state=None) -> None:
        """Extend each open position's MFE/MAE with the true high/low from
        minute bars since the last scan. Best-effort: any failure leaves the
        polled numbers untouched, so this can never block or break the loop."""
        interval = max(0, int(self.cfg.runtime.excursion_bar_seconds))
        if interval == 0 or not positions:
            return
        now_mono = time.monotonic()
        if now_mono - self._last_bar_excursion < interval:
            return
        self._last_bar_excursion = now_mono

        now = datetime.now(timezone.utc)
        for pos in positions:
            symbol = pos.symbol
            try:
                entry = float(pos.avg_entry_price)
                qty = abs(int(float(pos.qty)))
            except (TypeError, ValueError):
                continue
            if entry <= 0 or qty <= 0:
                continue
            side = getattr(pos.side, "value", str(pos.side)).lower()
            sign = -1 if "short" in side else 1
            # Scan from the last scan for this symbol, or the last 20 minutes
            # on first sight (covers the gap between fill and first poll).
            # v1.17: never scan back past the fill. The old floor was
            # `now - 20 minutes` on first sight, so a 10:00:45 entry pulled bars
            # from 09:40 and scored the whole opening range as this position's
            # excursion (2026-09-18 SNXX reported MAE -3.86% against a -1.50%
            # bracket stop that was never breached; MARA -2.63% likewise).
            # This is not cosmetic: `_check_trailing` arms off `mfe_pct`, so for
            # a short, a pre-entry high could arm the trail off a price the
            # position never experienced.
            entry_dt = self._entry_dt(symbol, state)
            since = self._bar_scan_from.get(symbol) or (now - timedelta(minutes=20))
            if entry_dt is not None and since < entry_dt:
                since = entry_dt
            try:
                bars = self.broker.get_minute_bars(symbol, since, now)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("Excursion bar fetch failed for %s: %s", symbol, exc)
                continue
            if not bars:
                continue
            if entry_dt is not None:
                # Drop the bar straddling the fill: its high/low span prices
                # from before we were in the trade. The 30s unrealized poll
                # already covers that first partial minute.
                bars = [b for b in bars if _bar_at_or_after(b, entry_dt)]
                if not bars:
                    continue
            watermark = now - timedelta(minutes=2)  # overlap
            if entry_dt is not None and watermark < entry_dt:
                watermark = entry_dt
            self._bar_scan_from[symbol] = watermark

            # v1.18 FILTER 1 -- believe a bar's extremes only if the bar looks
            # like real trading. One odd-lot print on a thin name makes a bar
            # whose high/low was never a tradeable price. 2026-09-22 SNXX was
            # scored MAE -13.69% (-95.90) on a session whose deepest whole-book
            # unrealized was -11.23; the stop at -1.5% never fired because the
            # price it claims never happened.
            min_trades = int(getattr(self.cfg.runtime, "excursion_min_trades", 0))
            if min_trades > 0:
                solid = [b for b in bars if _bar_is_liquid(b, min_trades)]
                dropped = len(bars) - len(solid)
                if solid:
                    if dropped and symbol not in self._thin_bar_warned:
                        self._thin_bar_warned.add(symbol)
                        self.log.info(
                            "EXCURSION %s: ignoring %d thin bar(s) (<%d trades) "
                            "when taking high/low.", symbol, dropped, min_trades,
                        )
                    bars = solid
                elif dropped:
                    # Every bar was thin: no opinion is better than a wrong one.
                    continue

            highs = [float(b.high) for b in bars if getattr(b, "high", None)]
            lows = [float(b.low) for b in bars if getattr(b, "low", None)]
            if not highs or not lows:
                continue
            # Favorable extreme is the high for a long, the low for a short.
            best = max(highs) if sign > 0 else min(lows)
            worst = min(lows) if sign > 0 else max(highs)
            best_pct = sign * (best / entry - 1.0) * 100.0
            worst_pct = sign * (worst / entry - 1.0) * 100.0
            best_pnl = (best - entry) * qty * sign
            worst_pnl = (worst - entry) * qty * sign

            # v1.18 FILTER 2 -- an open position cannot have traded far below
            # its own stop; the broker-side stop would have filled. A reading
            # past that bound is a data fault, not a risk event. Dropping it
            # keeps MAE honest AND protects `_check_trailing`, which arms off
            # mfe_pct. A REAL breach still surfaces the only way it can: an
            # actual stop fill, and a realised loss bigger than intended.
            mult = float(
                getattr(self.cfg.runtime, "excursion_max_adverse_stop_mult", 0) or 0
            )
            stop_pct = abs(float(getattr(self.cfg.exits, "stop_pct", 0) or 0))
            if mult > 0 and stop_pct > 0:
                floor_pct = -stop_pct * mult
                if worst_pct < floor_pct:
                    if symbol not in self._impossible_mae_warned:
                        self._impossible_mae_warned.add(symbol)
                        self.log.warning(
                            "EXCURSION %s: DROPPED an impossible adverse reading "
                            "%+.2f%% -- past the %.2f%% stop by more than %.1fx "
                            "while the position is still open, so the price never "
                            "traded. Bad print in the %s feed, not a stop failure.",
                            symbol, worst_pct, -stop_pct, mult,
                            getattr(self.cfg.runtime, "data_feed", "?"),
                        )
                    worst_pct = None
                    worst_pnl = None

            e = self._excursion.get(symbol)
            if e is None:
                self._excursion[symbol] = {
                    "mfe": best_pnl,
                    "mae": worst_pnl if worst_pnl is not None else 0.0,
                    "mfe_pct": best_pct,
                    "mae_pct": worst_pct if worst_pct is not None else 0.0,
                }
                continue
            if best_pct > e["mfe_pct"]:
                if best_pct - e["mfe_pct"] >= 0.25:
                    self.log.info(
                        "EXCURSION %s: bar high lifts peak %+.2f%% -> %+.2f%% "
                        "(the 30s poll missed it).",
                        symbol, e["mfe_pct"], best_pct,
                    )
                e["mfe"], e["mfe_pct"] = best_pnl, best_pct
            if worst_pct is not None and worst_pct < e["mae_pct"]:
                e["mae"], e["mae_pct"] = worst_pnl, worst_pct

    def _check_time_stop(self, state: DailyState) -> None:
        """Close positions held longer than `exits.max_hold_minutes` (0=off).
        ATAI 2026-07-17: 5h19m in the trade for -0.3% — neither TP nor SL was
        ever threatened, and the capital was locked up all day."""
        max_hold = self.cfg.exits.max_hold_minutes
        if max_hold <= 0 or not state.entry_times:
            return
        try:
            positions = self.broker.list_positions()
        except Exception:  # noqa: BLE001
            return
        now = datetime.now(timezone.utc)
        for pos in positions:
            iso = state.entry_times.get(pos.symbol)
            if not iso:
                continue
            try:
                opened = datetime.fromisoformat(iso)
            except ValueError:
                continue
            held_min = (now - opened).total_seconds() / 60.0
            if held_min < max_hold:
                continue
            upl = float(getattr(pos, "unrealized_pl", 0) or 0)
            e = self._excursion.get(pos.symbol)
            exc = (
                f" [MFE {e['mfe']:+.2f}/{e['mfe_pct']:+.2f}%, "
                f"MAE {e['mae']:+.2f}/{e['mae_pct']:+.2f}%]" if e else ""
            )
            self.log.info(
                "TIME STOP %s: held %d min (>= %d) without hitting TP or SL "
                "(unrealized %+.2f)%s; closing.",
                pos.symbol, int(held_min), max_hold, upl, exc,
            )
            if self.broker.exit_position(pos.symbol):
                state.entry_times.pop(pos.symbol, None)
                save_state(state)
            else:
                self.log.error("Time-stop close failed for %s.", pos.symbol)

    # ---------- intraday re-screen ----------

    def _maybe_rescreen(self, state: DailyState, now_et, entry_cutoff) -> None:
        """Every `intraday_rescreen_minutes`, rerun the movers/news scan and
        add NEW qualifying symbols to the target list (until the pool is
        full). Gives the bot fresh candidates when the morning picks go
        nowhere, instead of watching the same stale list all day."""
        minutes = self.cfg.runtime.intraday_rescreen_minutes
        if minutes <= 0 or not self.cfg.screener.enabled or state.halted:
            return
        if now_et > entry_cutoff:
            return  # new entries aren't allowed anymore; nothing to gain
        room = self.cfg.selection.max_targets - len(state.targets)
        if room <= 0:
            return
        if time.monotonic() - self._last_rescreen < minutes * 60:
            return
        self._last_rescreen = time.monotonic()

        try:
            candidates = Screener(self.cfg, self.broker).build_candidates()
        except Exception as e:  # noqa: BLE001
            self.log.error("Intraday re-screen failed: %s", e)
            return
        known = (
            {t["symbol"] for t in state.targets}
            | set(state.blocked_symbols)
            | set(state.traded_symbols)
        )
        fresh = [c for c in candidates if c.symbol not in known]
        if not fresh:
            return
        news = self.news.score_symbols([c.symbol for c in fresh])
        # v1.7: underlyings already targeted or traded today count as taken.
        existing = list(
            {t["symbol"] for t in state.targets} | set(state.traded_symbols)
        )
        taken: set[str] = set()
        if self.cfg.selection.max_per_underlying > 0:
            enames = self._asset_names(existing)
            taken = {underlying_key(s, enames.get(s)) for s in existing}
        # v1.14: names already targeted/traded today also occupy their
        # correlation group, so a mid-morning re-screen cannot quietly add a
        # fifth crypto name to the four taken at the open.
        fresh_syms = [c.symbol for c in fresh]
        all_rets = self._daily_returns(fresh_syms + existing)
        new_targets = build_targets(
            self.cfg, fresh, news,
            asset_names=self._asset_names(fresh_syms),
            taken_underlyings=taken,
            daily_returns={s: all_rets[s] for s in fresh_syms if s in all_rets},
            taken_returns={s: all_rets[s] for s in existing if s in all_rets},
        )
        # Same rule as the pre-market scan: don't add un-shortable shorts.
        new_targets = [
            t for t in new_targets
            if not (t.bias == "short" and not self._is_shortable(t.symbol))
        ]
        added = new_targets[:room]
        if not added:
            return
        state.targets.extend(
            dict(t.as_state_dict(), intraday=True) for t in added
        )
        save_state(state)
        # Baseline their current articles so the news rescan only reacts to
        # genuinely fresh headlines from here on.
        self.news.mark_seen([t.symbol for t in added])
        self.log.info(
            "INTRADAY RE-SCREEN: added %s (pool %d/%d).",
            ", ".join(f"{t.symbol}({t.bias})" for t in added),
            len(state.targets), self.cfg.selection.max_targets,
        )
        # Warm baselines for the new names here, not at breakout time.
        self._prefetch_rel_volume([t.symbol for t in added])

    # ---------- intraday news ----------

    def _news_recheck(self, state: DailyState) -> None:
        interval = self.cfg.news.rescan_interval_seconds
        if interval <= 0 or self.news.client is None:
            return
        now = datetime.now(timezone.utc)
        if (
            self._last_news_check is not None
            and (now - self._last_news_check).total_seconds() < interval
        ):
            return
        # Overlap the window slightly; seen-article dedup prevents re-scoring.
        since = (self._last_news_check or now) - timedelta(minutes=10)
        self._last_news_check = now

        positions = {p.symbol: p for p in self.broker.list_positions()}
        symbols = list({t["symbol"] for t in state.targets} | set(positions))

        changed = False
        for symbol in symbols:
            sig = self.news.fresh_signal(symbol, since)
            if sig.n_articles == 0:
                continue
            self.log.info(
                "NEWS %s: %d fresh article(s), score %+.2f | %s",
                symbol, sig.n_articles, sig.score, sig.latest_headline,
            )

            # 1) Open position: exit early if fresh news is strongly adverse.
            pos = positions.get(symbol)
            if pos is not None:
                side = getattr(pos.side, "value", str(pos.side)).lower()
                side = "short" if "short" in side else "long"
                if should_exit_on_news(side, sig.score, self.cfg.news):
                    self.log.warning(
                        "ADVERSE NEWS EXIT: closing %s %s (fresh score %+.2f): %s",
                        side.upper(), symbol, sig.score, sig.latest_headline,
                    )
                    if self.broker.exit_position(symbol):
                        if symbol not in state.traded_symbols:
                            state.traded_symbols.append(symbol)
                        changed = True
                    else:
                        self.log.error("Failed to close %s on adverse news.", symbol)
                    continue

            # 2) Untraded target: update its bias if the fresh signal is strong.
            if abs(sig.score) >= self.cfg.news.min_sentiment_for_bias:
                new_bias = "long" if sig.score > 0 else "short"
                for t in state.targets:
                    if t["symbol"] == symbol and t.get("bias") != new_bias:
                        self.log.info(
                            "NEWS BIAS UPDATE: %s %s -> %s",
                            symbol, t.get("bias"), new_bias,
                        )
                        t["bias"] = new_bias
                        self._bias_skips_logged.discard(symbol)
                        changed = True
        if changed:
            save_state(state)

    # ---------- entries ----------

    def _prefetch_rel_volume(self, symbols: list[str]) -> None:
        """v1.12: warm the opening-volume baseline OUTSIDE the entry path.

        This is a ~27-day minute-bar fetch per symbol — by far the heaviest
        call the bot makes. Doing it lazily when a breakout confirms would put
        it between "breakout confirmed" and "submit order", the one place in
        the loop where latency costs a fill. The Alpaca SDK runs with no read
        timeout (2026-08-12 logged `get_news failed ... Read timed out.
        (read timeout=None)`), so a stall there could miss the entry outright.
        Pre-market and just after a re-screen, there is no rush.

        Failures are cached as None = "unknown", which never blocks an entry.
        """
        strat = self.cfg.strategy
        todo = [s for s in symbols if s not in self._or_relvol_avg]
        if not todo:
            return
        for sym in todo:
            try:
                self._or_relvol_avg[sym] = self.broker.get_avg_opening_volume(
                    sym, strat.opening_range_minutes,
                    strat.rel_volume_lookback_days,
                )
            except Exception as e:  # noqa: BLE001
                self.log.warning(
                    "rel-volume baseline failed for %s: %s", sym, e)
                self._or_relvol_avg[sym] = None
        known = sum(1 for s in todo if self._or_relvol_avg.get(s))
        self.log.info(
            "RELVOL baselines: %d/%d symbol(s) measurable (%d session lookback).",
            known, len(todo), strat.rel_volume_lookback_days,
        )

    def _scan_for_entries(
        self, state: DailyState, open_et: datetime, or_end: datetime
    ) -> None:
        strat = self.cfg.strategy
        open_utc = open_et.astimezone(timezone.utc)
        or_end_utc = or_end.astimezone(timezone.utc)

        for target in state.targets:
            symbol = target["symbol"]
            bias = target.get("bias", "any")

            if state.trades_opened >= strat.max_trades_per_day:
                break
            if symbol in state.blocked_symbols:
                continue
            if strat.one_trade_per_symbol and symbol in state.traded_symbols:
                continue
            if self.broker.has_position(symbol):
                continue

            or_bars = self.broker.get_minute_bars(symbol, open_utc, or_end_utc)
            rng = compute_opening_range(symbol, or_bars)
            if rng is None:
                continue

            price = self.broker.get_latest_price(symbol)
            if price is None:
                continue

            side = check_breakout(rng, price, strat)
            if side is None:
                continue

            # Respect the news-implied direction (log once per symbol per day).
            if strat.respect_news_bias and bias != "any" and side != bias:
                if symbol not in self._bias_skips_logged:
                    self._bias_skips_logged.add(symbol)
                    self.log.info(
                        "%s broke out %s but news bias is %s; skipping "
                        "(further skips logged silently).",
                        symbol, side, bias,
                    )
                continue

            # Can't short it: suppress the short side only and keep watching
            # for a long breakout (log once per symbol per day).
            if side == "short" and not self._is_shortable(symbol):
                if symbol not in self._short_skips_logged:
                    self._short_skips_logged.add(symbol)
                    self.log.info(
                        "%s broke down but is not shortable; ignoring short side,"
                        " still watching for a long breakout.", symbol,
                    )
                continue

            # Bar-close confirmation + volume filter: a single print beyond
            # the range isn't enough; a completed 1-min bar (within the
            # lookback) must close outside it, and that bar must carry enough
            # volume when the filter is on. Not confirmed -> just wait; we
            # re-check every loop.
            now_utc = datetime.now(timezone.utc)
            cur_min = now_utc.replace(second=0, microsecond=0)
            recent = self.broker.get_minute_bars(symbol, or_end_utc, now_utc)
            completed = [b for b in recent if b.timestamp < cur_min]
            confirm_bar = find_confirm_bar(rng, side, completed, strat)
            if confirm_bar is None or not (
                passes_volume_filter(confirm_bar, or_bars, strat)
            ):
                if symbol not in self._filter_skips_logged:
                    self._filter_skips_logged.add(symbol)
                    self.log.info(
                        "%s broke out %s but not confirmed (bar close/volume);"
                        " waiting (further waits logged silently).",
                        symbol, side,
                    )
                continue

            # v1.12: opening relative volume. The research finding this bot
            # was missing: an ORB's edge tracks whether the name is unusually
            # active FOR ITSELF, not whether it gapped or had a headline.
            # Measured once per symbol per day, at confirmation. Logged
            # always; only gates the entry when min_opening_rel_volume > 0.
            if symbol not in self._or_relvol:
                today_or_vol = sum(float(b.volume or 0) for b in or_bars)
                # Baseline comes from the pre-warmed cache — never a network
                # call here. A miss just means "unknown", which never blocks.
                avg_or_vol = self._or_relvol_avg.get(symbol)
                rv = (today_or_vol / avg_or_vol) if avg_or_vol else None
                self._or_relvol[symbol] = rv
                self.log.info(
                    "RELVOL %s: opening range volume %.0f vs %s avg -> %s",
                    symbol, today_or_vol,
                    f"{avg_or_vol:,.0f}" if avg_or_vol else "unknown",
                    f"{rv:.2f}x" if rv is not None else "unknown",
                )
            rel_vol_open = self._or_relvol[symbol]
            # Unknown history is NOT a failure: skipping every name we can't
            # measure would silently gut the universe (the v1.11.1 liquidity
            # floor did exactly that on 08-11 and left one target all day).
            if (
                strat.min_opening_rel_volume > 0
                and rel_vol_open is not None
                and rel_vol_open < strat.min_opening_rel_volume
            ):
                if symbol not in self._filter_skips_logged:
                    self._filter_skips_logged.add(symbol)
                    self.log.info(
                        "%s breakout %s skipped: opening rel volume %.2fx < "
                        "%.2fx floor (further skips logged silently).",
                        symbol, side, rel_vol_open,
                        strat.min_opening_rel_volume,
                    )
                continue

            # Spread filter: wide bid-ask means bad fills and understated risk.
            spread = self.broker.get_spread_pct(symbol)
            if (
                strat.max_spread_pct > 0
                and spread is not None
                and spread > strat.max_spread_pct
            ):
                if symbol not in self._filter_skips_logged:
                    self._filter_skips_logged.add(symbol)
                    self.log.info(
                        "%s breakout %s skipped: spread %.2f%% > %.2f%% cap "
                        "(further skips logged silently).",
                        symbol, side, spread, strat.max_spread_pct,
                    )
                continue

            plan = build_trade_plan(
                rng, side, price, self.broker.get_equity(),
                self.cfg.exits, self.cfg.sizing,
            )
            if plan is None:
                self.log.info("%s breakout %s but no valid trade plan.", symbol, side)
                continue

            # v1.15: affordability pre-check. Sizing caps at
            # sizing.max_position_notional and never consults the account, so
            # on a small balance the Nth concurrent entry is guaranteed to be
            # rejected: 2026-09-09 SNXX asked for $685.99 against $669.78 of
            # buying power, failed three times and got BLOCKED for the day, with
            # an ERROR that read like a bug rather than a balance limit. Skip
            # cleanly instead. None = telemetry failure, so fall through and let
            # the broker decide rather than silently refusing to trade.
            need = plan.qty * price
            have = self.broker.get_buying_power()
            if have is not None and need > have:
                if symbol not in self._filter_skips_logged:
                    self._filter_skips_logged.add(symbol)
                    self.log.info(
                        "%s breakout %s skipped: needs $%.2f but buying power is "
                        "$%.2f (position cap $%.0f, %d open). Not an error — the "
                        "account is fully deployed.",
                        symbol, side, need, have,
                        self.cfg.sizing.max_position_notional,
                        len(state.traded_symbols),
                    )
                continue

            try:
                order = self.broker.submit_bracket(
                    symbol=plan.symbol,
                    qty=plan.qty,
                    side=plan.side,
                    take_profit_price=plan.take_profit,
                    stop_loss_price=plan.stop_loss,
                    stop_limit_price=plan.stop_limit,
                    entry_limit_price=plan.entry_limit,
                )
            except Exception as e:  # noqa: BLE001
                self._handle_order_failure(state, symbol, e)
                continue

            self._order_failures.pop(symbol, None)
            state.traded_symbols.append(symbol)
            state.trades_opened += 1
            state.entry_times[symbol] = now_utc.isoformat()  # for the time stop
            save_state(state)
            self.log.info(
                "ENTER %s %s qty=%d ref=%.2f TP=%.2f(+%.1f%%) SL=%.2f(-%.1f%%)%s%s",
                plan.side.upper(), symbol, plan.qty, plan.entry_ref,
                plan.take_profit, plan.tp_pct, plan.stop_loss, plan.stop_pct,
                f" stop-limit={plan.stop_limit:.2f}" if plan.stop_limit else "",
                f" entry-limit={plan.entry_limit:.2f}" if plan.entry_limit else "",
            )
            # Setup features for later analysis (join with trade_history).
            report.append_features({
                "date": open_et.date().isoformat(),
                "entry_time_et": now_utc.astimezone(ET).strftime("%H:%M:%S"),
                "symbol": symbol,
                "side": plan.side,
                "qty": plan.qty,
                "entry_ref": plan.entry_ref,
                "or_pct": round(rng.size / price * 100.0, 3) if price else "",
                "gap_pct": target.get("gap_pct", ""),
                "sentiment": target.get("sentiment", ""),
                "n_articles": target.get("n_articles", ""),
                "rel_volume": target.get("rel_volume", ""),
                # v1.12: the real one — own opening volume vs own 14d average.
                # `rel_volume` above is a cross-sectional rank against the
                # day's biggest name, which is mostly a market-cap proxy.
                "rel_volume_open": (
                    round(rel_vol_open, 3) if rel_vol_open is not None else ""
                ),
                "spread_pct": round(spread, 3) if spread is not None else "",
                "minutes_after_open":
                    int((now_utc - open_utc).total_seconds() // 60),
                "tp_pct": plan.tp_pct,
                "stop_pct": plan.stop_pct,
                "bias": bias,
                "added_intraday": 1 if target.get("intraday") else 0,
            })

            if not self._adjust_exits_to_fill(order, plan):
                self._pending_entries[symbol] = {
                    "order_id": order.id,
                    "plan": plan,
                    "submitted": time.monotonic(),
                    "cancel_requested": False,
                }

    # Broker error fragments that mean "this will never succeed today".
    _FATAL_ORDER_ERRORS = (
        "cannot be sold short",
        "not shortable",
        "42210000",
        "asset is not active",
        "asset not found",
        "is not tradable",
    )

    def _handle_order_failure(self, state: DailyState, symbol: str, err: Exception) -> None:
        """Log an order failure (deduped) and blacklist the symbol when the
        error is unrecoverable or keeps repeating."""
        msg = str(err)
        fails = self._order_failures.get(symbol, 0) + 1
        self._order_failures[symbol] = fails
        if fails == 1:
            self.log.error("Order failed for %s: %s", symbol, msg)
        if any(frag in msg.lower() for frag in self._FATAL_ORDER_ERRORS):
            self._block_symbol(state, symbol, f"unrecoverable order error: {msg}")
        elif fails >= self.MAX_ORDER_FAILURES:
            self._block_symbol(
                state, symbol, f"{fails} failed order submissions (last: {msg})"
            )

    def _adjust_exits_to_fill(self, order, plan) -> bool:
        """Wait for the entry fill and re-anchor TP/SL to the actual fill
        price so slippage on entry doesn't compress the intended reward:risk
        ratio. Returns True once the fill is confirmed; False on timeout (the
        caller then tracks the order for late-fill/cancel reconciliation)."""
        fill = self.broker.wait_for_fill(order.id, timeout_s=20)
        if fill is None:
            self.log.warning(
                "%s: no fill confirmation within 20s; tracking order for a "
                "late fill (will cancel and free the trade slot after %ds "
                "unfilled).", plan.symbol, self.ENTRY_FILL_GRACE_S,
            )
            return False
        self._reanchor_exits(order.id, plan, fill)
        return True

    def _reanchor_exits(self, order_id, plan, fill: float) -> None:
        drift = abs(fill - plan.entry_ref) / plan.entry_ref if plan.entry_ref else 0.0
        if drift < 0.001:  # < 0.1% slippage: not worth re-pricing
            return
        sign = 1 if plan.side == "long" else -1
        tp = fill * (1 + sign * plan.tp_pct / 100.0)
        sl = fill * (1 - sign * plan.stop_pct / 100.0)
        sl_limit = None
        if plan.stop_limit is not None:
            off = self.cfg.exits.stop_limit_offset_pct / 100.0
            sl_limit = sl * (1 - sign * off)
        if self.broker.adjust_bracket_legs(order_id, tp, sl, sl_limit):
            self.log.info(
                "%s: fill %.2f (ref %.2f, %+.2f%% slippage); exits re-anchored "
                "TP=%.2f SL=%.2f.",
                plan.symbol, fill, plan.entry_ref,
                (fill - plan.entry_ref) / plan.entry_ref * 100.0, tp, sl,
            )
        else:
            self.log.warning(
                "%s: fill %.2f deviates from ref %.2f but exit legs could not "
                "be re-priced; keeping original exits.",
                plan.symbol, fill, plan.entry_ref,
            )

    def _reconcile_pending_entries(self, state: DailyState) -> None:
        """Resolve entries whose fill was never confirmed.

        A late fill keeps the trade (exits re-anchored to the fill price).
        A dead order (canceled/expired/rejected) or one still unfilled after
        ENTRY_FILL_GRACE_S is canceled and rolled back so the trade slot and
        symbol are freed for the rest of the day.
        """
        for symbol in list(self._pending_entries):
            pe = self._pending_entries[symbol]
            order = self.broker.get_order(pe["order_id"])
            if order is None:
                continue  # transient API error; retry next loop
            status = getattr(order.status, "value", str(order.status)).lower()
            filled_qty = float(getattr(order, "filled_qty", 0) or 0)
            fill_price = float(getattr(order, "filled_avg_price", 0) or 0)

            # Any fill (even partial) means we hold stock: keep the trade.
            if filled_qty > 0 and fill_price > 0:
                self.log.info(
                    "%s: entry filled late at %.2f (%s); keeping trade.",
                    symbol, fill_price, status,
                )
                self._reanchor_exits(pe["order_id"], pe["plan"], fill_price)
                del self._pending_entries[symbol]
                continue

            if status in ("canceled", "cancelled", "expired", "rejected",
                          "done_for_day"):
                self._rollback_entry(state, symbol, f"entry order {status}, no fill")
                continue

            if (not pe["cancel_requested"]
                    and time.monotonic() - pe["submitted"] >= self.ENTRY_FILL_GRACE_S):
                self.log.warning(
                    "%s: entry still unfilled after %ds; canceling order.",
                    symbol, self.ENTRY_FILL_GRACE_S,
                )
                self.broker.cancel_order(pe["order_id"])
                # Don't roll back yet: a fill can race the cancel. The final
                # status lands on a later loop and is handled above.
                pe["cancel_requested"] = True

    def _check_breakeven(self) -> None:
        """Once a position is up `breakeven_trigger_pct` (unrealized), move
        its stop to the entry price: a confirmed winner can no longer turn
        into a full loser. Runs every loop; each symbol is moved once/day."""
        trigger = self.cfg.exits.breakeven_trigger_pct
        if trigger <= 0:
            return
        try:
            positions = self.broker.list_positions()
        except Exception:  # noqa: BLE001
            return
        for pos in positions:
            symbol = pos.symbol
            if symbol in self._breakeven_done:
                continue
            try:
                entry = float(pos.avg_entry_price)
                upl_pct = float(pos.unrealized_plpc) * 100.0
            except (TypeError, ValueError):
                continue
            if entry <= 0 or upl_pct < trigger:
                continue
            side = getattr(pos.side, "value", str(pos.side)).lower()
            side = "short" if "short" in side else "long"
            sl_limit = None
            if self.cfg.exits.stop_limit:
                off = self.cfg.exits.stop_limit_offset_pct / 100.0
                sl_limit = entry * (1 - off) if side == "long" else entry * (1 + off)
            if self.broker.move_stop(symbol, entry, sl_limit):
                self._breakeven_done.add(symbol)
                self.log.info(
                    "BREAKEVEN %s: up %.2f%% (>= %.2f%%); stop moved to entry"
                    " %.2f.", symbol, upl_pct, trigger, entry,
                )
            # On failure we retry next loop (transient API errors happen).

    def _check_trailing(self) -> None:
        """v1.11: server-side trailing stops.

        The v1.8 approach (poll excursion every 30s, ratchet the bracket's
        stop leg via replace_order) NEVER worked in live trading: every
        replace failed silently Aug 3-6 — CWVX peaked +3.88% on Aug 4 and
        still exited at the full initial stop. Instead, once a position's
        peak is far enough past breakeven that a `trail_pct` trail would
        lock in >= breakeven, we cancel the bracket legs and hand the exit
        to a native Alpaca TRAILING-STOP order: the high-water mark is then
        tracked server-side tick by tick. One arming action per position,
        loudly logged, with a plain-stop fallback if the handoff fails."""
        trail = self.cfg.exits.trail_pct
        if trail <= 0:
            return
        try:
            positions = self.broker.list_positions()
        except Exception:  # noqa: BLE001
            return
        live_symbols = set()
        for pos in positions:
            symbol = pos.symbol
            live_symbols.add(symbol)
            if symbol in self._trail_active:
                continue  # Alpaca is trailing this one already
            exc = self._excursion.get(symbol)
            if not exc:
                continue
            try:
                entry = float(pos.avg_entry_price)
                qty = abs(int(float(pos.qty)))
            except (TypeError, ValueError):
                continue
            if entry <= 0 or qty <= 0:
                continue
            mfe_pct = exc["mfe_pct"]
            if mfe_pct <= 0:
                continue
            side = getattr(pos.side, "value", str(pos.side)).lower()
            side = "short" if "short" in side else "long"
            sign = 1 if side == "long" else -1
            # v1.15 (2026-09-09): anchor on the CURRENT price, not the peak.
            # Alpaca seeds the trailing high-water mark from the price at the
            # moment the order is accepted (see broker.activate_trailing_stop),
            # so the level actually protected has nothing to do with a peak the
            # 30s poll reconstructed from a bar high. Arming off `mfe_pct` meant
            # that whenever price had already retraced, the handoff replaced a
            # live -stop_pct bracket stop with a trail anchored BELOW breakeven.
            # 2026-09-09 IRD: peak +3.22% (bar high the poll missed), armed, log
            # claimed "would lock ~+1.93%", realized -0.73%. The arm=breakeven
            # semantics the sweeps validated require the anchor to be real.
            cur_pct = float(getattr(pos, "unrealized_plpc", 0) or 0) * 100.0
            anchor = entry * (1 + sign * cur_pct / 100.0)
            desired = anchor * (1 - sign * trail / 100.0)
            # Arm only once trailing FROM HERE would lock >= breakeven; until
            # then the initial bracket stop governs.
            if (desired - entry) * sign <= 0:
                # Say so once per symbol. A peak that qualifies but a current
                # price that doesn't is exactly the state that used to arm
                # silently and lock a loss.
                if mfe_pct > trail and symbol not in self._trail_deferred:
                    self._trail_deferred.add(symbol)
                    self.log.info(
                        "TRAIL DEFERRED %s: peak %+.2f%% qualifies but price is "
                        "back at %+.2f%%; trailing from here would lock %+.2f%%. "
                        "Keeping the -%.2f%% bracket stop.",
                        symbol, mfe_pct, cur_pct,
                        (desired / entry - 1) * 100.0 * sign,
                        self.cfg.exits.stop_pct,
                    )
                continue
            fallback_stop = entry * (1 - sign * self.cfg.exits.stop_pct / 100.0)
            fallback_limit = None
            if self.cfg.exits.stop_limit:
                off = self.cfg.exits.stop_limit_offset_pct / 100.0
                fallback_limit = fallback_stop * (1 - sign * off)
            if self.broker.activate_trailing_stop(
                symbol, qty, side, trail,
                fallback_stop=fallback_stop, fallback_limit=fallback_limit,
            ):
                self._trail_active.add(symbol)
                self._trail_deferred.discard(symbol)
                locked = (desired / entry - 1) * 100.0 * sign
                self.log.info(
                    "TRAIL ACTIVATED %s: peak %+.2f%%, armed at %+.2f%%; native "
                    "trailing stop %.2f%% handed to Alpaca, anchored at the "
                    "CURRENT price (locks ~%+.2f%% if it turns here).",
                    symbol, mfe_pct, cur_pct, trail, locked,
                )
            # On failure activate_trailing_stop already logged loudly and
            # restored a plain stop; we retry the handoff next loop.
        # Detect trail fills: an armed symbol whose position is gone exited
        # via the trailing stop (bookkeeping classifies these as "trail").
        for symbol in list(self._trail_active):
            if symbol not in live_symbols:
                self._trail_active.discard(symbol)
                e = self._excursion.get(symbol)
                peak_s = f" (peak was {e['mfe_pct']:+.2f}%)" if e else ""
                self.log.info(
                    "TRAIL EXIT %s: trailing stop filled%s.", symbol, peak_s
                )

    def _rollback_entry(self, state: DailyState, symbol: str, reason: str) -> None:
        """Undo the per-day bookkeeping for an entry that never filled."""
        self._pending_entries.pop(symbol, None)
        if symbol in state.traded_symbols:
            state.traded_symbols.remove(symbol)
        state.entry_times.pop(symbol, None)
        if state.trades_opened > 0:
            state.trades_opened -= 1
        save_state(state)
        self.log.warning(
            "ENTRY ROLLED BACK %s: %s; trade slot freed.", symbol, reason
        )

    # ---------- end of day ----------

    def _final_report(self, state: DailyState, session_start: datetime) -> None:
        # Entries still unconfirmed at session end (the close flatten cancels
        # open orders): if they never filled, roll them back so the day count
        # and EOD pairing only reflect real trades.
        for symbol in list(self._pending_entries):
            try:
                order = self.broker.get_order(self._pending_entries[symbol]["order_id"])
                filled_qty = float(getattr(order, "filled_qty", 0) or 0) if order else 0.0
                if filled_qty <= 0:
                    self._rollback_entry(state, symbol, "session ended with no fill")
                else:
                    self._pending_entries.pop(symbol, None)
            except Exception as e:  # noqa: BLE001
                self.log.error("Could not reconcile pending entry %s: %s", symbol, e)

        try:
            equity = self.broker.get_equity()
            pnl = equity - state.start_equity
            pct = (pnl / state.start_equity * 100.0) if state.start_equity else 0.0
            self.log.info("-" * 60)
            self.log.info(
                "Day done. Trades: %d | End equity: %.2f | PnL: %+.2f (%+.2f%%)",
                state.trades_opened, equity, pnl, pct,
            )
            if state.halted:
                self.log.info("Halt reason: %s", state.halt_reason)
        except Exception as e:  # noqa: BLE001
            self.log.error("Could not produce final report: %s", e)

        # End-of-day trade summary + running trial stats.
        try:
            date_str = self._now_et().date().isoformat()
            since = session_start - timedelta(minutes=30)
            # Closing fills can take a while to land; retry until every trade
            # we opened pairs into a completed round trip (or the wait
            # expires). Anything still unpaired is recovered by the startup
            # backfill on the next run.
            deadline = time.monotonic() + self.EOD_PAIRING_WAIT_S
            trades = []
            while True:
                time.sleep(5)
                fills = self.broker.get_todays_fills(since)
                trades = report.pair_fills(fills, date_str)
                if len(trades) >= state.trades_opened or time.monotonic() >= deadline:
                    break
                self.log.info(
                    "Waiting for closing fills (%d/%d round trips paired)...",
                    len(trades), state.trades_opened,
                )
            if len(trades) < state.trades_opened:
                self.log.warning(
                    "Only %d of %d trades paired after %ds; history may be "
                    "incomplete for %s (startup backfill will recover it).",
                    len(trades), state.trades_opened,
                    self.EOD_PAIRING_WAIT_S, date_str,
                )
            report.append_trades(trades)
            # Cross-day sweep: recovers round trips today's pairing can't see
            # (e.g. an overnight leftover closed this morning whose entry
            # fill was yesterday) before the summary is printed (v1.5).
            try:
                added = report.backfill_history(self.broker)
                if added:
                    self.log.info(
                        "BACKFILL: recovered %d cross-day trade(s).", added
                    )
            except Exception as e:  # noqa: BLE001
                self.log.error("EOD backfill failed: %s", e)
            report.log_summary(trades)
            # Excursion recap (v1.6 instrumentation): peak favorable / worst
            # adverse each position reached, to evaluate a future trailing stop.
            if self._excursion:
                self.log.info("  EXCURSION (peak favorable / worst adverse):")
                for sym in sorted(self._excursion):
                    ex = self._excursion[sym]
                    self.log.info(
                        "    %-6s MFE %+.2f (%+.2f%%) | MAE %+.2f (%+.2f%%)",
                        sym, ex["mfe"], ex["mfe_pct"], ex["mae"], ex["mae_pct"],
                    )
        except Exception as e:  # noqa: BLE001
            self.log.error("Could not write end-of-day summary: %s", e)
