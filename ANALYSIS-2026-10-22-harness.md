# Why the gate failed: a resolution limit, not a bug

Follows `PREREG-STOP10-2026-10-22.md`. Nothing deployed. No harness code
changed — deliberately, see the last section.

## Two separate error sources, not one

All four gate cells showed **positive** mean error (+0.098 to +0.193): the
harness systematically flatters outcomes. Decomposed:

### 1. Missed trail exits — a hard resolution limit

`AXTX 2026-10-20`. Real bot: armed at +1.76%, peak **+4.78%**, `TRAIL EXIT`
10:26:05 filled 17.09 = **+3.51%**. The trail did exactly what it should.

| bar | low | peak | trail level | gap |
|---|---|---|---|---|
| 10:25 | 17.090 | 17.300 | 17.084 | +0.006 |
| **10:26** | **17.100** | **17.310** | **17.094** | **+0.006** |

The trail sat **$0.006** below the printed bar low. Reality filled inside
that minute; a 1-minute bar cannot show it. The sim rode on to a peak of
17.88 and exited 17.657 = **+6.94%**. **Half a cent swung the trade by
3.4 pp.**

**Scope: 23 of 48 holdout trades** have a trail level come within 0.1% of a
bar extreme at some point — on both feeds. Roughly **half the sample sits in
the unresolvable zone.**

### 2. Stop fills past the level — already measured, and fixable

BSP +0.293, VEEA +0.227, SOXS +0.183, SKDD +0.156 — all stop-outs booked
between −1.66% and −1.79% while the harness assumes exactly −1.500%.

This is **E11**, measured independently before today: *"the real stop is
1.58%, not 1.50%."* A systematic ≈ +0.08 pp optimism on every stop-out, and
roughly half the book stops out.

## What this does to the original result

The 2026-10-01 finding — stop 1.0 surviving ex-top2, ex-top3 and both halves
— was computed on **this same harness**. A tighter stop converts trail exits
into stop exits, which is precisely the comparison this defect distorts.

**The in-sample support for stop 1.0 is weaker than it looked.** Not refuted;
measured with an instrument that is unreliable on half the relevant trades.

## What I did NOT do, and why

- **Did not pin replays to the logged `TRAIL EXIT` timestamps.** That scores
  the answer against itself. Already flagged on 10-01: *"DO NOT return t.exit
  here ... gives a free perfect match"* — it turned 33% real agreement into a
  reported 66%.
- **Did not add an epsilon tolerance** to the trail trigger. Also already
  ruled: *"DO NOT tune assumptions until validation passes — that fits the
  harness to the known answer."*
- **Did not apply the E11 1.58% stop correction and re-run.** It is
  legitimate and independently measured, but applying it after seeing a
  failed gate and a favourable partial is post-hoc. It belongs in a fresh
  pre-registration, declared up front.

## The only fix that actually works

**Sub-minute data.** Alpaca's historical trades endpoint resolves exactly
when a trail or stop level was touched. It fixes **both** arms, not just the
gate: ticks say when a 1.0% stop would have been hit as readily as a 1.5%
one, so it is not a gate-gaming move.

Scope is bounded — only the ~23 ambiguous trades need tick data, not all 48.

Until then: **the stop-1.0 question cannot be settled from bars.** The
alternative route is forward paper data with the config actually changed,
which costs weeks instead of a fetch.
