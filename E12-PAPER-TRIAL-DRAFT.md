# E12 paper trial — DRAFT for Ethan's approval (2026-10-26)

**Status: NOT applied. Nothing deployed. `config.yaml` / `config-live.yaml` untouched.**

## 1. E12 grade — passes its five criteria, with one serious caveat

The holdout reached 30 trades on 10-15 and was replayed then
(`robust_fixed_holdout.txt`, harness check: touch diff +2.35 on 29 of 30; WFF
not replayable and excluded, not filled in). `stop 1.0 + trail` vs live:

| criterion (written 10-01) | result | |
|---|---|---|
| 1. beats live, touch | +51.93 | ✅ |
| 2. beats live, close | +51.93 | ✅ |
| 3. survives ex-top2 / ex-top3 | +51.93 / +52.32 (touch), +51.93 / +51.93 (close) | ✅ |
| 4. ≥ +0.25 per trade | ≈ +1.79 per trade (51.93 / 29) | ✅ |
| 5. no sign flip across halves | +23.10 / +28.83 | ✅ |

The gain is the same with and without the best trades, i.e. it comes from
smaller losses, which is the mechanism E12 was opened on.

**Caveat (ANALYSIS-2026-10-25 §4, found after the holdout was graded):**
`replay.py` uses 1-minute bars and fills stops exactly at the level, which
flatters every rule tighter than the current one — stop, trail and breakeven
grids all put their optimum at the smallest value tested. A 1.0% stop is
inside that zone. So the +51.93 is biased upward by an unknown amount, and
the replay **cannot** say how much. The pass is real under the rules as
written, but the harness that produced it is not trustworthy at 1.0%.

**Why that still points to a paper trial, not away from it:** the 10-01 rule
says a pass earns a paper trial with its own 50-trade sample. A paper trial
is the one test with no bar-resolution problem — real ticks, real stop
fills, real slippage. It is the right instrument for exactly the question the
replay cannot answer.

## 2. E3 — closes

37 overlapping paper/live trades. Median live − paper **+0.04pp**, mean
−0.07pp (WFF). The 10-26 entry already showed the instances enter minutes
apart, so E3 cannot isolate fill quality anyway. Nothing more to learn by
keeping paper == live; paper is free for the trial.

## 3. The change (paper only)

`config.yaml`, line 173:

```diff
-  stop_pct: 1.5
+  stop_pct: 1.0   # E12 paper trial from <start date>; live pinned at 1.5 in config-live.yaml
```

`config-live.yaml` — **required in the same commit.** The live checkout
`git pull`s the same `config.yaml`, and `config-live.yaml` only overrides the
keys it lists. Without this, live would silently pick up stop 1.0:

```diff
+exits:
+  # E12 paper trial runs stop_pct 1.0 in config.yaml. Live stays at 1.5 until
+  # E12 clears the promotion gate in EXPERIMENTS.md. Deliberate, temporary
+  # exception to THE RULE above; remove this block on promotion or rejection.
+  stop_pct: 1.5
```

Position size does not change: the $700 (paper) / $450 (live) notional cap
binds at both stop widths, so a stop-out costs ~$7 instead of ~$10.50.

**Do not combine with the `_reanchor_exits` fix** (ANALYSIS-2026-10-25 §2).
One change at a time.

**After deploy, verify:** the first live log line `ENTER ...` shows
`SL=...(-1.5%)`, and the first paper one `(-1.0%)`.

## 4. Pre-registered criteria — fixed now, before the first trial trade

Baseline = current regime (paper, 2026-08-19 onward, 94 trades — the trail's
working start, chosen mechanically, same cutoff `replay.py` uses):
expectancy **−0.06/trade**, ex-top2 **−2.42/trade**, max DD **143.98**,
avg win **+14.15**, avg loss **−9.28**.

Over the first **50 paper trades** on stop 1.0, with no other config change:

1. Expectancy **≥ +0.19/trade** (baseline + 0.25).
2. Ex-top2 expectancy **≥ −2.17/trade** (baseline + 0.25) — the improvement
   must survive without the outliers.
3. Max drawdown **≤ 143.98**.
4. Mechanism: avg loss **smaller** than −9.28 AND avg win **≥ 12.03**
   (no more than 15% below +14.15). If winners shrink, the stop is cutting
   trades that would have run, and the gain is not what E12 claimed.

Miss any one → revert paper to 1.5, no re-tuning. Pass all four → the
promotion gate in EXPERIMENTS.md (incl. criterion 5, mechanism restated
without reference to the backtest) before anything touches live.

At ~2 paper trades per session this takes ~5–6 weeks.
