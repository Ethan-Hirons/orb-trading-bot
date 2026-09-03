"""Start the ORB trading bot for today.

Usage:
  python arm.py     # first, grant permission for today
  python run.py     # then, start the bot

The bot will refuse to trade unless it has been armed for the current day.
It runs through the trading session and stops when the profit target or max
loss is hit, or shortly before the market close.
"""

from __future__ import annotations

import sys
import traceback

from orb_bot.config import load_config
from orb_bot.logutil import get_logger
from orb_bot.runner import Runner


def main() -> None:
    cfg = load_config()
    Runner(cfg).run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        get_logger().warning("Bot interrupted by user (Ctrl+C).")
        sys.exit(130)
    except Exception:  # noqa: BLE001
        # Make sure a fatal crash always leaves a trace in the day's log
        # (2026-07-15 the bot died twice with nothing in the log at all).
        get_logger().critical("FATAL: bot crashed:\n%s", traceback.format_exc())
        sys.exit(1)
