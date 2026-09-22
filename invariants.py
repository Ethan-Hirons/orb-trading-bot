"""Session health checks — assert what should have been IMPOSSIBLE.

Motivation (2026-08-13). The v1.8/v1.9 trailing stop never fired in live
trading. It was broken for four trading days (Aug 3-6, ~-130) before anyone
noticed, because every report answered "what happened today?" and none asked
"did anything happen that CANNOT happen if the bot is working?". The daily
recap ran through all four days and reported the losses as ordinary.

This module is the missing half. It is read-only: it parses the day's log and
state/trade_history.csv and emits PASS / WARN / FAIL per invariant. Nothing
here touches the broker, the config, or any bot state.

Usage:
    python invariants.py                # today
    python invariants.py 2026-08-04     # a specific session
    python invariants.py --all          # every log on disk (regression sweep)
    python invariants.py --all --quiet  # only sessions with WARN/FAIL

Exit code: 0 if no FAILs, 1 if any FAIL. Safe to run any time.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent
LOGS = REPO / "logs"
HISTORY = REPO / "state" / "trade_history.csv"

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

# US equity market holidays. Without these every holiday reads as a lost
# session and the "no log today" check cries wolf until it gets ignored.
MARKET_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in MARKET_HOLIDAYS


def missing_sessions(back_to: date, through: date, have: set[str]) -> list[str]:
    """Trading days in [back_to, through] with no log on disk."""
    out, cur = [], back_to
    while cur <= through:
        if is_trading_day(cur) and cur.isoformat() not in have:
            out.append(cur.isoformat())
        cur = date.fromordinal(cur.toordinal() + 1)
    return out

# Tolerance on "the stop held": fills are never exact, and a stop-limit can
# slip a little. Beyond this multiple of the intended stop it is a real breach.
#
# v1.13 (2026-08-14): this was a flat 1.15 and it was WRONG — it ignored the
# bot's own configured stop-limit band. With stop_pct 1.5 and
# stop_limit_offset_pct 0.5, a fill anywhere down to -2.0% is the design
# working as intended (the stop triggers at -1.5% and the limit permits 0.5%
# more), i.e. up to 1.33x — well past the 1.15 threshold. ONDS on 2026-08-13
# filled at -1.74% and was reported as a FAIL; it was a normal stop-limit fill
# inside the configured band. The budget is now derived from config, so the
# check tests the bot against its own contract instead of a magic number.
# SLIPPAGE_MARGIN covers the fact that a stop-LIMIT can still miss and fall
# back to a worse fill on a fast tape.
SLIPPAGE_MARGIN = 1.10


def stop_budget_pct(stop_pct: float, stop_band_pct: float = 0.0) -> float:
    """Worst loss % the exit design permits before it counts as a breach.

    stop_pct is the width recorded on that trade's entry line; stop_band_pct is
    the configured stop-limit offset (0 when stop_limit is off).
    """
    return (float(stop_pct) + float(stop_band_pct)) * SLIPPAGE_MARGIN
# How far past breakeven a peak must run before the trail is expected to arm.
# runner._check_trailing arms once a trail_pct trail would lock >= breakeven,
# i.e. roughly when peak >= trail_pct. Add a margin so we only flag clear cases.
TRAIL_ARM_MARGIN = 1.25


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

RE_ENTER = re.compile(
    r"ENTER (?P<side>LONG|SHORT) (?P<sym>[A-Z.]+) qty=(?P<qty>\d+).*?"
    r"SL=(?P<sl>[\d.]+)\((?P<slpct>[-+][\d.]+)%\)"
)
RE_EXCURSION = re.compile(
    r"^\s+(?P<sym>[A-Z.]+)\s+MFE (?P<mfe>[-+][\d.]+) \((?P<mfepct>[-+][\d.]+)%\)"
    r"\s*\|\s*MAE (?P<mae>[-+][\d.]+) \((?P<maepct>[-+][\d.]+)%\)"
)
RE_EOD_POS = re.compile(
    r"^\s+(?P<sym>[A-Z.]+) (?:LONG|SHORT) qty=\d+\s+[\d.]+ -> [\d.]+\s+"
    r"PnL (?P<pnl>[-+][\d.]+) \((?P<pnlpct>[-+][\d.]+)%\)"
)
RE_DAYDONE = re.compile(r"Day done\. Trades: (?P<n>\d+) \| End equity: (?P<eq>[\d.]+)")
# The 30s unrealized poll. The series bounds the whole book's drawdown, which
# is what makes a single position's claimed MAE falsifiable (v1.18).
RE_EQUITY = re.compile(r"Equity (?P<eq>[\d.]+) \| PnL")
# Flatten retries are the routine working, not a carry. "BITO still open
# (qty 61); re-closing." is logged BY the flatten loop; treating it as an
# overnight carry made 2026-09-21 a FAIL on a session that flattened cleanly
# 11 seconds later. A checker that cries wolf gets ignored, which is the exact
# failure this module exists to prevent.
RE_FLATTEN_RETRY = re.compile(r"Flatten: .* still open \(qty \d+\); re-closing")
RE_ILLIQUID = re.compile(r"ILLIQUID skipped \d+ candidate\(s\)[^:]*: (?P<syms>.+)$")
RE_TRAIL_ACT = re.compile(r"TRAIL ACTIVATED (?P<sym>[A-Z.]+)")
RE_TRAIL_EXIT = re.compile(r"TRAIL EXIT (?P<sym>[A-Z.]+)")

# "2026-08-04 21:50:27,722 [INFO]     CWVX   MFE ..." -> "    CWVX   MFE ..."
# The indent matters (it is what distinguishes a summary row from a log line),
# so strip only the timestamp+level and keep everything after the single space.
RE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ \[[A-Z]+\] ")

# The EOD flatten verification line landed in v1.5; logs before this date
# legitimately do not have it and must not be scored against it.
FLATTEN_VERIFY_SINCE = "2026-07-20"
# The trailing stop went live with v1.9 (config.yaml: "v1.9: ON at 1.25 ...
# 2026-08-03"). Before that trail_pct was 0, so a peak running past the
# arming threshold was not a failure — do not score those sessions.
TRAIL_LIVE_SINCE = "2026-08-03"


def strip_prefix(line: str) -> str:
    return RE_PREFIX.sub("", line)


@dataclass
class Session:
    day: str
    path: Path
    lines: list[str] = field(default_factory=list)
    entries: dict[str, dict] = field(default_factory=dict)
    excursions: dict[str, dict] = field(default_factory=dict)
    eod: dict[str, dict] = field(default_factory=dict)
    illiquid: set[str] = field(default_factory=set)
    trail_activated: set[str] = field(default_factory=set)
    trail_exited: set[str] = field(default_factory=set)
    equity: list[float] = field(default_factory=list)
    day_done: bool = False
    day_done_trades: int | None = None
    flatten_verified: bool = False
    breaker: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_session(path: Path) -> Session:
    day = path.stem.replace("orb_", "")
    s = Session(day=day, path=path)
    s.lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    for ln in s.lines:
        body = strip_prefix(ln)
        if m := RE_ENTER.search(ln):
            s.entries[m["sym"]] = {
                "side": m["side"], "qty": int(m["qty"]),
                "stop_pct": abs(float(m["slpct"])),
            }
        if m := RE_EXCURSION.match(body):
            s.excursions[m["sym"]] = {
                "mfe": float(m["mfe"]), "mfe_pct": float(m["mfepct"]),
                "mae": float(m["mae"]), "mae_pct": float(m["maepct"]),
            }
        if m := RE_EOD_POS.match(body):
            s.eod[m["sym"]] = {"pnl": float(m["pnl"]), "pnl_pct": float(m["pnlpct"])}
        if m := RE_ILLIQUID.search(ln):
            s.illiquid |= {x.strip() for x in m["syms"].split(",") if x.strip()}
        if m := RE_TRAIL_ACT.search(ln):
            s.trail_activated.add(m["sym"])
        if m := RE_TRAIL_EXIT.search(ln):
            s.trail_exited.add(m["sym"])
        if m := RE_EQUITY.search(ln):
            s.equity.append(float(m["eq"]))
        if m := RE_DAYDONE.search(ln):
            s.day_done = True
            s.day_done_trades = int(m["n"])
        if "Flatten verified" in ln:
            s.flatten_verified = True
        if "BREAKER:" in ln:
            s.breaker = ln.split("BREAKER:", 1)[1].strip()
        if "[ERROR]" in ln:
            s.errors.append(ln.strip())
        elif "[WARNING]" in ln:
            s.warnings.append(ln.strip())
    return s


def book_low_delta(s: Session) -> float | None:
    """Worst the WHOLE book's unrealized P&L got, in dollars (v1.18).

    The 30s poll logs account equity, which is start-of-day equity plus the
    unrealized P&L of everything open (nothing is realized intraday — the bot
    exits at the close). The first poll fires at the open, before any entry, so
    min(series) - first is the deepest the book ever went underwater.

    Reported as context, never as a pass/fail trigger: a single position's MAE
    can legitimately exceed this if another position was up at the same moment.
    """
    if len(s.equity) < 2:
        return None
    return min(s.equity) - s.equity[0]


def history_rows(day: str) -> list[dict]:
    if not HISTORY.exists():
        return []
    return [r for r in csv.DictReader(HISTORY.open(newline="")) if r["date"] == day]


# --------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------

@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


def check_session(s: Session, trail_pct: float,
                  stop_band_pct: float = 0.0) -> list[Check]:
    out: list[Check] = []
    rows = history_rows(s.day)

    # 1. The bot ran to completion.
    if s.day_done:
        out.append(Check("session completed", PASS,
                         f"{s.day_done_trades} trade(s)"))
    else:
        out.append(Check("session completed", FAIL,
                         "no 'Day done' line — bot crashed or never finished"))

    # 2. The EOD flatten verified it left nothing open (v1.5+ logs only).
    if s.day < FLATTEN_VERIFY_SINCE:
        out.append(Check("flatten verified", PASS, "pre-v1.5 log, n/a"))
    elif s.entries and not s.flatten_verified:
        out.append(Check("flatten verified", FAIL,
                         "positions were opened but no 'Flatten verified' line"))
    elif s.flatten_verified:
        out.append(Check("flatten verified", PASS))
    else:
        out.append(Check("flatten verified", PASS, "no positions opened"))

    # 3. No position survived to the next session.
    carried = [ln for ln in s.lines
               if not RE_FLATTEN_RETRY.search(ln)
               and ("STILL OPEN" in ln.upper() or "still held" in ln
                    or "overnight" in ln.lower() and "[ERROR]" in ln)]
    out.append(Check("no overnight carry", FAIL if carried else PASS,
                     carried[0][:130] if carried else ""))

    # 4. THE ONE THAT WAS MISSED: a winner that ran past the trail's arming
    #    threshold must have handed off to a trailing stop. CWVX peaked +3.88%
    #    on 2026-08-04, never armed, and exited at the full initial stop.
    if trail_pct > 0 and s.day >= TRAIL_LIVE_SINCE:
        should_have: list[str] = []
        for sym, exc in s.excursions.items():
            peak = exc["mfe_pct"]
            if peak >= trail_pct * TRAIL_ARM_MARGIN and sym not in s.trail_activated:
                should_have.append(f"{sym} peaked {peak:+.2f}% (trail {trail_pct}%)")
        if should_have:
            out.append(Check("trail armed when it should", FAIL,
                             "; ".join(should_have) + " — no TRAIL ACTIVATED"))
        elif s.trail_activated:
            out.append(Check("trail armed when it should", PASS,
                             f"armed: {', '.join(sorted(s.trail_activated))}"))
        else:
            peaks = [e["mfe_pct"] for e in s.excursions.values()]
            best = f"best peak {max(peaks):+.2f}%" if peaks else "no positions"
            out.append(Check("trail armed when it should", PASS,
                             f"nothing qualified ({best})"))

        # 4b. A winner that armed the trail should not then exit at the
        #     initial stop — that means the handoff silently reverted.
        bad_handoff = [
            sym for sym in s.trail_activated
            if sym in s.eod and sym in s.entries
            and s.eod[sym]["pnl_pct"] <= -s.entries[sym]["stop_pct"] * 0.95
        ]
        if bad_handoff:
            out.append(Check("trail handoff held", FAIL,
                             f"{', '.join(bad_handoff)} armed but exited at the "
                             "initial stop"))

    # 5. Realized losses must respect the intended stop, allowing for the
    #    configured stop-limit band (see stop_budget_pct).
    breaches = []
    for sym, e in s.entries.items():
        pos = s.eod.get(sym)
        if not pos:
            continue
        budget = stop_budget_pct(e["stop_pct"], stop_band_pct)
        if pos["pnl_pct"] < -budget:
            breaches.append(
                f"{sym} {pos['pnl_pct']:+.2f}% vs stop -{e['stop_pct']:.2f}% "
                f"(budget -{budget:.2f}%)")
    out.append(Check("stops held", FAIL if breaches else PASS,
                     "; ".join(breaches)))

    # 5b. (v1.18) A position's adverse excursion cannot run past its own
    #     bracket stop unless that stop actually fired. Exactly two things
    #     produce this line, and both need a human:
    #       (a) the excursion number is fiction — a pre-fill bar scan scoring
    #           the opening range as the position's own MAE. This is what the
    #           v1.17 clamp fixed, so seeing it again means the clamp is not
    #           running on the host that traded. 2026-09-18 SNXX -3.86% and
    #           MARA -2.63%; 2026-09-21 WBD -2.57% and INTC -1.68%, all
    #           against a -1.50% stop that was never touched.
    #       (b) the excursion is real and the stop-limit blew through without
    #           filling — VALIDATION-GO-NOGO.md hard fail #3.
    #     (a) corrupts the one statistic anybody would cite to argue for a
    #     wider stop. (b) is a live-money risk-containment failure. Neither is
    #     ordinary, and before this check nothing in the suite caught either.
    impossible = []
    for sym, exc in s.excursions.items():
        ent = s.entries.get(sym)
        if not ent or ent["stop_pct"] <= 0:
            continue
        budget = stop_budget_pct(ent["stop_pct"], stop_band_pct)
        realized = s.eod.get(sym, {}).get("pnl_pct")
        took_the_stop = (realized is not None
                         and realized <= -ent["stop_pct"] * 0.95)
        if exc["mae_pct"] < -budget and not took_the_stop:
            exit_txt = f"{realized:+.2f}%" if realized is not None else "n/a"
            impossible.append(
                f"{sym} MAE {exc['mae_pct']:+.2f}% past a -{ent['stop_pct']:.2f}% "
                f"stop that never fired (exit {exit_txt})")
    if impossible:
        detail = "; ".join(impossible)
        low = book_low_delta(s)
        if low is not None:
            detail += (f" — but the whole book's worst unrealized point all "
                       f"session was only {low:+.2f}")
        out.append(Check("excursion within stop", FAIL, detail))
    elif s.excursions:
        out.append(Check("excursion within stop", PASS))

    # 6. Excursion tracking must bracket the realized result. The final price
    #    is by definition one of the observed prices, so MAE can never be
    #    milder than the realized loss. If it is, the 30s excursion poll is
    #    missing moves — and the SAME tracker is what arms the trail, so an
    #    understated MFE means the trail can fail to arm on a real winner.
    stale = []
    for sym, exc in s.excursions.items():
        pos = s.eod.get(sym)
        if not pos:
            continue
        if pos["pnl_pct"] < 0 and exc["mae_pct"] > pos["pnl_pct"] + 0.05:
            stale.append(f"{sym} MAE {exc['mae_pct']:+.2f}% but closed "
                         f"{pos['pnl_pct']:+.2f}%")
        if pos["pnl_pct"] > 0 and exc["mfe_pct"] < pos["pnl_pct"] - 0.05:
            stale.append(f"{sym} MFE {exc['mfe_pct']:+.2f}% but closed "
                         f"{pos['pnl_pct']:+.2f}%")
    if stale:
        out.append(Check(
            "excursion tracking sane", WARN,
            "; ".join(stale) + "  <- the 30s poll missed the move; the SAME "
            "tracker arms the trail, so peaks can be understated"))
    else:
        out.append(Check("excursion tracking sane", PASS))

    # 7. Every entry produced exactly one history row.
    hist_syms = [r["symbol"] for r in rows]
    missing = sorted(set(s.entries) - set(hist_syms))
    extra = sorted(set(hist_syms) - set(s.entries))
    if missing or extra:
        bits = []
        if missing:
            bits.append(f"entered but not booked: {', '.join(missing)}")
        if extra:
            bits.append(f"booked but no ENTER line: {', '.join(extra)}")
        out.append(Check("trades reconcile", FAIL, "; ".join(bits)))
    else:
        out.append(Check("trades reconcile", PASS, f"{len(rows)} row(s)"))

    if s.day_done_trades is not None and s.day_done_trades != len(rows):
        out.append(Check("trade count matches history", FAIL,
                         f"log says {s.day_done_trades}, history has {len(rows)}"))

    # 8. A name rejected as illiquid must never be traded.
    leaked = sorted(s.illiquid & set(s.entries))
    out.append(Check("no illiquid entries", FAIL if leaked else PASS,
                     ", ".join(leaked)))

    # 9. Silent order-management failure was the root cause of the trail bug.
    silent = [ln for ln in s.lines
              if ("move_stop" in ln or "replace" in ln.lower())
              and ("[ERROR]" in ln or "[WARNING]" in ln)]
    if silent:
        out.append(Check("order management clean", WARN,
                         f"{len(silent)} order-replace failure(s) logged"))
    else:
        out.append(Check("order management clean", PASS))

    # 10. Errors are never routine.
    if s.errors:
        out.append(Check("no errors", FAIL, f"{len(s.errors)}: {s.errors[0][:110]}"))
    else:
        out.append(Check("no errors", PASS,
                         f"{len(s.warnings)} warning(s)" if s.warnings else ""))

    return out


def _exits_config() -> dict:
    """Read the exits: block from config.yaml without importing the bot (which
    wants credentials). Empty dict when unreadable."""
    cfg = REPO / "config.yaml"
    if not cfg.exists():
        return {}
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        return data.get("exits") or {}
    except Exception:  # noqa: BLE001
        return {}


def load_trail_pct() -> float:
    """trail_pct from config.yaml; 0 = skip the trail checks."""
    exits = _exits_config()
    if exits:
        return float(exits.get("trail_pct", 0) or 0)
    cfg = REPO / "config.yaml"
    if not cfg.exists():
        return 0.0
    m = re.search(r"^\s*trail_pct:\s*([\d.]+)", cfg.read_text(encoding="utf-8"),
                  re.M)
    return float(m.group(1)) if m else 0.0


def load_stop_band_pct() -> float:
    """Configured stop-limit offset %, or 0 when stop_limit is off.

    This is the slack the exit design itself permits below the stop trigger; a
    fill inside it is the stop-limit working, not a breach.
    """
    exits = _exits_config()
    if not exits.get("stop_limit", False):
        return 0.0
    return float(exits.get("stop_limit_offset_pct", 0.0) or 0.0)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def report(s: Session, checks: list[Check], quiet: bool = False) -> str:
    worst = FAIL if any(c.status == FAIL for c in checks) else (
        WARN if any(c.status == WARN for c in checks) else PASS)
    if quiet and worst == PASS:
        return ""
    icon = {PASS: "ok  ", WARN: "WARN", FAIL: "FAIL"}
    head = f"{s.day}  [{worst}]"
    if s.breaker:
        head += f"  breaker: {s.breaker[:60]}"
    out = [head]
    for c in checks:
        if quiet and c.status == PASS:
            continue
        line = f"  {icon[c.status]}  {c.name}"
        if c.detail:
            line += f" — {c.detail}"
        out.append(line)
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("day", nargs="?", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--all", action="store_true", help="check every log on disk")
    ap.add_argument("--quiet", action="store_true",
                    help="only show sessions/checks that are not PASS")
    args = ap.parse_args()

    trail = load_trail_pct()
    stop_band = load_stop_band_pct()

    no_session_fail = False
    if args.all:
        paths = sorted(LOGS.glob("orb_*.log"))
    else:
        day = args.day or date.today().isoformat()
        p = LOGS / f"orb_{day}.log"
        if p.exists():
            paths = [p]
        else:
            d = datetime.strptime(day, "%Y-%m-%d").date()
            paths = []
            if is_trading_day(d):
                # v1.15: a trading day with no log is a FAILURE, not a shrug.
                # This used to `return 0` with a soft "holiday, or the bot never
                # started" and the daily recap accepted it: Aug 7/10, then six
                # more sessions Aug 28-Sep 4 (2026), all lost to the bot simply
                # not being armed, none flagged. A day that produced nothing is
                # the cheapest possible way to fail validation and the easiest
                # to miss.
                print(f"{day}  [FAIL]")
                print("  FAIL  no session — trading day with no log at all. The "
                      "bot never started, or was never armed (`python arm.py`).")
                no_session_fail = True
            else:
                # v1.16 (2026-09-12): do NOT return here. This used to
                # `return 0` immediately, which meant the trailing-14-day gap
                # report below never ran on a weekend or a holiday. On Sat
                # 2026-09-12 the bare command printed "Nothing to check" and
                # exited 0 while Fri 09-11 sat there as an unflagged missed
                # session — the exact blindness v1.15 removed from weekdays,
                # surviving on the one day of the week you actually sit down to
                # review. A non-trading day means no SESSION to check; it does
                # not mean no GAPS to report.
                print(f"No log for {day} — not a trading day.")

    any_fail = no_session_fail
    blocks = []
    for p in paths:
        try:
            datetime.strptime(p.stem.replace("orb_", ""), "%Y-%m-%d")
        except ValueError:
            continue  # wrapper.log etc.
        s = parse_session(p)
        checks = check_session(s, trail, stop_band)
        any_fail |= any(c.status == FAIL for c in checks)
        block = report(s, checks, quiet=args.quiet)
        if block:
            blocks.append(block)

    # Only claim cleanliness when sessions were actually examined — on a
    # non-trading day `paths` is empty and "All sessions clean." would be a
    # false all-clear (v1.16).
    if paths:
        print("\n".join(blocks) if blocks else "All sessions clean.")

    # Missing sessions: two of the Aug 3-7 validation week were lost this way
    # (no log on Aug 7 and Aug 10) and nothing flagged it; then six more went
    # the same way Aug 28-Sep 4. v1.15: this now runs on EVERY invocation, not
    # only under --all, because the daily recap calls the bare command — the
    # one code path where the gap was invisible.
    on_disk = {p.stem.replace("orb_", "") for p in LOGS.glob("orb_*.log")}
    on_disk = {d for d in on_disk if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)}
    if on_disk:
        first = datetime.strptime(min(on_disk), "%Y-%m-%d").date()
        if args.all:
            window_start, label = first, "Trial to date"
        else:
            # Trailing two weeks is enough to catch an outage while it is still
            # actionable, without re-reporting ancient history every evening.
            end = datetime.strptime(args.day or date.today().isoformat(),
                                    "%Y-%m-%d").date()
            window_start = max(first, date.fromordinal(end.toordinal() - 14))
            label = "Last 14 days"
        window_end = datetime.strptime(max(on_disk), "%Y-%m-%d").date()
        if not args.all:
            window_end = max(
                window_end,
                datetime.strptime(args.day or date.today().isoformat(),
                                  "%Y-%m-%d").date(),
            )
        missing = missing_sessions(window_start, window_end, on_disk)
        if missing:
            any_fail = True
            print(f"\n{label}: {len(missing)} trading day(s) with NO log at all "
                  f"— {', '.join(missing)}")
            print("  Each one is a lost validation session. Market holidays are "
                  "already excluded.")

    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
