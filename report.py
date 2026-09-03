"""Print the running paper-trial stats from state/trade_history.csv.

Usage:  python report.py
"""

from __future__ import annotations

from orb_bot.report import _load_history, running_stats


def main() -> None:
    rows = _load_history()
    if not rows:
        print("No trade history yet (state/trade_history.csv is empty).")
        return

    print(f"{'date':<12}{'symbol':<8}{'side':<7}{'qty':>6}{'entry':>10}"
          f"{'exit':>10}{'pnl':>10}{'pnl%':>8}")
    for r in rows:
        print(f"{r['date']:<12}{r['symbol']:<8}{r['side']:<7}"
              f"{float(r['qty']):>6g}{float(r['entry_price']):>10.2f}"
              f"{float(r['exit_price']):>10.2f}{float(r['pnl']):>+10.2f}"
              f"{float(r['pnl_pct']):>+8.2f}")

    s = running_stats()
    print("-" * 71)
    print(f"Trades: {s.n_trades} | Win rate: {s.win_rate:.1f}% "
          f"({s.n_wins}/{s.n_trades})")
    print(f"Expectancy: {s.expectancy:+.2f}/trade ({s.expectancy_pct:+.3f}%)")
    print(f"Avg win: {s.avg_win:+.2f} | Avg loss: {s.avg_loss:+.2f}")
    print(f"Total PnL: {s.total_pnl:+.2f} | Max drawdown: {s.max_drawdown:.2f}")


if __name__ == "__main__":
    main()
