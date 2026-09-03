"""Daily state persistence: the 'arm' permission and per-day tracking.

The bot only trades on a given calendar day if you have ARMED it for that day.
Arming is done with `python arm.py` (see that script). This is the daily
permission switch you asked for: each morning you grant permission, and the
bot trades that day without asking again.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .config import ROOT

STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "daily_state.json"


@dataclass
class DailyState:
    # The calendar day (US/Eastern) this state applies to, ISO format.
    trade_date: str = ""
    # Has the user armed the bot for trade_date?
    armed: bool = False
    # Equity recorded when the bot started trading today (basis for PnL %).
    start_equity: float = 0.0
    # Symbols we have already opened a trade on today.
    traded_symbols: list[str] = field(default_factory=list)
    # The day's chosen targets from the pre-market scan: [{"symbol","bias"}, ...]
    targets: list[dict] = field(default_factory=list)
    # Has the pre-market scan already run today?
    scanned: bool = False
    # Number of new trades opened today.
    trades_opened: int = 0
    # Symbols blocked for the rest of the day (not shortable, repeated order
    # rejections, ...). The bot will not attempt entries on these again.
    blocked_symbols: list[str] = field(default_factory=list)
    # Has a circuit breaker tripped today? If so we stop opening trades.
    halted: bool = False
    # Why we halted, for the log/report.
    halt_reason: str = ""
    # v1.5: entry submit time per symbol (ISO UTC), for the time stop.
    # Persisted so a bot restart doesn't reset the clock on open positions.
    entry_times: dict = field(default_factory=dict)

    def is_for_today(self, today: date) -> bool:
        return self.trade_date == today.isoformat()


def _ensure_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def load_state() -> DailyState:
    if not STATE_FILE.exists():
        return DailyState()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return DailyState(**data)
    except (json.JSONDecodeError, TypeError):
        return DailyState()


def save_state(state: DailyState) -> None:
    _ensure_dir()
    STATE_FILE.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def arm_for(today: date) -> DailyState:
    """Arm the bot for the given day. Resets per-day counters."""
    state = DailyState(trade_date=today.isoformat(), armed=True)
    save_state(state)
    return state


def disarm() -> None:
    """Turn off permission. The bot will idle and not open trades."""
    state = load_state()
    state.armed = False
    save_state(state)
