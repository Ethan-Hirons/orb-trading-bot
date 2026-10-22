"""E8 step 1 - recover every rejected breakout from the session logs.

The bot logs the rejection event but never what the symbol did next, so the
outcome has to be backfilled from market data (e8_backfill.py). This script
produces the event list only. Offline, no network, no keys.

Live and paper see the SAME screen and the SAME confirmation rule, so their
rejections are near-duplicates. Live events are kept only when paper has no
matching (date, symbol, side); otherwise the sample is inflated.
"""
import csv, glob, os, re, sys

PAT = re.compile(
    r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d),\d+ \[INFO\] (\S+) "
    r"broke out (long|short) but not confirmed"
)

def scan(logdir, source):
    seen, rows = set(), []
    for path in sorted(glob.glob(os.path.join(logdir, "orb_*.log"))):
        for line in open(path, encoding="utf-8", errors="replace"):
            m = PAT.match(line)
            if not m:
                continue
            date, tstamp, sym, side = m.groups()
            key = (date, sym, side)
            if key in seen:      # "further waits logged silently" - first only
                continue
            seen.add(key)
            rows.append(dict(date=date, symbol=sym, side=side,
                             reject_time=tstamp, source=source))
    return rows

paper = scan("logs", "paper")
live  = scan("live/logs", "live")

pkeys = {(r["date"], r["symbol"], r["side"]) for r in paper}
live_only = [r for r in live if (r["date"], r["symbol"], r["side"]) not in pkeys]

rows = sorted(paper + live_only, key=lambda r: (r["date"], r["reject_time"]))

out = "state/rejected_breakouts_backfill.csv"
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["date","symbol","side","reject_time","source"])
    w.writeheader()
    w.writerows(rows)

print(f"paper rejections      {len(paper)}")
print(f"live rejections       {len(live)}  ({len(live)-len(live_only)} duplicate of paper)")
print(f"live-only kept        {len(live_only)}")
print(f"UNIQUE EVENTS written {len(rows)}  -> {out}")
days = sorted({r['date'] for r in rows})
print(f"sessions              {len(days)}   {days[0]} .. {days[-1]}")
longs = sum(1 for r in rows if r['side']=='long')
print(f"sides                 long {longs} / short {len(rows)-longs}")
