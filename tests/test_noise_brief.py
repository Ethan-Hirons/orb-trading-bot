"""Tests for pipeline/noise.py - the E16 block in BRIEF.md.

Runs WITHOUT pytest (it is not installed on the bot's interpreter):

    python tests/test_noise_brief.py

Builds synthetic noise/ trees in a temp dir; nothing here touches real state.
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import noise as N  # noqa: E402

PASS, FAILED = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAILED).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}{('  ' + extra) if extra else ''}")


HEAD = "day,checkpoint,price,price_time,sigma,ub,lb,pos_before,pos_after,orders\n"
TRADED = '"[[""buy"", 10, 601.2, ""oid""]]"'


def row(day, cp, price="601.0", before=0, after=0, orders=""):
    return (f"{day},{cp},{price},10:00:02,0.004,604.0,598.0,"
            f"{before},{after},{orders}\n")


def mkrepo(tmp, ledger=None, logs=None):
    root = Path(tmp) / f"noise{time.time_ns()}"
    (root / "logs").mkdir(parents=True)
    (root / "state").mkdir(parents=True)
    if ledger is not None:
        (root / "state" / "noise_ledger.csv").write_text(ledger, encoding="utf-8")
    for name, text in (logs or {}).items():
        (root / "logs" / name).write_text(text, encoding="utf-8")
    return root


def full_day(day, traded_at=("10:00",), flat=True, flips=0):
    """A complete 12-checkpoint session, optionally with trades and a close row."""
    cps = [f"{h:02d}:{m:02d}" for h in range(10, 16) for m in (0, 30)]
    out = HEAD
    pos = 0
    for cp in cps:
        before = pos
        if cp in traded_at:
            pos = -1 if (flips and before == 1) else 1
            out += row(day, cp, before=before, after=pos, orders=TRADED)
        else:
            out += row(day, cp, before=before, after=pos)
    out += (f"{day},close,601.5,,,,,{pos},{0 if flat else pos},"
            f'"[[""filled"", 601.5, ""oid""]]"\n')
    return out


def main():
    with tempfile.TemporaryDirectory() as tmp:
        print("\nledger parsing")
        # A clean day includes the evening job: without it the brief SHOULD warn,
        # which is what the "missing evening job" case below asserts.
        r = mkrepo(tmp, full_day("2026-10-28"),
                   {"noise_2026-10-28.log": "session 2026-10-28 done: flat=True\n",
                    "noise_eod_2026-10-28.log": "RUNNING: 1/250 active sessions\n"})
        ss = N.sessions(r)
        check("one session", len(ss) == 1)
        s = ss[0]
        check("12 checkpoints priced", s.priced == 12, f"got {s.priced}")
        check("coverage true", s.covered and s.full_price_coverage)
        check("one decision traded", s.traded == 1, f"got {s.traded}")
        check("flat at close", s.flat is True)
        check("session done parsed", s.done)
        check("no warnings on a clean day", N.warnings(r, ss) == [], str(N.warnings(r, ss)))

        print("\nthe checkpoint count is the frozen one")
        try:
            import index_scan  # noqa: F401
            check("matches index_scan.CHECKPOINTS",
                  N.EXPECTED_CHECKPOINTS == len(index_scan.CHECKPOINTS))
        except Exception as e:  # noqa: BLE001
            check("index_scan not importable here, constant unchecked", True, f"({e})")

        print("\nheld checkpoints count toward coverage but not toward 'priced'")
        led = HEAD + row("2026-10-28", "10:00", price="")
        for cp in ("10:30", "11:00"):
            led += row("2026-10-28", cp)
        r = mkrepo(tmp, led)
        s = N.sessions(r)[0]
        check("held counted", s.held == 1 and s.priced == 2)
        check("partial coverage flagged",
              any("checkpoints logged" in w for w in N.warnings(r, N.sessions(r))))

        print("\na session that is not flat is loud")
        r = mkrepo(tmp, full_day("2026-10-29", flat=False))
        w = N.warnings(r, N.sessions(r))
        check("NOT FLAT surfaced", any("NOT FLAT" in x for x in w), str(w))

        print("\nno close row at all")
        led = HEAD + "".join(row("2026-10-30", f"{h:02d}:{m:02d}")
                             for h in range(10, 16) for m in (0, 30))
        r = mkrepo(tmp, led)
        s = N.sessions(r)[0]
        check("flat is None", s.flat is None)
        check("did-not-reach-16:05 surfaced",
              any("did not reach" in x for x in N.warnings(r, [s])))

        print("\na log with no ledger rows is the failure that must not hide")
        r = mkrepo(tmp, full_day("2026-10-28"),
                   {"noise_2026-10-28.log": "ok\n",
                    "noise_2026-10-29.log": "died before the first checkpoint\n"})
        w = N.warnings(r, N.sessions(r))
        check("orphan log surfaced", any("no ledger rows" in x for x in w), str(w))

        print("\nERROR lines are counted")
        r = mkrepo(tmp, full_day("2026-10-28"),
                   {"noise_2026-10-28.log": "[ERROR] buy 10 QQQ did not fill\n"})
        s = N.sessions(r)[0]
        check("one error", s.errors == 1)
        check("error surfaced", any("ERROR line" in x for x in N.warnings(r, [s])))

        print("\nDISARM file is surfaced")
        r = mkrepo(tmp, full_day("2026-10-28"))
        (r / "state" / "NOISE_DISARMED").write_text("", encoding="utf-8")
        check("paused surfaced",
              any("NOISE_DISARMED" in x for x in N.warnings(r, N.sessions(r))))

        print("\nevening job: the lines that matter, and staleness")
        eod = ("E16 Stage A: 1 sessions, full coverage 1/1, verified flat 1/1\n"
               "  decision agreement with SIP shadow: 12/12 (100.0%)\n"
               "  adverse slippage per side: mean +0.41 bp over 2 fills\n"
               "E16 forward shadow, QQQ noise-area base model, sessions >= 2026-10-28\n"
               "  0.5 bp/side (scored floor)   sessions 1 active 1 mean +12.0 bp\n"
               "    RUNNING: 1/250 active sessions, next look at 60\n")
        r = mkrepo(tmp, full_day("2026-10-29"),
                   {"noise_2026-10-29.log": "ok\n", "noise_eod_2026-10-28.log": eod})
        got = N.latest_eod(r)
        check("eod day", got and got[0] == "2026-10-28")
        check("verdict kept", any("RUNNING" in ln for ln in got[1]))
        check("stage A kept", any("agreement" in ln for ln in got[1]))
        check("noise in the prose is not kept",
              not any("noise-area base model" in ln for ln in got[1]))
        check("stale evening job surfaced",
              any("stale" in x for x in N.warnings(r, N.sessions(r))))

        print("\nmissing evening job is surfaced")
        r = mkrepo(tmp, full_day("2026-10-28"), {"noise_2026-10-28.log": "ok\n"})
        check("missing eod surfaced",
              any("evening job" in x for x in N.warnings(r, N.sessions(r))))

        print("\nan unsynced tree does not crash")
        empty = Path(tmp) / "nothing"
        check("sessions empty", N.sessions(empty) == [])
        check("freshness says so", N.freshness(empty) == "no logs")
        check("latest_eod None", N.latest_eod(empty) is None)

    print(f"\n{len(PASS)} passed, {len(FAILED)} failed")
    for f in FAILED:
        print(f"  FAILED: {f}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
