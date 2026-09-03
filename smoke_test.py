"""One-shot smoke test for the ORB bot. No orders are placed.

Usage (PowerShell):
  python smoke_test.py

Runs: unit tests -> config load -> Alpaca paper connectivity -> screener +
news dry-run -> arm status. Writes results to state/smoke_test_result.txt.
"""

from __future__ import annotations

import io
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "state" / "smoke_test_result.txt"

lines: list[str] = []
failures = 0


def section(title: str) -> None:
    lines.append("")
    lines.append(f"=== {title} ===")


def ok(msg: str) -> None:
    lines.append(f"  PASS  {msg}")


def fail(msg: str) -> None:
    global failures
    failures += 1
    lines.append(f"  FAIL  {msg}")


# 1. Unit tests -------------------------------------------------------------
section("1. Unit tests")
r = subprocess.run(
    [sys.executable, str(ROOT / "tests" / "test_logic.py")],
    capture_output=True, text=True, cwd=ROOT,
)
tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
lines.extend(f"  {t}" for t in tail)
ok("unit tests passed") if r.returncode == 0 else fail("unit tests failed")

# 2. Config + credentials ---------------------------------------------------
section("2. Config + credentials")
try:
    from orb_bot.config import load_config

    cfg = load_config()
    ok(f"config.yaml loaded; paper={cfg.credentials.paper}")
    if not cfg.credentials.paper:
        fail("ALPACA_PAPER is not true — this would hit the LIVE account!")
except BaseException as e:
    fail(f"config load: {e}")
    OUT.write_text("\n".join(lines) + "\nABORTED\n", encoding="utf-8")
    print("\n".join(lines))
    sys.exit(1)

# 3. Alpaca connectivity ----------------------------------------------------
section("3. Alpaca paper connectivity")
broker = None
try:
    from orb_bot.broker import Broker

    broker = Broker(cfg)
    acct = broker.get_account()
    clock = broker.get_clock()
    ok(f"account {acct.account_number} status={acct.status}")
    ok(f"equity=${float(acct.equity):,.2f} buying_power=${float(acct.buying_power):,.2f}")
    ok(f"market open now: {clock.is_open}; next open: {clock.next_open}")
    pos = broker.list_positions()
    ok(f"open positions: {len(pos)}")
except Exception as e:
    fail(f"Alpaca connection: {e}")

# 4. Screener + news dry-run ------------------------------------------------
section("4. Screener + news dry-run (no orders)")
if broker is None:
    fail("skipped — no broker connection")
else:
    try:
        from orb_bot.screener import Screener
        from orb_bot.news import NewsScanner
        from orb_bot.selection import build_targets, fallback_targets

        cands = Screener(cfg, broker).build_candidates()
        lines.append(f"  candidates: {len(cands)}")

        # v1.14: verify the correlated-theme cap can actually fetch history.
        # This is the ONLY place get_daily_returns meets the live Alpaca API
        # before a session starts, and the cap fails SAFE (returns {} =>
        # silently inert), so an unchecked fetch failure would be invisible
        # until someone noticed the bot still stacking correlated names.
        #
        # Probed against the fallback watchlist when the screener is empty —
        # run before the open (or on a holiday) there are no candidates, and
        # an earlier version of this check sat inside `if cands:` and quietly
        # skipped itself while still reporting ALL CHECKS PASSED.
        rets = {}
        if cfg.selection.max_correlation > 0:
            probe = [c.symbol for c in cands] or list(cfg.fallback_symbols)
            probing = "candidates" if cands else "fallback watchlist (screener empty)"
            try:
                rets = broker.get_daily_returns(
                    probe, cfg.selection.correlation_lookback_days
                )
            except Exception as e:  # noqa: BLE001
                fail(f"get_daily_returns raised: {e}")
            usable = sum(1 for s in probe if len(rets.get(s, {})) >= 10)
            lines.append(f"  correlation probe: {probing}")
            lines.append(
                f"  correlation history: {usable}/{len(probe)} symbols "
                f"with >=10 daily returns"
            )
            if not rets:
                fail("correlation cap INERT — no return history fetched at all")
            elif usable < max(2, len(probe) // 4):
                fail(
                    f"correlation cap nearly inert — only {usable} usable "
                    f"of {len(probe)}"
                )
            else:
                ok(f"correlation data OK ({usable}/{len(probe)} usable)")
        else:
            lines.append("  correlation cap disabled (max_correlation 0)")

        if cands:
            news = NewsScanner(cfg).score_symbols([c.symbol for c in cands])
            targets = build_targets(cfg, cands, news, daily_returns=rets)
            src = "screener"
            if not targets:
                targets = fallback_targets(cfg)
                src = "fallback"
        else:
            targets = fallback_targets(cfg)
            src = "fallback"
        ok(f"targets ({src}): {len(targets)}")
        for t in targets:
            lines.append(
                f"    {t.symbol:<6} bias={getattr(t, 'bias', '?'):<7}"
                f" gap={getattr(t, 'gap_pct', 0):+.2f}%"
                f" sent={getattr(t, 'sentiment', 0):+.2f}"
                f" news={getattr(t, 'n_articles', 0)}"
            )
    except Exception:
        fail("screener/news dry-run:")
        lines.extend("  " + l for l in traceback.format_exc().splitlines()[-4:])

# 5. Arm status ---------------------------------------------------------------
section("5. Arm status")
try:
    from orb_bot.state import load_state

    s = load_state()
    lines.append(f"  armed: {s.armed}")
    ok("state file readable")
except Exception as e:
    fail(f"state: {e}")

# Summary ---------------------------------------------------------------------
section("RESULT")
lines.append("  ALL CHECKS PASSED" if failures == 0 else f"  {failures} CHECK(S) FAILED")

report = "\n".join(lines).lstrip("\n")
OUT.parent.mkdir(exist_ok=True)
OUT.write_text(report + "\n", encoding="utf-8")
print(report)
print(f"\nSaved to {OUT}")
sys.exit(0 if failures == 0 else 1)
