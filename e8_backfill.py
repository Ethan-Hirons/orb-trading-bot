"""E8 - what happened to the breakouts the confirmation rule rejected.

Criteria are locked in PREREG-E8-2026-10-22.md. This script enforces them:
the validation gate runs FIRST and the E8 result is not printed if it fails.

Reuses replay.py's engine on purpose. That engine was wrong twice and was
caught twice by validating against real trades; writing a second simulator
here would throw that away.

Needs .env and market data, so it runs on the laptop or the VPS, not in the
cloud container (yfinance/Alpaca are blocked at that proxy).

    python -u e8_backfill.py --feed iex
    python -u e8_backfill.py --feed sip      # sensitivity check
"""
from __future__ import annotations
import argparse, csv, statistics as st
from pathlib import Path

import replay as R

ROOT = Path(__file__).resolve().parent
EVENTS = ROOT / "state" / "rejected_breakouts_backfill.csv"
OUT = ROOT / "state" / "e8_results.csv"

RULE = {"stop_pct": R.LIVE_STOP_PCT,
        "trail_pct": R.LIVE_TRAIL_PCT,
        "trail_arm": R.LIVE_TRAIL_PCT}
MODE = "touch"
OR_END = "09:45"          # opening_range_minutes: 15, bars 09:30..09:44
ENTRY_CUTOFF = "10:30"    # entry_window_minutes: 60
ENTRY_SLIP_PCT = 0.046    # E1 measured mean, applied adversely to BOTH arms

# Gate thresholds - from the prereg, do not edit after the first run.
GATE_MED_ABS = 0.25
GATE_MEAN_SIGNED = 0.10


def or_level(bars, side):
    """Opening range high/low from the bars themselves, not from the log."""
    orb = [b for b in bars if b["t"] < OR_END]
    if not orb:
        return None
    return max(b["h"] for b in orb) if side == "long" else min(b["l"] for b in orb)


def simulate(bars, date, symbol, side, entry_time, min_trades):
    """Enter at the OR level at entry_time, exit with the live stack."""
    lvl = or_level(bars, side)
    if lvl is None:
        return None, "no-or"
    sign = 1 if side == "long" else -1
    entry = lvl * (1 + sign * ENTRY_SLIP_PCT / 100.0)
    t = R.Trade(date=date, symbol=symbol, side=side, qty=1.0,
                entry=entry, exit=0.0, pnl=0.0, pnl_pct=0.0,
                entry_time=entry_time, bars=bars)
    px, why = R.replay(t, RULE, MODE, min_trades)
    if px is None:
        return None, why
    return t.pct(px), why


def describe(vals, label):
    if not vals:
        print(f"{label:<28} n=0")
        return None
    n, mean = len(vals), st.mean(vals)
    w = [v for v in vals if v > 0]
    lo = [v for v in vals if v <= 0]
    aw = st.mean(w) if w else 0.0
    al = st.mean(lo) if lo else 0.0
    payoff = abs(aw / al) if al else float("inf")
    drop2 = sorted(vals)[:-2]
    print(f"{label:<28} n={n:>4} mean={mean:+7.3f}%  win={100*len(w)/n:5.1f}%  "
          f"payoff={payoff:5.3f}  ex-top2 mean={st.mean(drop2):+7.3f}%")
    return mean


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="iex", choices=["iex", "sip"])
    ap.add_argument("--min-trades", type=int, default=3)
    ap.add_argument("--force", action="store_true",
                    help="print the E8 result even if the gate fails (never use)")
    a = ap.parse_args()

    client = R._client()

    # ---------------- validation gate ----------------
    print("=" * 78)
    print(f"VALIDATION GATE  (feed={a.feed}, mode={MODE}, min_trades={a.min_trades})")
    print("=" * 78)
    taken = R.load_trades()
    taken = R.attach_bars(taken, a.feed)
    errs, scored, skipped = [], [], 0
    for t in taken:
        px, why = R.replay(t, RULE, MODE, a.min_trades)
        if px is None:
            skipped += 1
            continue
        errs.append(t.pct(px) - t.pnl_pct)
        scored.append(t)
    if not errs:
        raise SystemExit("gate: nothing scoreable")
    med_abs = st.median([abs(e) for e in errs])
    mean_signed = st.mean(errs)
    print(f"  scored {len(errs)} taken trades ({skipped} unreplayable: halt / no bars)")
    print(f"  median |error| {med_abs:.3f} pp   (gate < {GATE_MED_ABS})")
    print(f"  mean   error   {mean_signed:+.3f} pp   (gate within +/-{GATE_MEAN_SIGNED})")
    passed = med_abs < GATE_MED_ABS and abs(mean_signed) <= GATE_MEAN_SIGNED
    print(f"  GATE: {'PASS' if passed else 'FAIL'}")
    if not passed and not a.force:
        print("\nGate failed. Per PREREG-E8-2026-10-22.md the E8 result is NOT read.")
        print("The simulator does not reproduce trades whose outcome is known, so")
        print("its verdict on trades whose outcome is unknown means nothing.")
        return

    # ---------------- arm A: rejected ----------------
    rows = list(csv.DictReader(open(EVENTS)))
    results, dropped = [], {}
    for r in rows:
        bars = R.get_bars(client, r["symbol"], r["date"], a.feed)
        if not bars:
            dropped["no-bars"] = dropped.get("no-bars", 0) + 1
            continue
        et = r["reject_time"][:5]
        if et >= ENTRY_CUTOFF:          # past 10:30 the bot could not have entered
            dropped["past-cutoff"] = dropped.get("past-cutoff", 0) + 1
            continue
        pct, why = simulate(bars, r["date"], r["symbol"], r["side"], et, a.min_trades)
        if pct is None:
            dropped[why] = dropped.get(why, 0) + 1
            continue
        results.append({**r, "pct": round(pct, 4), "exit": why})

    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
        w.writeheader(); w.writerows(results)

    # ---------------- arm B: taken, same convention ----------------
    arm_b = []
    for t in scored:
        pct, why = simulate(t.bars, t.date, t.symbol, t.side,
                            t.entry_time or OR_END, a.min_trades)
        if pct is not None:
            arm_b.append({"date": t.date, "pct": pct, "booked": t.pnl_pct})

    print("\n" + "=" * 78)
    print("E8 RESULT")
    print("=" * 78)
    print(f"dropped from arm A: {dropped}")
    A = [r["pct"] for r in results]
    B = [r["pct"] for r in arm_b]
    print()
    ma = describe(A, "REJECTED (simulated)")
    mb = describe(B, "TAKEN    (simulated)")
    describe([r["booked"] for r in arm_b], "TAKEN    (booked, ref)")
    print(f"\ndifference rejected - taken: {ma - mb:+.3f} pp per event")

    print("\n--- robustness ---")
    for lbl, lo, hi in (("look  (..2026-09-30)", "0000", "2026-09-30"),
                        ("hold  (2026-10-01..)", "2026-10-01", "9999")):
        sa = [r["pct"] for r in results if lo <= r["date"] <= hi]
        sb = [r["pct"] for r in arm_b if lo <= r["date"] <= hi]
        if sa and sb:
            print(f"{lbl}: rejected {st.mean(sa):+7.3f}%  taken {st.mean(sb):+7.3f}%  "
                  f"diff {st.mean(sa)-st.mean(sb):+7.3f} pp   (nA={len(sa)} nB={len(sb)})")
    d2a, d2b = sorted(A)[:-2], sorted(B)[:-2]
    print(f"ex-top2 both arms   : diff {st.mean(d2a)-st.mean(d2b):+7.3f} pp")

    print("\n--- exit mix, rejected arm ---")
    mix = {}
    for r in results:
        mix[r["exit"]] = mix.get(r["exit"], 0) + 1
    for k, v in sorted(mix.items(), key=lambda kv: -kv[1]):
        sub = [r["pct"] for r in results if r["exit"] == k]
        print(f"  {k:<16} n={v:>4}  mean={st.mean(sub):+7.3f}%")
    print(f"\nper-event rows -> {OUT}")
    print("\nRead PREREG-E8-2026-10-22.md section 'What each outcome authorises'")
    print("before drawing any conclusion. No config change is authorised by this.")


if __name__ == "__main__":
    main()
