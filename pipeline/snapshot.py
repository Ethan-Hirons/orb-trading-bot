"""Turn a session log into structured data.

The logs already contain everything the bot saw and decided; it is just
trapped in prose. This extracts it so a session can be read at a glance and
so the rejected-breakout question (E8) has a structure to hang off.

Parses only lines whose format is stable across the whole log history. Any
line that does not match is ignored rather than guessed at - a snapshot that
silently invents a field is worse than one with a gap.
"""

from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import LOGS, STATE

LOG_RE = re.compile(r"orb_(\d{4}-\d{2}-\d{2})\.log$")
TIME_RE = re.compile(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d)")

RX = {
    "target": re.compile(
        r"TARGET (\w+) \| gap ([+-][\d.]+)%, sentiment ([+-][\d.]+) "
        r"\((\d+) articles?\), bias (\w+) \| score ([\d.]+)"),
    "rescreen": re.compile(r"INTRADAY RE-SCREEN: added (\w+)\((\w+)\)"),
    "unconfirmed": re.compile(r"(\w+) broke out (\w+) but not confirmed"),
    "relvol": re.compile(
        r"RELVOL (\w+): opening range volume ([\d,]+) vs ([\d,]+) avg -> "
        r"([\d.]+)x"),
    "enter": re.compile(
        r"ENTER (LONG|SHORT) (\w+) qty=(\S+) ref=([\d.]+) TP=([\d.]+)"
        r"\(([+-][\d.]+)%\) SL=([\d.]+)\(([+-][\d.]+)%\)"),
    "fill": re.compile(
        r"(\w+): fill ([\d.]+) \(ref ([\d.]+), ([+-][\d.]+)% slippage\)"),
    "equity": re.compile(r"Equity ([\d.]+) \| PnL ([+-][\d.]+)%"),
    "trail_act": re.compile(r"TRAIL ACTIVATED (\w+)"),
    "trail_def": re.compile(r"TRAIL DEFERRED (\w+)"),
    "trail_exit": re.compile(r"TRAIL EXIT (\w+)"),
    "news_exit": re.compile(r"ADVERSE NEWS EXIT: closing (?:LONG|SHORT) (\w+)"),
    "news": re.compile(
        r"NEWS (\w+): (\d+) fresh article\(s\), score ([+-][\d.]+) \| (.+)$"),
    "corr": re.compile(r"CORRELATION[^:]*: ?(.+)$"),
    "dup": re.compile(r"DUP-UNDERLYING (\w+):"),
    "blocked": re.compile(r"BLOCKED (\w+)"),
    "daydone": re.compile(
        r"Day done\. Trades: (\d+) \| End equity: ([\d.]+) \| "
        r"PnL: ([+-][\d.]+)"),
}


@dataclass
class Session:
    date: str
    targets: list[dict] = field(default_factory=list)
    rescreened: list[dict] = field(default_factory=list)
    unconfirmed: list[dict] = field(default_factory=list)
    relvol: list[dict] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    trail: list[dict] = field(default_factory=list)
    news: list[dict] = field(default_factory=list)
    vetoes: list[dict] = field(default_factory=list)
    equity: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    errors: int = 0
    day_done: dict | None = None
    flatten_verified: bool = False

    @property
    def rejected_count(self) -> int:
        """Distinct symbols that broke out but never confirmed AND were never
        entered. This is the E8 population."""
        entered = {e["symbol"] for e in self.entries}
        return len({u["symbol"] for u in self.unconfirmed} - entered)


def _t(line: str) -> str:
    m = TIME_RE.match(line)
    return m.group(1) if m else ""


def parse_log(path: Path) -> Session:
    day = LOG_RE.search(path.name)
    s = Session(date=day.group(1) if day else path.stem)
    for ln in open(path, errors="ignore"):
        t = _t(ln)
        if "[ERROR]" in ln:
            s.errors += 1
        if "Flatten verified" in ln:
            s.flatten_verified = True

        m = RX["target"].search(ln)
        if m:
            s.targets.append({"t": t, "symbol": m.group(1),
                              "gap_pct": float(m.group(2)),
                              "sentiment": float(m.group(3)),
                              "articles": int(m.group(4)),
                              "bias": m.group(5),
                              "score": float(m.group(6))})
            continue
        m = RX["rescreen"].search(ln)
        if m:
            s.rescreened.append({"t": t, "symbol": m.group(1),
                                 "bias": m.group(2)})
            continue
        m = RX["unconfirmed"].search(ln)
        if m:
            s.unconfirmed.append({"t": t, "symbol": m.group(1),
                                  "side": m.group(2)})
            continue
        m = RX["relvol"].search(ln)
        if m:
            s.relvol.append({"t": t, "symbol": m.group(1),
                             "or_volume": int(m.group(2).replace(",", "")),
                             "avg_volume": int(m.group(3).replace(",", "")),
                             "ratio": float(m.group(4))})
            continue
        m = RX["enter"].search(ln)
        if m:
            s.entries.append({"t": t, "side": m.group(1).lower(),
                              "symbol": m.group(2), "qty": m.group(3),
                              "ref": float(m.group(4)),
                              "tp": float(m.group(5)),
                              "tp_pct": float(m.group(6)),
                              "sl": float(m.group(7)),
                              "sl_pct": float(m.group(8))})
            continue
        m = RX["fill"].search(ln)
        if m:
            s.fills.append({"t": t, "symbol": m.group(1),
                            "fill": float(m.group(2)),
                            "ref": float(m.group(3)),
                            "slippage_pct": float(m.group(4)),
                            "reanchored": "re-anchored" in ln})
            continue
        for key, kind in (("trail_act", "activated"),
                          ("trail_def", "deferred"),
                          ("trail_exit", "exit"),
                          ("news_exit", "news_exit")):
            m = RX[key].search(ln)
            if m:
                s.trail.append({"t": t, "symbol": m.group(1), "kind": kind,
                                "line": ln.split("] ", 1)[-1].strip()})
                break
        else:
            m = RX["news"].search(ln)
            if m:
                s.news.append({"t": t, "symbol": m.group(1),
                               "articles": int(m.group(2)),
                               "score": float(m.group(3)),
                               "headline": m.group(4).strip()})
                continue
            for key, kind in (("corr", "correlation"), ("dup", "duplicate"),
                              ("blocked", "blocked")):
                m = RX[key].search(ln)
                if m:
                    s.vetoes.append({"t": t, "kind": kind,
                                     "line": ln.split("] ", 1)[-1].strip()})
                    break
            else:
                m = RX["equity"].search(ln)
                if m:
                    s.equity.append({"t": t, "equity": float(m.group(1)),
                                     "pnl_pct": float(m.group(2))})
                    continue
                m = RX["daydone"].search(ln)
                if m:
                    s.day_done = {"trades": int(m.group(1)),
                                  "end_equity": float(m.group(2)),
                                  "pnl": float(m.group(3))}
    return s


def attach_trades(sessions: list[Session]) -> None:
    f = STATE / "trade_history.csv"
    if not f.exists():
        return
    by_day: dict[str, list[dict]] = {}
    with open(f, newline="", encoding="utf-8", errors="ignore") as fh:
        for r in csv.DictReader(fh):
            by_day.setdefault(r["date"], []).append(r)
    for s in sessions:
        s.trades = by_day.get(s.date, [])


def load_sessions(limit: int | None = None,
                  logs_dir: Path | None = None) -> list[Session]:
    d = Path(logs_dir) if logs_dir else LOGS
    paths = sorted(d.glob("orb_*.log"))
    if limit:
        paths = paths[-limit:]
    out = [parse_log(p) for p in paths]
    attach_trades(out)
    return out


def to_dicts(sessions: list[Session]) -> list[dict]:
    out = []
    for s in sessions:
        d = asdict(s)
        d["rejected_count"] = s.rejected_count
        out.append(d)
    return out
