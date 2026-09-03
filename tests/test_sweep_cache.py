"""Offline checks for sweep.py's prefetch cache — no API, no alpaca calls.

Run standalone:  python tests/test_sweep_cache.py  (expect "All 9 checks passed.")
Also collected by pytest as test_sweep_cache_suite.

The cache must key on everything that changes the DATA and nothing that only
changes the GRID — otherwise Runs 1-5 of the runbook either refetch needlessly
or, far worse, silently reuse a prefetch built under different screener rules.
"""

from __future__ import annotations

import os
import sys
import tempfile
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ORB_NO_FILE_LOG", "1")

import sweep  # noqa: E402


def mk_args(**over):
    base = dict(
        start="2026-06-08", end="2026-08-08", symbols="",
        min_prev_volume=500_000.0, no_news=False,
        no_cache=False, refresh_cache=False,
        # grid dimensions — must NOT affect the key
        stops="1.5", bias="0.5", trail="1.25", arm="breakeven", tp="off",
        or_minutes="", relvol="", atr_stop="", entry_window="",
        first_candle_dir="off", hold="", no_shorts=True,
    )
    base.update(over)
    return Namespace(**base)


class FakeCfg:
    class screener:
        min_price, max_price = 5.0, 400.0
        min_abs_gap_pct, max_abs_gap_pct, max_candidates = 2.0, 75.0, 40

    class news:
        enabled, lookback_hours = True, 24

    class runtime:
        data_feed = "iex"


def run_all() -> None:
    checks = 0

    def ok(cond, label):
        nonlocal checks
        assert cond, f"FAILED: {label}"
        checks += 1
        print(f"  ok: {label}")

    cfg = FakeCfg()
    base = sweep._cache_key(cfg, mk_args())

    # --- grid dimensions must NOT change the key ---------------------------
    for field, val in [("stops", "1.5,2.0,2.5"), ("bias", "0.5,off"),
                       ("trail", "off"), ("arm", "entry"), ("tp", "cfg"),
                       ("or_minutes", "5,10,15,30"), ("relvol", "1.0,2.0"),
                       ("atr_stop", "1.0"), ("entry_window", "30,60,90,180"),
                       ("first_candle_dir", "on"), ("hold", "180"),
                       ("no_shorts", False)]:
        ok(sweep._cache_key(cfg, mk_args(**{field: val})) == base,
           f"grid dim '{field}' does not change the cache key")

    # --- data-affecting settings MUST change the key ----------------------
    ok(sweep._cache_key(cfg, mk_args(start="2026-01-01")) != base,
       "different --start changes the key")
    ok(sweep._cache_key(cfg, mk_args(end="2026-08-21")) != base,
       "different --end changes the key")
    ok(sweep._cache_key(cfg, mk_args(min_prev_volume=2_000_000.0)) != base,
       "different --min-prev-volume changes the key")
    ok(sweep._cache_key(cfg, mk_args(no_news=True)) != base,
       "--no-news changes the key")
    ok(sweep._cache_key(cfg, mk_args(symbols="NOK,INTC")) != base,
       "--symbols changes the key")

    class Feed(FakeCfg):
        class runtime:
            data_feed = "sip"
    ok(sweep._cache_key(Feed(), mk_args()) != base, "data_feed changes the key")

    class Gap(FakeCfg):
        class screener:
            min_price, max_price = 5.0, 400.0
            min_abs_gap_pct, max_abs_gap_pct, max_candidates = 5.0, 75.0, 40
    ok(sweep._cache_key(Gap(), mk_args()) != base,
       "screener min_abs_gap_pct changes the key")

    # --- _lighten preserves the attributes the sim reads -------------------
    class RawBar:
        def __init__(s):
            s.timestamp = datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc)
            s.open, s.high, s.low, s.close, s.volume = 10, 11, 9, "10.5", 1234

    light = sweep._lighten({"AAA": [RawBar()]})["AAA"][0]
    ok(light.open == 10.0 and light.high == 11.0 and light.low == 9.0
       and light.close == 10.5 and light.volume == 1234.0
       and light.timestamp.tzinfo is not None,
       "_lighten keeps timestamp/OHLCV and coerces to float")

    # --- round-trips through pickle, unchanged ----------------------------
    import pickle
    payload = [(datetime(2026, 8, 21).date(), ["cand"], {"AAA": "sig"},
                sweep._lighten({"AAA": [RawBar()]}), {"AAA": 2.5})]
    back = pickle.loads(pickle.dumps(payload))
    ok(back[0][3]["AAA"][0].close == 10.5 and back[0][4]["AAA"] == 2.5,
       "cache payload survives a pickle round trip")

    # --- cache file actually written and re-read --------------------------
    with tempfile.TemporaryDirectory() as td:
        old_dir, old_pre = sweep.CACHE_DIR, sweep._prefetch_uncached
        sweep.CACHE_DIR = Path(td) / ".sweep_cache"
        calls = []
        sweep._prefetch_uncached = lambda cfg, args: (calls.append(1), payload)[1]
        try:
            a = sweep.prefetch(cfg, mk_args())
            b = sweep.prefetch(cfg, mk_args())
            ok(len(calls) == 1 and len(a) == len(b) == 1,
               "second prefetch is served from cache (no refetch)")
            sweep.prefetch(cfg, mk_args(refresh_cache=True))
            ok(len(calls) == 2, "--refresh-cache forces a refetch")
            sweep.prefetch(cfg, mk_args(no_cache=True))
            ok(len(calls) == 3, "--no-cache bypasses the cache")
        finally:
            sweep.CACHE_DIR, sweep._prefetch_uncached = old_dir, old_pre

    print(f"\nAll {checks} checks passed.")


def test_sweep_cache_suite():
    run_all()


if __name__ == "__main__":
    run_all()
