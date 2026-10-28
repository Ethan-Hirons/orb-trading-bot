# pipeline/ — ops and research tooling (built 2026-10-25)

**Design rule, and the reason the pipeline stops where it does: nothing here
may influence a trade decision.** An agent or model in the decision path
cannot be backtested — anything reading news or prices for a past date
already knows what happened next, and the resulting lookahead is invisible in
the output. So this is research and operations only. See ANALYSIS-2026-10-25 §4
and the 2026-09-29 note on LLM agents.

## Layout

    pipeline/__init__.py   repo paths
    pipeline/store.py      unified on-disk cache (gzipped JSON, no pickle)
    pipeline/sources.py    Alpaca adapters -> plain dicts, replay.py's schema
    pipeline/health.py     ops checks
    pipeline/snapshot.py   session log -> structured data

    ops_report.py          run the health checks
    prefetch.py            bulk-fetch market data into the store
    dashboard.py           render dashboard.html

    tests/test_pipeline.py   22 checks, no pytest needed
    tests/test_dashboard.js  headless render of every session (needs node)

## 1. Ops report

    python ops_report.py              # exit 1 on any FAIL
    python ops_report.py --json
    python ops_report.py --root live  # the other instance
    python ops_report.py --notify     # push failures via deploy/notify.sh

Complements `invariants.py` rather than replacing it. invariants validates
sessions it can see; this checks the seams BETWEEN artefacts, which is where
this project's failures have actually lived. Every check came from a real
incident:

| check | incident |
|---|---|
| `entered_vs_booked` | 8 trades entered, never booked (day-end-only writes) |
| `stale_xlsx` | `live/trade_log.xlsx` sat 11 days behind the CSV |
| `frozen_equity` | 2026-10-23, equity flat to the cent for 45+ min |
| `reanchor_rate` | most stops keep a ref-anchored level, not the fill |
| `session_integrity` | 08-26/08-27 died mid-day, no `Day done` |
| `features_header` | header predates v1.12's extra field |
| `heartbeat_age` | watchdog silent since 2026-09-21 |

`session_integrity` distinguishes a session that was **killed** from a log
**copied** mid-session (mtime sitting right after its last line) — otherwise
every synced-down log from the VPS reads as a crash.

Only FAIL sets the exit code. Warnings that cry wolf get ignored, and this
project has already lost sessions to ignored silence.

Suggested systemd timer on the VPS: weekdays ~16:20 ET, after the EOD summary.

## 2. Prefetch

    python prefetch.py --migrate                 # import .replay_cache, free
    python prefetch.py --coverage --from-targets # report, fetch nothing
    python prefetch.py --from-targets --days 90
    python prefetch.py --symbols SOXL,INTC --start 2026-01-01 --end 2026-10-24

The point is **sample size**. 150 booked trades cannot validate anything; the
way out is simulating sessions, and that needs bars on disk.

Coverage as of 2026-10-25, after migrating 426 sessions from `.replay_cache`
with zero network calls:

    from-trades    149 symbol-days,  71 symbols — 100% cached
    from-targets   626 symbol-days, 288 symbols —  34% cached

**`--from-targets` is the valuable one.** It covers every name the screener
put on the board, not just the ones entered — 4.2x more data than the trade
history, from the same logs, no new data source. That is the only way to
simulate decisions the bot did NOT make, and it is the population E8 needs.

Fetching is incremental and resumable; anything cached is skipped. Use
`--limit` for a cautious first run.

## 3. Dashboard

    python dashboard.py --limit 30        # -> dashboard.html

One self-contained HTML file: no CDN, no network, opens over SSH or offline.
Per session: status chips, KPI row, equity sparkline, every screened target
with gap/sentiment/relvol/score and its outcome (entered, broke out but not
confirmed, or nothing), booked trades, and a merged timeline of entries,
fills, trail events, news and vetoes.

Note: the bot logs only the FIRST unconfirmed breakout per symbol ("further
waits logged silently"), so the dashboard's rejected count is the
log-visible population, not E8's full count from `e8_backfill.py`.

## Tests

    python tests/test_pipeline.py     # 22 checks
    node tests/test_dashboard.js      # renders all sessions headless

Both run without pytest, which is not installed on the bot's interpreter.
