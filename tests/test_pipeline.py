"""Tests for pipeline/ - store and ops checks.

Runs WITHOUT pytest (it is not installed on the bot's interpreter):

    python tests/test_pipeline.py

Builds synthetic repos in a temp dir, so nothing here touches real state.
"""
import gzip
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.health import (OK, WARN, FAIL, entered_vs_booked,  # noqa: E402
                             features_header, frozen_equity, reanchor_rate,
                             session_integrity, stale_xlsx)
from pipeline.store import Store, missing  # noqa: E402

PASS, FAILED = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAILED).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}{('  ' + extra) if extra else ''}")


def mkrepo(tmp, logs=None, history=None, features=None):
    """Minimal repo skeleton."""
    root = Path(tmp) / f"repo{time.time_ns()}"
    (root / "logs").mkdir(parents=True)
    (root / "state").mkdir(parents=True)
    for day, text in (logs or {}).items():
        (root / "logs" / f"orb_{day}.log").write_text(text, encoding="utf-8")
    if history is not None:
        rows = ["date,symbol,side,qty,entry_price,exit_price,pnl,pnl_pct"]
        rows += [",".join(str(x) for x in r) for r in history]
        (root / "state" / "trade_history.csv").write_text(
            "\n".join(rows) + "\n", encoding="utf-8")
    if features is not None:
        (root / "state" / "trade_features.csv").write_text(
            features, encoding="utf-8")
    return root


def enter_line(day, t, sym, ref="10.00"):
    return (f"{day} {t},000 [INFO] ENTER LONG {sym} qty=10 ref={ref} "
            f"TP=12.50(+25.0%) SL=9.85(-1.5%) stop-limit=9.80\n")


def equity_line(day, t, val):
    return f"{day} {t},000 [INFO] Equity {val} | PnL +0.00% within limits.\n"


# --------------------------------------------------------------------------
print("\nstore")
tmp = tempfile.mkdtemp()
s = Store(Path(tmp) / "cache")
n = []
s.get("bars", "AAA", "d1", lambda: (n.append(1), [{"c": 1}])[1])
s.get("bars", "AAA", "d1", lambda: (n.append(1), [{"c": 1}])[1])
check("read-through fetches once", len(n) == 1)
s.get("bars", "BBB", "d1", lambda: [])
check("empty result not cached", not s.has("bars", "BBB", "d1"))
s.get("bars", "CCC", "d1", lambda: 1 / 0)
check("fetch exception swallowed", s.errors == 1)
s.get("bars", "AAA", "d1", lambda: [{"c": 2}], feed="iex")
check("feeds isolated",
      s.read("bars", "AAA", "d1") != s.read("bars", "AAA", "d1", "iex"))
s.path("bars", "AAA", "d1").write_bytes(b"garbage")
check("corrupt entry reads as miss", s.read("bars", "AAA", "d1") is None)
check("missing() lists gaps",
      missing(s, "bars", [("AAA", "d1"), ("ZZZ", "d1")], "iex") == [("ZZZ", "d1")])
check("inventory counts entries", s.inventory()["entries"] >= 2)

# --------------------------------------------------------------------------
print("\nentered_vs_booked")
y = (date.today() - timedelta(days=2)).isoformat()
older = (date.today() - timedelta(days=200)).isoformat()
r = entered_vs_booked(mkrepo(tmp,
    logs={y: enter_line(y, "10:00:00", "AAA") + "Day done.\n"},
    history=[[y, "AAA", "long", 10, 10.0, 10.5, 5.0, 5.0]]))
check("clean book -> ok", r.status == OK, r.summary)

r = entered_vs_booked(mkrepo(tmp,
    logs={y: enter_line(y, "10:00:00", "AAA") + enter_line(y, "10:05:00", "BBB") + "Day done.\n",
          older: "x\n"},
    history=[[y, "AAA", "long", 10, 10.0, 10.5, 5.0, 5.0]]))
check("recent unbooked -> FAIL", r.status == FAIL, r.summary)

r = entered_vs_booked(mkrepo(tmp,
    logs={older: enter_line(older, "10:00:00", "OLD"), y: "Day done.\n"},
    history=[]))
check("only-old unbooked -> WARN", r.status == WARN, r.summary)

# newest session may still be running
r = entered_vs_booked(mkrepo(tmp,
    logs={y: enter_line(y, "10:00:00", "AAA")}, history=[]))
check("newest session exempt", r.status == OK, r.summary)

# --------------------------------------------------------------------------
print("\nfrozen_equity")
day = y
flat = "".join(equity_line(day, f"10:{m:02d}:00", "1000.00") for m in range(12))
r = frozen_equity(mkrepo(tmp, logs={day: flat}))
check("flat equity -> WARN", r.status == WARN, r.summary)
moving = "".join(equity_line(day, f"10:{m:02d}:00", f"10{m:02d}.00") for m in range(12))
r = frozen_equity(mkrepo(tmp, logs={day: moving}))
check("moving equity -> ok", r.status == OK, r.summary)
r = frozen_equity(mkrepo(tmp, logs={day: "no equity lines\n"}))
check("no polls -> ok", r.status == OK, r.summary)

# --------------------------------------------------------------------------
print("\nsession_integrity")
root = mkrepo(tmp, logs={y: equity_line(y, "10:00:00", "1000.00")})
p = root / "logs" / f"orb_{y}.log"
os.utime(p, (time.time(), time.time()))          # mtime long after last line
r = session_integrity(root)
check("killed session -> FAIL", r.status == FAIL, r.summary)

root = mkrepo(tmp, logs={y: equity_line(y, "10:00:00", "1000.00")})
p = root / "logs" / f"orb_{y}.log"
from zoneinfo import ZoneInfo
from datetime import datetime
stamp = datetime(int(y[:4]), int(y[5:7]), int(y[8:10]), 10, 2, 0,
                 tzinfo=ZoneInfo("America/New_York")).timestamp()
os.utime(p, (stamp, stamp))                      # mtime right after last line
r = session_integrity(root)
check("mid-session copy -> WARN not FAIL", r.status == WARN, r.summary)

r = session_integrity(mkrepo(tmp, logs={y: "Day done. Trades: 1\nFlatten verified\n"}))
check("complete session -> ok", r.status == OK, r.summary)

# --------------------------------------------------------------------------
print("\nfeatures_header")
r = features_header(mkrepo(tmp, features="a,b,c\n1,2,3\n4,5,6\n"))
check("matching widths -> ok", r.status == OK, r.summary)
r = features_header(mkrepo(tmp, features="a,b,c\n1,2,3,4\n"))
check("wide rows -> FAIL", r.status == FAIL, r.summary)

# --------------------------------------------------------------------------
print("\nstale_xlsx / reanchor_rate")
root = mkrepo(tmp, history=[[y, "AAA", "long", 10, 10.0, 10.5, 5.0, 5.0]])
r = stale_xlsx(root)
check("no spreadsheet -> ok", r.status == OK, r.summary)
x = root / "trade_log.xlsx"
x.write_bytes(b"stub")
old = time.time() - 30 * 86400
os.utime(x, (old, old))
check("stale spreadsheet -> WARN", stale_xlsx(root).status == WARN)

root = mkrepo(tmp, logs={y: enter_line(y, "10:00:00", "AAA")
                         + "AAA: fill 10.05 (ref 10.00, +0.50% slippage); exits re-anchored TP=1 SL=2.\n"
                         + enter_line(y, "10:30:00", "BBB")})
r = reanchor_rate(root)
check("reanchor rate counted", "1/2" in r.summary, r.summary)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAILED)} failed")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
raise SystemExit(1 if FAILED else 0)
