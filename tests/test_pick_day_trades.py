"""v1.13 regression: daily_profit_target_pct = 0 must DISABLE the sim's profit
breaker, not fire it instantly.

RiskManager.assess has guarded `target > 0` since v1.12; backtest.pick_day_trades
did not. When config set daily_profit_target_pct: 0 on 2026-08-13 to turn the
profit breaker off, the sim's check became `realized >= 0.0` — true on the first
trade of every day — and every sweep silently returned ZERO trades for every
combo. The live bot was unaffected; only the simulator was.

Run from the project root:
  python tests/test_pick_day_trades.py
"""
import os
import sys

os.environ.setdefault("ORB_NO_FILE_LOG", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import SimTrade, pick_day_trades
from orb_bot.config import load_config


def trade(entry, exit_, pnl, sym="T"):
    return SimTrade(
        date="2026-07-20", symbol=sym, side="long", qty=1,
        entry_price=100.0, exit_price=100.0 + pnl, exit_reason="eod",
        entry_time=entry, exit_time=exit_, pnl=pnl, pnl_pct=pnl / 100.0,
        bias="any", or_pct=1.0, gap_pct=0.0, sentiment=0.0,
    )


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    return ok


def main():
    cfg = load_config()
    cfg.strategy.max_trades_per_day = 10
    cfg.risk.daily_max_loss_pct = 5.0
    eq = 2050.0
    results = []

    winners = [trade("09:40", "10:00", +10.0, "A"),
               trade("10:10", "10:30", +10.0, "B"),
               trade("10:40", "11:00", +10.0, "C")]

    # The bug: target 0 dropped every trade.
    cfg.risk.daily_profit_target_pct = 0
    results.append(check("target 0 -> breaker disabled, all trades kept",
                         len(pick_day_trades(winners, cfg, eq)), 3))

    cfg.risk.daily_profit_target_pct = -1
    results.append(check("negative target -> also disabled",
                         len(pick_day_trades(winners, cfg, eq)), 3))

    # A configured target must still stop the day.
    cfg.risk.daily_profit_target_pct = 0.5   # 0.5% of 2050 = 10.25
    results.append(check("target 0.5% -> stops after realized clears it",
                         len(pick_day_trades(winners, cfg, eq)), 2))

    cfg.risk.daily_profit_target_pct = 100.0
    results.append(check("unreachable target -> all trades kept",
                         len(pick_day_trades(winners, cfg, eq)), 3))

    # The loss breaker is independent of the profit target.
    cfg.risk.daily_profit_target_pct = 0
    cfg.risk.daily_max_loss_pct = 1.0        # -1% of 2050 = -20.5
    losers = [trade("09:40", "10:00", -25.0, "A"),
              trade("10:10", "10:30", -25.0, "B"),
              trade("10:40", "11:00", -25.0, "C")]
    results.append(check("loss breaker still fires with target disabled",
                         len(pick_day_trades(losers, cfg, eq)), 1))

    # max_trades_per_day still caps.
    cfg.risk.daily_max_loss_pct = 5.0
    cfg.strategy.max_trades_per_day = 2
    results.append(check("max_trades_per_day still caps",
                         len(pick_day_trades(winners, cfg, eq)), 2))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
        return 0
    print(f"{results.count(False)}/{len(results)} checks FAILED.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
