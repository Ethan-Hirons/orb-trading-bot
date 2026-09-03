"""Daily permission switch.

  python arm.py          -> arm the bot for TODAY (it will trade today)
  python arm.py --off    -> disarm (bot will idle, no new trades)
  python arm.py --status -> show current arm status

This is the "start of every day I give it permission" step. Run it each
morning before (or while) the bot is running. Arming resets the day's
counters and clears any previous halt.
"""

from __future__ import annotations

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from orb_bot.state import arm_for, disarm, load_state

ET = ZoneInfo("America/New_York")


def main() -> None:
    args = set(sys.argv[1:])

    if "--status" in args:
        s = load_state()
        print(f"trade_date : {s.trade_date or '(none)'}")
        print(f"armed      : {s.armed}")
        print(f"start_equity: {s.start_equity}")
        print(f"trades     : {s.trades_opened}")
        print(f"halted     : {s.halted} {('- ' + s.halt_reason) if s.halted else ''}")
        return

    if "--off" in args:
        disarm()
        print("Bot DISARMED. It will not open new trades.")
        return

    today = datetime.now(ET).date()
    arm_for(today)
    print(f"Bot ARMED for {today.isoformat()} (US/Eastern).")
    print("It will trade today within your configured risk limits.")
    print("Start it with:  python run.py")


if __name__ == "__main__":
    main()
