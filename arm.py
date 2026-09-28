"""Daily permission switch.

  python arm.py             -> arm the bot for TODAY
  python arm.py --next      -> arm for the NEXT trading day (use the evening before)
  python arm.py --off       -> disarm (bot will idle, no new trades)
  python arm.py --status    -> show current arm status

Paper arms itself every weekday (deploy/start.sh). LIVE never does: a human
runs this, or there is no live session. `--next` exists because the live host
checks the arm at 09:25 ET and a market open is an awkward time to be at a
terminal -- arming the evening before is the same decision, made earlier.

Arming resets the day's counters and clears any previous halt.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from orb_bot.state import arm_for, disarm, load_state

ET = ZoneInfo("America/New_York")

# Kept in step with invariants.MARKET_HOLIDAYS. A holiday armed by mistake is
# harmless -- the bot finds a closed market and ends the session -- but it
# would waste the arm, so --next skips them.
MARKET_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in MARKET_HOLIDAYS


def next_trading_day(after: date) -> date:
    d = after + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def main() -> None:
    args = set(sys.argv[1:])
    now_et = datetime.now(ET)

    if "--status" in args:
        s = load_state()
        today = now_et.date().isoformat()
        print(f"trade_date : {s.trade_date or '(none)'}")
        print(f"armed      : {s.armed}")
        print(f"start_equity: {s.start_equity}")
        print(f"trades     : {s.trades_opened}")
        print(f"halted     : {s.halted} {('- ' + s.halt_reason) if s.halted else ''}")
        if s.armed and s.trade_date and s.trade_date != today:
            when = "a past day" if s.trade_date < today else "a future day"
            print(f"note       : armed for {when}, not today ({today}).")
        return

    if "--off" in args:
        disarm()
        print("Bot DISARMED. It will not open new trades.")
        return

    if "--next" in args:
        day = next_trading_day(now_et.date())
    else:
        day = now_et.date()
        if not is_trading_day(day):
            nxt = next_trading_day(day)
            print(f"{day.isoformat()} is not a trading day.")
            print(f"Arming today would do nothing. Did you mean:")
            print(f"    python arm.py --next     (arms {nxt.isoformat()})")
            sys.exit(1)

    arm_for(day)
    label = "TODAY" if day == now_et.date() else day.strftime("%A")
    print(f"Bot ARMED for {day.isoformat()} ({label}, US/Eastern).")
    print("It will trade that session within your configured risk limits.")
    if day != now_et.date():
        print("Nothing happens until that morning's 09:25 ET start.")


if __name__ == "__main__":
    main()
