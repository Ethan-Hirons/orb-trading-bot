# PRE-REGISTRATION — midday ORB (the "second strategy" question)

Written 2026-10-22, **before** the test was run. Criteria are not edited
afterwards; a RESULT section is appended. Same discipline as
`PREREG-E8-2026-10-22.md` and `TRACK-B-GONOGO.md`.

## The question

The bot cannot enter after 10:30 (`entry_window_minutes: 60`). Does the ORB
*mechanism* work in a second window later in the day?

Feasibility screen (213 cached symbol-days, SIP) already passed: midday
12:00-15:30 median range 3.355% vs the morning's 4.446%, and 97.7% of
symbol-days offer a perfect-foresight move >= 3x round-trip cost. That screen
could only have killed the idea. It cannot support it — the morning window
passes it at 95.8% and still loses money.

## ONE configuration. No grid.

Locked in advance, mirroring the live config rather than searching:

- midday range = **12:00-12:15** (mirrors `opening_range_minutes: 15`)
- entry window = **12:15-13:15** (mirrors `entry_window_minutes: 60`)
- entry = **close of the first bar that CLOSES beyond the range**
  (mirrors `confirm_bar_close: true`)
- stop 1.5%, trail 1.25% with the real arming rule, EOD flatten 15:50
- entry slippage 0.046% applied adversely

**No other window, range length or entry rule may be tried.** Runs 1-6 swept
96 and 192 combos and nothing survived out-of-sample; the defence is to test
one honest configuration, not to search.

**Entry is at the bar close, NOT at the range level.** E8 established that
level-entry is unobtainable: actual fills ran a mean +1.31% past the level,
worst +38.72%. That mistake is not repeated here.

## Universe and its defect

The 213 cached symbol-days — the names the morning screener picked, on the
days it picked them. **These were selected for morning gaps, not midday
setups.** A real midday strategy would screen at noon. This tests the
mechanism on a borrowed universe and nothing more.

## Comparator

The morning ORB run through the **identical pipeline** on the **same
symbol-days** (range 09:30-09:45, entry 09:45-10:30, same entry rule, same
exits, same costs). Without this control a midday number means nothing.

## Pass requires ALL of

1. midday mean % per trade **positive**
2. sign survives **drop-top-2**
3. sign holds in **both halves** split by date
4. **both feeds agree** (IEX and SIP) — E8 failed exactly here

## What each outcome authorises

| outcome | authorises |
|---|---|
| passes all four | a proper sim study with its own untouched holdout window. **Not** a second Alpaca account, **not** paper trading, **not** a line of code in `orb_bot/`. |
| fails any | closed. **No re-running with a different window, range length or entry rule.** |

Nothing here authorises a live or paper deployment under any outcome. The
promotion bar from 2026-09-27 stands: ideas are tested on paper only after
they survive sim, and promoted to live only after they work on paper.

## Stated in advance

- The honest prior is failure. The reference class is Run 1, Run 5 and Track
  B — three in-sample results that reversed sign out-of-sample, 0 for 3.
- A positive result on a borrowed universe is weaker than it will look.
- n is unknown before the run and may be too small to read.

---

# RESULT — 2026-10-22

Criteria above untouched. One configuration, as locked. No grid was run.

## VERDICT: FAIL on criterion 1. Closed. Nothing authorised.

| feed | midday mean | win% | ex-top2 | morning control | midday − morning |
|---|---|---|---|---|---|
| IEX | **−0.099%** (n=167) | 49.1% | −0.168% | +0.029% (n=168) | −0.127 pp |
| SIP | **−0.052%** (n=200) | 52.0% | −0.100% | −0.097% (n=185) | +0.045 pp |

Criterion 1 required a positive midday mean. Negative on both feeds, so
criteria 2-4 were not reached. **No re-running with a different window, range
length or entry rule.**

## The window was never the problem

The morning control, through the identical pipeline on the same symbol-days,
is also flat to negative (+0.029% / −0.097%). Moving the clock changes
nothing because the clock was not what was broken.

## What the failure actually shows — and it is worth more than the test

Exit mix, midday, both feeds in close agreement:

| exit | n (IEX/SIP) | mean (IEX/SIP) |
|---|---|---|
| stop | 58 / 70 | −1.500% / −1.500% |
| trail | 45 / 64 | +1.250% / +1.196% |
| eod | 64 / 66 | +0.223% / +0.275% |

**The win rate is 49-52% — better than the live book's 41-44%.** Selection in
this window is fine. It still loses, because the trail caps a winner at
≈ +1.25% while the stop takes −1.50%.

> Payoff ratio ≈ **0.83**. On a trail/stop pairing alone, breakeven needs a
> **54.5%** win rate. The strategy clears 52.0% at best.

This is the third independent route to the same conclusion. `exit-structure`
reached it on the 100-trade live book: *"Selection is not the gap. Payoff
structure is."* Here it reappears on 200 simulated trades, a different window,
a different entry rule, and a sample the original finding never touched.

A second strategy cannot fix this, because the exit stack travels with it.
Any new entry rule bolted onto this exit structure inherits a payoff ratio
below 1 and needs a win rate the system has never produced.

## What this closes and what it points at

Closed: the midday window, and with it the "bolt on a second strategy"
direction in general. The constraint is not which hours the bot trades.

Points at: the exit side, where the evidence has pointed since 2026-09-29,
and where there is now — as of the E8 gate — a replay harness validated at
0.09 pp median error against trades whose outcome is known.
