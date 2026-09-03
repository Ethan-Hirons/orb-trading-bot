"""Daily risk circuit breaker.

Compares current account equity against the equity recorded when trading
started today, and decides whether to keep trading, stop on profit, or stop
on loss. This implements your rule: once it hits a certain profit OR a certain
loss, it stops trading for the day.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config import RiskConfig


class BreakerStatus(str, Enum):
    OK = "ok"
    PROFIT_TARGET = "profit_target"
    MAX_LOSS = "max_loss"


@dataclass
class RiskAssessment:
    status: BreakerStatus
    pnl: float
    pnl_pct: float
    message: str


class RiskManager:
    def __init__(self, cfg: RiskConfig, start_equity: float):
        self.cfg = cfg
        self.start_equity = max(start_equity, 0.0)

    def assess(self, current_equity: float) -> RiskAssessment:
        if self.start_equity <= 0:
            return RiskAssessment(BreakerStatus.OK, 0.0, 0.0, "No starting equity.")

        pnl = current_equity - self.start_equity
        pnl_pct = (pnl / self.start_equity) * 100.0

        # v1.12 (2026-08-13): 0 (or negative) DISABLES the profit breaker.
        # Without this guard a 0 target would fire the instant the day ticks
        # green. A profit breaker caps the right tail, which is the only part
        # of this distribution that pays for everything else: on 2026-08-03 it
        # stopped the best day of the trial (+127.55) at +6.14%. The daily LOSS
        # breaker and the EOD flatten are the risk controls; this one was not.
        target = self.cfg.daily_profit_target_pct
        if target > 0 and pnl_pct >= target:
            return RiskAssessment(
                BreakerStatus.PROFIT_TARGET,
                pnl,
                pnl_pct,
                f"Profit target hit: {pnl_pct:+.2f}% "
                f"(target {target:.2f}%). Stopping for the day.",
            )

        if pnl_pct <= -abs(self.cfg.daily_max_loss_pct):
            return RiskAssessment(
                BreakerStatus.MAX_LOSS,
                pnl,
                pnl_pct,
                f"Max loss hit: {pnl_pct:+.2f}% "
                f"(limit -{abs(self.cfg.daily_max_loss_pct):.2f}%). Stopping for the day.",
            )

        return RiskAssessment(
            BreakerStatus.OK,
            pnl,
            pnl_pct,
            f"PnL {pnl_pct:+.2f}% within limits.",
        )
