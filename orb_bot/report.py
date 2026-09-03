"""End-of-day reporting: trade history + running trial metrics.

After each session the runner pulls the day's fills from Alpaca, reconstructs
completed round-trip trades, appends them to state/trade_history.csv, and logs
the running stats that matter for the paper trial: win rate, expectancy, and
max drawdown. `python report.py` (project root) prints the same stats anytime.

Pure logic (pair_fills / compute_stats) has no Alpaca dependency so it's
unit-testable with synthetic data.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ROOT
from .logutil import get_logger

log = get_logger()

HISTORY_FILE = ROOT / "state" / "trade_history.csv"
FIELDS = [
    "date", "symbol", "side", "qty", "entry_price", "exit_price",
    "pnl", "pnl_pct",
]

# Per-entry setup features, written at entry time. Join with trade_history on
# (date, symbol) to learn which setups actually win once there's enough data.
FEATURES_FILE = ROOT / "state" / "trade_features.csv"
FEATURE_FIELDS = [
    "date", "entry_time_et", "symbol", "side", "qty", "entry_ref",
    "or_pct",          # opening-range size as % of price (volatility)
    "gap_pct",         # gap at scan time
    "sentiment",       # news score at selection
    "n_articles",
    "rel_volume",      # cross-sectional rank vs the day's biggest active
    "rel_volume_open",  # v1.12: own opening-range volume / own 14d average
    "spread_pct",      # bid-ask spread at entry ("" if unavailable)
    "minutes_after_open",
    "tp_pct", "stop_pct",
    "bias",
    "added_intraday",  # 1 if the target came from the intraday re-screen
]


@dataclass
class TradeRecord:
    date: str
    symbol: str
    side: str  # "long" or "short"
    qty: float
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float


def pair_fills(fills: list[dict], date_str: str) -> list[TradeRecord]:
    """Reconstruct completed round trips from a day's fills.

    fills: [{symbol, side ('buy'/'sell'), qty, price, time}], time-ordered.
    A round trip completes when a symbol's net position returns to zero.
    Every trade is dated `date_str`.
    """
    return _pair(fills, lambda f: date_str)


def pair_fills_all(fills: list[dict]) -> list[TradeRecord]:
    """Pair round trips across the WHOLE fill window, not per calendar day.

    v1.5: the old per-day grouping (pair_fills_by_day) could never pair a
    round trip that spanned days — exactly what happens when an EOD close
    fill is lost and the position carries overnight (INTC 2026-07-16/17).
    Each completed trip is dated by its CLOSING fill's ET date. Fills with
    no timestamp are skipped (can't be dated reliably)."""
    et = ZoneInfo("America/New_York")
    timed = [f for f in fills if f.get("time") is not None]
    return _pair(
        timed, lambda f: f["time"].astimezone(et).date().isoformat()
    )


def _pair(fills: list[dict], date_of) -> list[TradeRecord]:
    """Shared pairing engine. `date_of(closing_fill)` dates each round trip."""
    trades: list[TradeRecord] = []
    # Per-symbol running state.
    pos: dict[str, dict] = {}

    for f in fills:
        sym = f["symbol"]
        signed = f["qty"] if f["side"] == "buy" else -f["qty"]
        st = pos.setdefault(
            sym, {"qty": 0.0, "entry_cost": 0.0, "exit_cost": 0.0, "exit_qty": 0.0}
        )

        if st["qty"] == 0:
            # Opening a new position.
            st.update(
                qty=signed,
                entry_cost=f["qty"] * f["price"],
                exit_cost=0.0,
                exit_qty=0.0,
            )
            continue

        same_direction = (st["qty"] > 0) == (signed > 0)
        if same_direction:
            st["qty"] += signed
            st["entry_cost"] += f["qty"] * f["price"]
            continue

        # Reducing / closing.
        st["exit_cost"] += f["qty"] * f["price"]
        st["exit_qty"] += f["qty"]
        st["qty"] += signed

        if abs(st["qty"]) < 1e-9:
            entry_qty = st["exit_qty"]
            entry_price = st["entry_cost"] / entry_qty if entry_qty else 0.0
            exit_price = st["exit_cost"] / st["exit_qty"] if st["exit_qty"] else 0.0
            long_side = signed < 0  # closed by selling => was long
            pnl = (
                (exit_price - entry_price) if long_side else (entry_price - exit_price)
            ) * entry_qty
            pnl_pct = (
                (pnl / (entry_price * entry_qty) * 100.0)
                if entry_price and entry_qty
                else 0.0
            )
            trades.append(
                TradeRecord(
                    date=date_of(f),
                    symbol=sym,
                    side="long" if long_side else "short",
                    qty=entry_qty,
                    entry_price=round(entry_price, 4),
                    exit_price=round(exit_price, 4),
                    pnl=round(pnl, 2),
                    pnl_pct=round(pnl_pct, 3),
                )
            )
            pos.pop(sym, None)

    return trades


def backfill_history(broker, days: int = 5) -> int:
    """Reconcile trade_history.csv against the broker's fills from the last
    `days` days. Recovers round trips whose closing fills landed after the
    end-of-day pairing gave up (slow fills right at the close) AND trips
    that spanned days (a position wrongly carried overnight — v1.5).
    Read-only against the broker; only appends missing rows to the CSV.
    Returns the number of rows added."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=days)
    fills = broker.get_todays_fills(since)
    if not fills:
        return 0
    return append_trades(pair_fills_all(fills))


@dataclass
class TrialStats:
    n_trades: int
    n_wins: int
    win_rate: float       # 0..100
    avg_win: float        # $
    avg_loss: float       # $ (negative)
    expectancy: float     # $ per trade
    expectancy_pct: float # % per trade
    total_pnl: float      # $
    max_drawdown: float   # $ (peak-to-trough on the per-trade equity curve)


def compute_stats(pnls: list[float], pnl_pcts: list[float]) -> TrialStats:
    n = len(pnls)
    if n == 0:
        return TrialStats(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0

    # Max drawdown on the cumulative per-trade PnL curve.
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return TrialStats(
        n_trades=n,
        n_wins=len(wins),
        win_rate=round(len(wins) / n * 100.0, 1),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        expectancy=round(sum(pnls) / n, 2),
        expectancy_pct=round(sum(pnl_pcts) / n, 3),
        total_pnl=round(sum(pnls), 2),
        max_drawdown=round(max_dd, 2),
    )


# ----- persistence -----

def _load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    with open(HISTORY_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_trades(trades: list[TradeRecord]) -> int:
    """Append trades not already in the history CSV. Returns rows added."""
    if not trades:
        return 0
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_history()
    seen = {(r["date"], r["symbol"], r["entry_price"], r["exit_price"]) for r in existing}
    new_rows = [
        t for t in trades
        if (t.date, t.symbol, str(t.entry_price), str(t.exit_price)) not in seen
    ]
    if not new_rows:
        return 0
    write_header = not HISTORY_FILE.exists()
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        for t in new_rows:
            w.writerow(
                {
                    "date": t.date, "symbol": t.symbol, "side": t.side,
                    "qty": t.qty, "entry_price": t.entry_price,
                    "exit_price": t.exit_price, "pnl": t.pnl, "pnl_pct": t.pnl_pct,
                }
            )
    return len(new_rows)


def append_features(row: dict) -> None:
    """Append one entry's setup features to state/trade_features.csv."""
    try:
        FEATURES_FILE.parent.mkdir(parents=True, exist_ok=True)
        write_header = not FEATURES_FILE.exists()
        with open(FEATURES_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FEATURE_FIELDS, extrasaction="ignore")
            if write_header:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in FEATURE_FIELDS})
    except Exception as e:  # noqa: BLE001
        log.error("Could not write trade features: %s", e)


def running_stats() -> TrialStats:
    rows = _load_history()
    pnls = [float(r["pnl"]) for r in rows]
    pcts = [float(r["pnl_pct"]) for r in rows]
    return compute_stats(pnls, pcts)


def log_summary(todays_trades: list[TradeRecord]) -> None:
    """Log the end-of-day block: today's round trips + running trial stats."""
    log.info("=" * 60)
    log.info("END-OF-DAY SUMMARY")
    if todays_trades:
        for t in todays_trades:
            log.info(
                "  %s %s qty=%g  %.2f -> %.2f  PnL %+.2f (%+.2f%%)",
                t.symbol, t.side.upper(), t.qty,
                t.entry_price, t.exit_price, t.pnl, t.pnl_pct,
            )
    else:
        log.info("  No completed round-trip trades today.")

    s = running_stats()
    if s.n_trades == 0:
        log.info("  Trial history is empty so far.")
        return
    log.info(
        "  TRIAL SO FAR: %d trades | win rate %.1f%% | expectancy %+.2f/trade "
        "(%+.3f%%) | total PnL %+.2f | max drawdown %.2f",
        s.n_trades, s.win_rate, s.expectancy, s.expectancy_pct,
        s.total_pnl, s.max_drawdown,
    )
    log.info(
        "  avg win %+.2f | avg loss %+.2f | %d/%d winners",
        s.avg_win, s.avg_loss, s.n_wins, s.n_trades,
    )
