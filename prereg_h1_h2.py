#!/usr/bin/env python3
"""Run PREREG-2026-09-22 H1 and H2 -- ONCE -- and grade them against the
criteria written before the data existed. Sim code only; touches no bot code,
config or state.

    python -u prereg_h1_h2.py            (run on your laptop: needs Alpaca + .env)

What it does, exactly as the pre-registration specifies:
  window   2025-07-01 -> 2025-12-31 (no ORB sweep has used it)
  A        config exactly as live (stop 1.5, bias 0.50, trail 1.25 armed at
           breakeven, TP 25% ceiling, 15-min OR, 60-min entry window,
           no shorts, no relvol floor)
  B1 (H1)  A, but targets with bias "any" are not traded
  B2 (H2)  A, but exits.mode range: stop at OR low + 0.10R buffer,
           risk_per_trade_pct 0.5, take_profit_r 100 (trail stays the upside exit)
  B1+B2    run ONLY if both pass, as a confirmatory third check

Each B passes only if ALL FOUR hold, else it is rejected -- no second window,
no threshold tweak, no re-cut:
  1. B expectancy/trade > A expectancy/trade
  2. B expectancy > 0 with t >= 2.0
  3. B stop-out rate < A stop-out rate
  4. B max drawdown <= A max drawdown

The result is written to prereg_h1_h2_result.txt. If that file exists the
script refuses to run: the pre-registration allows one run. Deleting the file
to run again is a decision to break that rule -- write down why first.

Sim fidelity caveat (from the prereg): the sim overstated live trade count
3.3x and P&L by ~+96 over Jun-Aug. A pass means "worth a paper trial", not
"edge".
"""
from __future__ import annotations

import os

os.environ.setdefault("ORB_NO_FILE_LOG", "1")

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import sweep
from orb_bot.config import load_config

START, END = "2025-07-01", "2025-12-31"
RESULT = Path("prereg_h1_h2_result.txt")


def live_combo(cfg, cache, **kw):
    return sweep.run_combo(
        cfg, cache,
        stop_pct=cfg.exits.stop_pct,
        bias_setting=cfg.news.min_sentiment_for_bias,
        trail_setting=cfg.exits.trail_pct,
        hold_setting=None, arm_setting="breakeven", tp_setting="cfg",
        or_minutes=None, relvol_min=0.0, atr_k=None,
        entry_window=None, first_candle=False, **kw,
    )


def grade(a, b):
    checks = [
        ("expectancy B > A", b["expectancy"] > a["expectancy"],
         f"{b['expectancy']:+.2f} vs {a['expectancy']:+.2f}"),
        ("B expectancy > 0, t >= 2.0", b["expectancy"] > 0 and b["t_stat"] >= 2.0,
         f"exp {b['expectancy']:+.2f}, t {b['t_stat']:+.2f}"),
        ("stop rate B < A", b["stop_rate"] < a["stop_rate"],
         f"{b['stop_rate']:.1f}% vs {a['stop_rate']:.1f}%"),
        ("max DD B <= A", b["max_drawdown"] <= a["max_drawdown"],
         f"{b['max_drawdown']:.2f} vs {a['max_drawdown']:.2f}"),
    ]
    return all(ok for _, ok, _ in checks), checks


def fmt(name, r):
    return (f"{name:8} trades {r['trades']:4}  win {r['win_rate']:5}%  net {r['net_pnl']:+9.2f}  "
            f"exp {r['expectancy']:+6.2f}  t {r['t_stat']:+5.2f}  stop-rate {r['stop_rate']:5.1f}%  "
            f"maxDD {r['max_drawdown']:8.2f}  exits {r['exits']}")


def main() -> int:
    if RESULT.exists():
        print(f"{RESULT} already exists -- H1/H2 have been run. The prereg allows ONE run.")
        print(RESULT.read_text())
        return 1
    cfg = load_config()
    cfg.strategy.allow_shorts = False  # live is long-only
    args = SimpleNamespace(start=START, end=END, symbols="", min_prev_volume=500_000,
                           no_news=False, no_cache=False, refresh_cache=False)
    cache = sweep.prefetch(cfg, args)
    if not cache:
        print("No days with candidates -- nothing graded, no result written.")
        return 2

    a = live_combo(cfg, cache)
    b1 = live_combo(cfg, cache, require_news=True)
    b2 = live_combo(cfg, cache, exit_mode="range")
    out = [f"PREREG-2026-09-22 H1/H2 -- run {datetime.now():%Y-%m-%d %H:%M}, "
           f"window {START} -> {END}, {len(cache)} sim days", "",
           fmt("A live", a), fmt("B1 H1", b1), fmt("B2 H2", b2), ""]
    verdicts = {}
    for name, b in (("H1 require news", b1), ("H2 range stop", b2)):
        ok, checks = grade(a, b)
        verdicts[name] = ok
        out.append(f"{name}: {'PASS' if ok else 'REJECTED'}")
        out += [f"   [{'x' if c else ' '}] {label:28} {detail}" for label, c, detail in checks]
    if all(verdicts.values()):
        both = live_combo(cfg, cache, require_news=True, exit_mode="range")
        ok, checks = grade(a, both)
        out += ["", fmt("B1+B2", both), f"Combined (confirmatory): {'PASS' if ok else 'FAIL'}"]
        out += [f"   [{'x' if c else ' '}] {label:28} {detail}" for label, c, detail in checks]
    else:
        out += ["", "Combined run skipped: it runs only if both pass (prereg rule)."]
    out += ["", "A pass earns a PAPER trial with its own pre-registered sample -- not a live change."]
    text = "\n".join(out)
    print("\n" + text)
    RESULT.write_text(text + "\n", encoding="utf-8")
    print(f"\nWritten to {RESULT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
