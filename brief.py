#!/usr/bin/env python3
"""One compact digest of BOTH instances, for handing to Claude.

    python brief.py              # -> BRIEF.md
    python brief.py --sessions 8
    python brief.py --stdout

Why this exists: the raw material is ~500 KB of logs across two instances,
and dashboard.html is ~460 KB. Neither is something you want to paste into a
chat. BRIEF.md is a few KB and carries the things that actually get asked
about - did it run, what did it see, what did it take, what broke - with a
pointer to the files for anything deeper.

Reads both instances from this checkout:
    paper -> ./logs,      ./state
    live  -> ./live/logs, ./live/state
so it is only as fresh as your last sync-both.ps1. The header says when each
side was last written, precisely so a stale sync cannot be mistaken for a
quiet week.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from pipeline import noise as noise_mod      # noqa: E402
from pipeline.health import run_all          # noqa: E402
from pipeline.snapshot import load_sessions  # noqa: E402

INSTANCES = [("paper", ROOT), ("live", ROOT / "live")]
NOISE = ROOT / "noise"        # E16, synced from /opt/orb-noise by sync-both.ps1


def freshness(logs_dir: Path) -> str:
    """Newest SESSION and newest WRITE are different questions.

    A bulk sync stamps every file with the same mtime, so picking the newest
    file by mtime and printing its date gives an arbitrary answer. Report the
    latest session by DATE, and the age of the most recent write separately -
    the pair is what distinguishes "nothing traded" from "nothing synced"."""
    files = sorted(logs_dir.glob("orb_*.log")) if logs_dir.exists() else []
    if not files:
        return "no logs"
    latest = max(f.stem.replace("orb_", "") for f in files)
    newest_write = max(f.stat().st_mtime for f in files)
    age_h = (datetime.now().timestamp() - newest_write) / 3600
    return (f"latest session {latest}, {len(files)} logs, "
            f"last synced {age_h:.0f}h ago")


def session_line(s) -> str:
    booked = sum(float(t.get("pnl") or 0) for t in s.trades)
    done = "done" if s.day_done else "NO Day done"
    flat = "flat" if s.flatten_verified else "NO flatten"
    pnl = f"{booked:+.2f}" if s.trades else "-"
    return (f"| {s.date} | {len(s.targets) + len(s.rescreened)} | "
            f"{len(s.entries)} | {s.rejected_count} | {len(s.trades)} | "
            f"{pnl} | {s.errors} | {done}, {flat} |")


def instance_block(name: str, root: Path, n: int) -> list[str]:
    logs = root / "logs"
    out = [f"## {name}", "", f"- path: `{root}`",
           f"- freshness: {freshness(logs)}"]
    if not logs.exists():
        return out + ["", "**No logs directory - never synced.**", ""]

    sessions = load_sessions(limit=n, logs_dir=logs)
    # trade_history lives beside each instance; load_sessions reads the
    # package-level STATE, so re-attach from this instance's own file.
    import csv
    hist = root / "state" / "trade_history.csv"
    if hist.exists():
        by_day: dict = {}
        with open(hist, newline="", encoding="utf-8", errors="ignore") as fh:
            for r in csv.DictReader(fh):
                by_day.setdefault(r["date"], []).append(r)
        for s in sessions:
            s.trades = by_day.get(s.date, [])
        out.append(f"- booked trades on file: {sum(len(v) for v in by_day.values())}")

    out += ["", "| date | targets | entries | rejected | booked | P&L | err | close |",
            "|---|---|---|---|---|---|---|---|"]
    out += [session_line(s) for s in sessions]

    checks = run_all(root)
    bad = [c for c in checks if c.status != "ok"]
    out += ["", f"**Ops:** {len(checks)} checks, "
                f"{sum(c.failed for c in checks)} fail, "
                f"{sum(c.status == 'warn' for c in checks)} warn"]
    out += [f"- `{c.name}` **{c.status}** - {c.summary}" for c in bad] or \
           ["- all clear"]

    last = sessions[-1] if sessions else None
    if last and last.entries:
        out += ["", f"**Last session detail ({last.date}):**"]
        for e in last.entries:
            fill = next((f for f in last.fills if f["symbol"] == e["symbol"]),
                        None)
            anchor = ("re-anchored" if fill and fill["reanchored"]
                      else "ref-anchored stop")
            got = (f"filled {fill['fill']} ({fill['slippage_pct']:+.2f}% slip, "
                   f"{anchor})" if fill else "no fill line logged")
            out.append(f"- {e['t']} {e['side']} {e['symbol']} qty={e['qty']} "
                       f"ref={e['ref']} SL={e['sl']} ({e['sl_pct']:+.2f}%) - {got}")
        for t in last.trail:
            out.append(f"- {t['t']} {t['kind']}: {t['line'][:110]}")
    out.append("")
    return out


def noise_block(root: Path, n: int) -> list[str]:
    """E16. A different shape of record, so it gets its own reader and its own table.

    The ORB block above is built from round-trip trades. E16 has no trades in that
    sense: it holds a position between checkpoints and flattens at the close, so
    what matters per session is whether every checkpoint was logged, whether it
    ended flat, and what the evening job scored. P&L deliberately is NOT shown
    per session - the evidence is the SIP shadow record, not the paper account,
    and putting a daily number here would invite reading it as the result."""
    out = [f"## noise (E16 QQQ)", "", f"- path: `{root}`"]
    if not (root / "logs").exists() and not (root / "state" / "noise_ledger.csv").exists():
        return out + ["", "**Never synced.** Run `sync-both.ps1`; if that still finds "
                          "nothing, the runner has not had a session yet "
                          "(forward clock starts 2026-10-28).", ""]
    out.append(f"- freshness: {noise_mod.freshness(root)}")
    ss = noise_mod.sessions(root, limit=n)
    if not ss:
        out += ["", "**No ledger rows yet.** The runner has logs but wrote no decision.", ""]
        return out

    all_days = noise_mod.sessions(root)
    out.append(f"- Stage A: {len(all_days)} of 20 sessions, "
               f"{sum(s.full_price_coverage for s in all_days)} with all "
               f"{noise_mod.EXPECTED_CHECKPOINTS} checkpoints priced, "
               f"{sum(s.flat is True for s in all_days)} verified flat")
    out += ["", "| date | checkpoints | priced | traded | flips | into close | flat | err |",
            "|---|---|---|---|---|---|---|---|"]
    for s in ss:
        flat = "yes" if s.flat else ("NO" if s.flat is False else "-")
        out.append(f"| {s.day} | {s.priced + s.held}/{noise_mod.EXPECTED_CHECKPOINTS} | "
                   f"{s.priced} | {s.traded} | {s.flips} | {s.pos_close or '-'} | "
                   f"{flat} | {s.errors} |")

    warn = noise_mod.warnings(root, all_days)
    out += ["", f"**Ops:** {len(warn)} warn"] + ([f"- {w}" for w in warn] or ["- all clear"])

    eod = noise_mod.latest_eod(root)
    if eod:
        day, lines = eod
        out += ["", f"**Evening job ({day}):**"] + [f"- {ln}" for ln in lines]
    out.append("")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=5)
    ap.add_argument("-o", "--out", default="BRIEF.md")
    ap.add_argument("--stdout", action="store_true")
    a = ap.parse_args()

    doc = [f"# ORB brief - generated {datetime.now():%Y-%m-%d %H:%M} local", "",
           "Paste this, or just say \"read BRIEF.md\". Deeper material lives in "
           "`dashboard.html`, `logs/`, `live/logs/`, `noise/logs/` and `state/`.", ""]
    for name, root in INSTANCES:
        doc += instance_block(name, root, a.sessions)
    doc += noise_block(NOISE, a.sessions)
    paper_logs = sorted((ROOT / "logs").glob("orb_*.log"))
    live_logs = sorted((ROOT / "live" / "logs").glob("orb_*.log"))
    if paper_logs and live_logs:
        p_last = max(f.stem.replace("orb_", "") for f in paper_logs)
        l_last = max(f.stem.replace("orb_", "") for f in live_logs)
        if p_last != l_last:
            behind, ahead = ((("live", l_last), ("paper", p_last))
                             if l_last < p_last else
                             (("paper", p_last), ("live", l_last)))
            doc += [f"> **Sync gap:** {behind[0]} has nothing after "
                    f"{behind[1]} while {ahead[0]} reaches {ahead[1]}. "
                    f"Either {behind[0]} did not run, or it was not synced. "
                    f"Re-run `sync-both.ps1` before concluding anything.", ""]
    doc += ["---", "", "Generated by `brief.py`. Freshness comes from the last "
            "`sync-both.ps1`; a stale sync looks like a quiet week, so check "
            "the per-instance freshness line before reading anything into a "
            "gap."]
    # PowerShell 5.1's Get-Content reads UTF-8 as ANSI, which turns an em
    # dash into "a~EUR~". This file is read in that console, so keep it ASCII.
    text = "\n".join(doc).encode("ascii", "replace").decode("ascii")
    if a.stdout:
        print(text)
    else:
        p = Path(a.out)
        p.write_text(text, encoding="utf-8")
        print(f"{p} written ({p.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
