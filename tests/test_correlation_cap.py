"""Offline checks for the v1.14 correlated-theme cap. No API, no alpaca.

Run standalone:  python tests/test_correlation_cap.py
Also collected by pytest as test_correlation_cap_suite.

The cap exists because on 2026-08-21 the bot held MARA, CONL, ETHA, BITO and
IBIT simultaneously (-46.92) and on 08-24 held BMNR, ETHA, IBIT, BITO. The
v1.7 same-underlying cap sees five different underlyings; the market saw one
bet. These tests pin the behaviour that fixes it AND the fail-safes that stop
it from becoming a silent veto.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ORB_NO_FILE_LOG", "1")

from orb_bot.selection import (  # noqa: E402
    Target, aligned_returns, cap_correlated, pearson,
)

DATES = [f"2026-07-{d:02d}" for d in range(1, 31)]


def series(vals, dates=None):
    return dict(zip(dates or DATES, vals))


def tgt(sym, score):
    return Target(symbol=sym, bias="any", score=score, gap_pct=0.0,
                  sentiment=0.0, n_articles=0, reason="test")


def run_all() -> None:
    checks = 0

    def ok(cond, label):
        nonlocal checks
        assert cond, f"FAILED: {label}"
        checks += 1
        print(f"  ok: {label}")

    # ---- pearson ---------------------------------------------------------
    a = [0.01, -0.02, 0.03, -0.01, 0.02, 0.00, -0.03, 0.01, 0.02, -0.01, 0.01, 0.02]
    ok(abs(pearson(a, a) - 1.0) < 1e-9, "pearson: identical series = 1.0")
    ok(abs(pearson(a, [-x for x in a]) + 1.0) < 1e-9, "pearson: inverted = -1.0")
    ok(pearson(a[:5], a[:5]) is None, "pearson: too few observations -> None")
    ok(pearson(a, [0.01] * len(a)) is None, "pearson: flat series -> None")

    # ---- date alignment --------------------------------------------------
    x, y = aligned_returns(
        {"2026-07-01": 1.0, "2026-07-02": 2.0, "2026-07-03": 3.0},
        {"2026-07-02": 9.0, "2026-07-03": 8.0, "2026-07-09": 7.0},
    )
    ok(x == [2.0, 3.0] and y == [9.0, 8.0],
       "aligned_returns intersects on DATE, not list position")

    # ---- the 2026-08-21 scenario ----------------------------------------
    # Five crypto names moving together, plus two unrelated names.
    crypto = [0.03, -0.02, 0.05, -0.04, 0.02, 0.01, -0.03, 0.04, -0.01, 0.02,
              0.03, -0.05, 0.01, 0.02, -0.02, 0.03, 0.01, -0.01, 0.04, -0.03]
    # Deterministic pseudo-noise, sized so peers land at a REALISTIC ~0.85-0.95
    # rather than a suspiciously perfect 1.00 — otherwise the test would pass
    # for any threshold and prove nothing about where the cut actually falls.
    def noise(k, i):
        return (((i * 7919 + k * 104729) % 1000) / 1000.0 - 0.5)

    def jitter(s, k, amp=0.03):
        return [v + amp * noise(k, i) for i, v in enumerate(s)]
    tsll = [-0.01, 0.03, -0.02, 0.01, 0.04, -0.03, 0.02, -0.01, 0.03, 0.01,
            -0.04, 0.02, 0.03, -0.01, 0.01, -0.02, 0.04, 0.02, -0.03, 0.01]
    mrna = [0.02, 0.01, -0.03, 0.04, -0.01, 0.02, 0.03, -0.02, 0.01, -0.04,
            0.02, 0.01, -0.01, 0.03, 0.04, -0.03, 0.02, -0.01, 0.01, 0.02]
    d20 = DATES[:20]
    rets = {
        "MARA": series(jitter(crypto, 0), d20),
        "CONL": series(jitter(crypto, 1), d20),
        "ETHA": series(jitter(crypto, 2), d20),
        "BITO": series(jitter(crypto, 3), d20),
        "IBIT": series(jitter(crypto, 4), d20),
        "TSLL": series(tsll, d20),
        "MRNA": series(mrna, d20),
    }
    # Score order: the crypto names outrank the others, as they did live.
    targets = [tgt("MARA", 9), tgt("CONL", 8), tgt("ETHA", 7), tgt("BITO", 6),
               tgt("IBIT", 5), tgt("TSLL", 4), tgt("MRNA", 3)]
    kept = [t.symbol for t in cap_correlated(targets, rets, 0.80, 1)]
    ok(kept == ["MARA", "TSLL", "MRNA"],
       f"08-21 replay: 5 crypto names collapse to the strongest, uncorrelated "
       f"names survive (kept {kept})")
    ok(len(kept) == 3 and "TSLL" in kept and "MRNA" in kept,
       "the two winners that day (TSLL +13.24, MRNA +9.01) are NOT dropped")

    # ---- cap = 2 allows a pair ------------------------------------------
    kept2 = [t.symbol for t in cap_correlated(targets, rets, 0.80, 2)]
    ok(kept2[:2] == ["MARA", "CONL"] and len(kept2) == 4,
       f"max_per_cluster=2 keeps two of the group (kept {kept2})")

    # ---- fail-safes: unknown must never block ---------------------------
    ok([t.symbol for t in cap_correlated(targets, {}, 0.80, 1)]
       == [t.symbol for t in targets],
       "no return history at all -> every target kept (cap inert)")
    partial = {k: v for k, v in rets.items() if k not in ("CONL", "BITO")}
    kept3 = [t.symbol for t in cap_correlated(targets, partial, 0.80, 1)]
    ok("CONL" in kept3 and "BITO" in kept3,
       "symbols with unknown history are kept, never vetoed")
    short_hist = dict(rets)
    short_hist["CONL"] = series(jitter(crypto, 1)[:6], d20[:6])
    kept4 = [t.symbol for t in cap_correlated(targets, short_hist, 0.80, 1)]
    ok("CONL" in kept4, "too-short history is kept (pearson returns None)")

    # ---- disabled paths --------------------------------------------------
    ok([t.symbol for t in cap_correlated(targets, rets, 0.0, 1)]
       == [t.symbol for t in targets], "max_correlation 0 disables the cap")
    ok([t.symbol for t in cap_correlated(targets, rets, 0.80, 0)]
       == [t.symbol for t in targets], "max_per_cluster 0 disables the cap")

    # ---- taken_returns: the intraday re-screen path ----------------------
    # Four crypto names already held; a fifth must not be added at 10:00.
    taken = {s: rets[s] for s in ("MARA", "CONL", "ETHA", "BITO")}
    fresh = [tgt("IBIT", 9), tgt("MRNA", 8)]
    kept5 = [t.symbol for t in cap_correlated(
        fresh, {"IBIT": rets["IBIT"], "MRNA": rets["MRNA"]}, 0.80, 1, taken)]
    ok(kept5 == ["MRNA"],
       "re-screen: a 5th correlated name is blocked by already-held positions")

    # ---- negative correlation is NOT capped ------------------------------
    # An inverse ETF moves opposite, not together — it is a different bet and
    # capping it would be wrong. Only >= +max_corr counts.
    inv = {"SQQQ": series([-v for v in crypto], d20)}
    kept6 = [t.symbol for t in cap_correlated(
        [tgt("MARA", 9), tgt("SQQQ", 8)],
        {"MARA": rets["MARA"], **inv}, 0.80, 1)]
    ok(kept6 == ["MARA", "SQQQ"], "inverse (negatively correlated) name is kept")

    # ---- the threshold must actually discriminate ------------------------
    # A loosely-related name (~0.5-0.7) is a different bet and must survive a
    # 0.80 cap. Without this the suite would pass even if the cap dropped
    # everything that moved vaguely together.
    loose = series(jitter(crypto, 42, amp=0.14), d20)
    c_loose = pearson(*aligned_returns(loose, rets["MARA"]))
    ok(0.2 < c_loose < 0.80,
       f"test fixture sanity: loose name correlates {c_loose:.2f} (below cut)")
    kept_loose = [t.symbol for t in cap_correlated(
        [tgt("MARA", 9), tgt("LOOSE", 8)],
        {"MARA": rets["MARA"], "LOOSE": loose}, 0.80, 1)]
    ok(kept_loose == ["MARA", "LOOSE"],
       "a loosely-correlated name is NOT capped at threshold 0.80")
    c_peer = pearson(*aligned_returns(rets["CONL"], rets["MARA"]))
    peers_vs_lead = [
        pearson(*aligned_returns(rets[s], rets["MARA"]))
        for s in ("CONL", "ETHA", "BITO", "IBIT")
    ]
    ok(all(0.80 <= c < 0.999 for c in peers_vs_lead),
       f"test fixture sanity: theme peers correlate "
       f"{[f'{c:.2f}' for c in peers_vs_lead]} — realistically high, not a "
       f"perfect 1.00, so the 0.80 cut is genuinely exercised")

    # ---- ordering: strongest survives ------------------------------------
    rev = [tgt("IBIT", 9), tgt("MARA", 8), tgt("CONL", 7)]
    ok([t.symbol for t in cap_correlated(rev, rets, 0.80, 1)] == ["IBIT"],
       "the highest-scored member of a group is the one kept")

    print(f"\nAll {checks} checks passed.")


def test_correlation_cap_suite():
    run_all()


if __name__ == "__main__":
    run_all()
