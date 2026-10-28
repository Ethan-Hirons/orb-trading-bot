"""E16 (QQQ noise-area momentum) read-side: the ledger, the logs, the evening job.

Separate from health.py on purpose. The ORB checks there parse `orb_*.log` and a
`trade_history.csv` of round-trip trades; E16 has neither. Its record is a
per-checkpoint ledger (`state/noise_ledger.csv`, written by noise_bot.py) plus
whatever `deploy/noise-eod.sh` left in `logs/noise_eod_<day>.log`.

Nothing here recomputes Stage A or the forward verdict. Both need the SIP cache,
which lives on the VPS; the evening job already computes them there and writes
them to a log. This module reads that log and says how old it is. Recomputing
locally off a half-synced cache would be a second answer to a question that
already has one, which is how two instances start disagreeing.

Reads a tree shaped like the other instances:

    noise/logs/noise_<day>.log        one per session
    noise/logs/noise_eod_<day>.log    Stage A + forward verdict, from the VPS
    noise/state/noise_ledger.csv      one row per checkpoint, plus a close row
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# 10:00, 10:30, ... 15:30 -- PREREG-QQQ-NOISE-PAPER-2026-10-27, frozen.
# Hard-coded rather than imported from index_scan, which pulls in alpaca; the
# brief must still render on a machine with no broker library installed.
# tests/test_noise_brief.py asserts this equals len(index_scan.CHECKPOINTS).
EXPECTED_CHECKPOINTS = 12

SESSION_LOG_RE = re.compile(r"noise_(\d{4}-\d{2}-\d{2})\.log$")
EOD_LOG_RE = re.compile(r"noise_eod_(\d{4}-\d{2}-\d{2})\.log$")
STATUS_RE = re.compile(r"\b(RUNNING|STOPPED|FINAL)\b")
STAGE_A_RE = re.compile(r"Stage A:|decision agreement|adverse slippage|ledger is empty")


@dataclass
class NoiseSession:
    day: str
    priced: int = 0          # checkpoints with a usable price
    held: int = 0            # checkpoints that held because the price was stale
    traded: int = 0          # checkpoints that sent orders
    flips: int = 0           # long <-> short reversals
    pos_close: str = ""      # position carried into the close
    flat: bool | None = None  # None = no close row yet
    errors: int = 0
    warnings: int = 0
    done: bool = False

    @property
    def covered(self) -> bool:
        return self.priced + self.held == EXPECTED_CHECKPOINTS

    @property
    def full_price_coverage(self) -> bool:
        """Stage A criterion 1 counts checkpoints with a PRICE, not held ones."""
        return self.priced == EXPECTED_CHECKPOINTS


def _ledger_rows(root: Path) -> list[dict]:
    f = root / "state" / "noise_ledger.csv"
    if not f.exists():
        return []
    with f.open(newline="", encoding="utf-8", errors="ignore") as fh:
        return list(csv.DictReader(fh))


def sessions(root: Path, limit: int | None = None) -> list[NoiseSession]:
    """One NoiseSession per day in the ledger, oldest first, newest `limit` kept."""
    by_day: dict[str, NoiseSession] = {}
    for r in _ledger_rows(root):
        day = (r.get("day") or "").strip()
        if not day:
            continue
        s = by_day.setdefault(day, NoiseSession(day=day))
        if (r.get("checkpoint") or "").strip() == "close":
            s.pos_close = (r.get("pos_before") or "").strip()
            s.flat = (r.get("pos_after") or "").strip() == "0"
            continue
        if (r.get("price") or "").strip():
            s.priced += 1
        else:
            s.held += 1
        before, after = (r.get("pos_before") or "").strip(), (r.get("pos_after") or "").strip()
        orders = (r.get("orders") or "").strip()
        if orders.startswith("[["):
            s.traded += 1
        try:
            if int(before) * int(after) < 0:
                s.flips += 1
        except ValueError:
            pass
    for s in by_day.values():
        _read_log(root, s)
    out = sorted(by_day.values(), key=lambda s: s.day)
    return out[-limit:] if limit else out


def _read_log(root: Path, s: NoiseSession) -> None:
    f = root / "logs" / f"noise_{s.day}.log"
    if not f.exists():
        return
    text = f.read_text(encoding="utf-8", errors="ignore")
    s.errors = text.count("[ERROR]")
    s.warnings = text.count("[WARNING]")
    s.done = "session %s done" % s.day in text or f"session {s.day} done" in text


def logged_days(root: Path) -> list[str]:
    """Days with a session log, which is not the same set as days in the ledger.

    A session that died before its first checkpoint leaves a log and no ledger
    rows. Reporting only ledger days would hide exactly that failure."""
    logs = root / "logs"
    if not logs.exists():
        return []
    return sorted(m.group(1) for f in logs.glob("noise_*.log")
                  if (m := SESSION_LOG_RE.search(f.name)))


def latest_eod(root: Path) -> tuple[str, list[str]] | None:
    """(day, the Stage A and verdict lines) from the newest evening-job log."""
    logs = root / "logs"
    if not logs.exists():
        return None
    days = sorted((m.group(1), f) for f in logs.glob("noise_eod_*.log")
                  if (m := EOD_LOG_RE.search(f.name)))
    if not days:
        return None
    day, f = days[-1]
    keep = []
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and (STATUS_RE.search(line) or STAGE_A_RE.search(line)):
            keep.append(line)
    return day, keep


def freshness(root: Path) -> str:
    logs = root / "logs"
    days = logged_days(root)
    if not days:
        return "no logs"
    files = list(logs.glob("noise_*.log"))
    age_h = (datetime.now().timestamp() - max(f.stat().st_mtime for f in files)) / 3600
    return (f"latest session {days[-1]}, {len(days)} session "
            f"log{'' if len(days) == 1 else 's'}, last synced {age_h:.0f}h ago")


def warnings(root: Path, ss: list[NoiseSession]) -> list[str]:
    """Things worth saying out loud. Deliberately few: a brief nobody reads is worse."""
    out = []
    if (root / "state" / "NOISE_DISARMED").exists():
        out.append("`NOISE_DISARMED` is present - the runner is paused and will not trade")
    ledger_days = {s.day for s in ss}
    orphans = [d for d in logged_days(root) if d not in ledger_days]
    if orphans:
        out.append(f"{len(orphans)} session log(s) with no ledger rows at all "
                   f"({', '.join(orphans[-3:])}) - the runner started and wrote no decision")
    for s in ss:
        if not s.covered:
            out.append(f"{s.day}: {s.priced + s.held}/{EXPECTED_CHECKPOINTS} checkpoints "
                       f"logged - Stage A criterion 1 needs all 12")
        elif s.held:
            out.append(f"{s.day}: {s.held} checkpoint(s) held on a stale price")
        if s.flat is None:
            out.append(f"{s.day}: no close row - the session did not reach 16:05")
        elif not s.flat:
            out.append(f"{s.day}: NOT FLAT at the close - check the account")
        if s.errors:
            out.append(f"{s.day}: {s.errors} ERROR line(s) in the session log")
    eod = latest_eod(root)
    if eod is None:
        out.append("no `noise_eod_*.log` - the 16:40 evening job has not been synced or "
                   "has not run, so Stage A and the forward verdict are unknown here")
    elif ss and eod[0] < ss[-1].day:
        out.append(f"evening job last ran {eod[0]}, the runner reached {ss[-1].day} - "
                   f"the Stage A and verdict lines below are stale")
    return out
