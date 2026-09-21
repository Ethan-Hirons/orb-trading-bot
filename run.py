"""Start the ORB trading bot for today.

Usage:
  python arm.py     # first, grant permission for today
  python run.py     # then, start the bot

The bot will refuse to trade unless it has been armed for the current day.
It runs through the trading session and stops when the profit target or max
loss is hit, or shortly before the market close.

v1.17: SIGTERM and SIGINT are handled gracefully. `systemctl stop`, a Ctrl+C,
or a systemd shutdown now asks the runner to stop, and the runner flattens
every open position before exiting. Previously either signal killed the
process outright and left positions open with only the broker-side bracket
stop protecting them overnight. Send the signal twice to exit immediately
without flattening (only if the flatten is itself stuck).
"""

from __future__ import annotations

import signal
import sys
import traceback

from orb_bot.config import load_config
from orb_bot.logutil import get_logger
from orb_bot.runner import Runner


def _install_signal_handlers(runner: Runner) -> None:
    def _handle(signum, _frame):
        log = get_logger()
        try:
            name = signal.Signals(signum).name
        except ValueError:  # pragma: no cover - platform dependent
            name = str(signum)
        if runner.stop_requested:
            # Second signal: the operator is telling us the graceful path is
            # stuck. Leave loudly -- positions may still be open.
            log.error(
                "%s received again; exiting NOW without flattening. "
                "CHECK THE ALPACA DASHBOARD for open positions.", name
            )
            sys.exit(130)
        log.warning(
            "%s received; finishing the current iteration, then flattening "
            "all positions before exit.", name
        )
        runner.request_stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError, AttributeError):  # pragma: no cover
            # Not the main thread, or the platform lacks the signal.
            pass


def main() -> None:
    cfg = load_config()
    runner = Runner(cfg)
    _install_signal_handlers(runner)
    runner.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Only reachable if the signal arrived before the handler was
        # installed (i.e. during config load).
        get_logger().warning("Bot interrupted during startup (Ctrl+C).")
        sys.exit(130)
    except Exception:  # noqa: BLE001
        # Make sure a fatal crash always leaves a trace in the day's log
        # (2026-07-15 the bot died twice with nothing in the log at all).
        get_logger().critical("FATAL: bot crashed:\n%s", traceback.format_exc())
        sys.exit(1)
