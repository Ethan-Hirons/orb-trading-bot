"""One-shot reconcile: backfill trade_history.csv from recent Alpaca fills.

Usage:  python backfill.py [days]

Read-only against Alpaca (only fetches closed orders); appends any completed
round trips that are missing from state/trade_history.csv, then prints the
running trial stats. The bot also does this automatically at every startup.
"""

from __future__ import annotations

import sys

from orb_bot.broker import Broker
from orb_bot.config import load_config
from orb_bot import report


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    broker = Broker(load_config())
    added = report.backfill_history(broker, days=days)
    print(f"Backfill over the last {days} day(s): {added} missing trade(s) added.")
    s = report.running_stats()
    if s.n_trades:
        print(
            f"Trial: {s.n_trades} trades | win rate {s.win_rate:.1f}% | "
            f"expectancy {s.expectancy:+.2f}/trade | total PnL {s.total_pnl:+.2f}"
        )


if __name__ == "__main__":
    main()
