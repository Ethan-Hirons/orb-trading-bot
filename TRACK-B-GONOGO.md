# Track B (qqq_orb.py) — holdout criteria, LOCKED 2026-09-09

---

# ⛔ RESULT — 2026-09-12: **NO-GO. 4 of 5 criteria FAILED.**

Holdout run once, as specified. Criteria below were NOT modified.

```
trades,win_rate,expectancy,expectancy_r,total_pnl,final_equity,return_pct,max_dd,max_dd_pct,sharpe
130,33.8,-2.68,-0.184,-348.55,1701.45,-17.0,542.58,24.65,-1.86
```

| # | criterion | required | actual | verdict |
|---|---|---|---|---|
| 1 | expR > 0 | > 0 | **−0.184** | **FAIL** |
| 2 | expR ≥ +0.045 | ≥ +0.045 | **−0.184** | **FAIL** |
| 3 | TQQQ and SQQQ both positive | both > 0 | impossible, total negative | **FAIL** |
| 4 | maxDD < 30% | < 30% | 24.65% | pass |
| 5 | positive after dropping top 2 | > 0 | negative before dropping any | **FAIL** |

**In-sample → out-of-sample, the sign reversed:**

| | tuning (ex-2026) | holdout |
|---|---|---|
| expR | +0.091 | **−0.184** |
| return | +92.2% | **−17.0%** |
| Sharpe | 1.42 (2bps) / 0.92 (5bps) | **−1.86** |
| win rate | 33.7% | 33.8% |

Win rate held almost exactly; the payoff structure did not. n = 130 matched the
pre-registered estimate, so this is not a small-sample artifact relative to what
was planned.

**Per the locked rule: no paper trading, no further tuning of this variant, and
the holdout is NOT re-run with different parameters.** Track B is closed.

This is the **third** in-sample result in this project to reverse sign out-of-
sample (Run 1 entry-window, Run 5 combined levers, now Track B). The pattern is
now the most reliable finding the project has produced.

---


Written **before** the holdout is run, deliberately. Run 6 produced an
unarguable 0/6 verdict precisely because there was no room left to re-cut the
data once the numbers were in. Same discipline here.

**The holdout window is 2026-02-15 → 2026-08-21 and it is run ONCE.**

```
python -u qqq_orb.py --start 2026-02-15 --end 2026-08-21 --or-minutes 15 --target-r off --slippage-bps 5 | Tee-Object -FilePath qqq_holdout_5bps.txt
Copy-Item qqq_trades.csv qqq_trades_holdout_5bps.csv
```

---

## What the tuning window actually established (B1, 2026-09-09)

All three pre-committed checks PASSED. Details in `qqq_b1_2bps.txt`,
`qqq_b1_5bps.txt` and the per-trade CSVs.

- **Concentration: pass, comfortably.** Drop top 2 → +1274.65 (+62.2%).
  Top 2 = 6.8% of gross profit.
- **Slippage: pass on sign, at the cost of half the edge.** 2→5 bps:
  +1890.73 → +1007.35, Sharpe 1.42 → 0.92, maxDD 15.3% → 21.2%.
- **Long/short split: pass, and it refutes the long-bias hypothesis.**
  SQQQ is the better half at both levels (+1234 vs TQQQ +656 at 2 bps;
  +774 vs +233 at 5 bps).

**But in R, stripped of compounding, the result is much weaker:**

| year | n | expR @5bps |
|---|---|---|
| 2024 | 249 | +0.165 |
| 2025 | 249 | **+0.016** |
| 2026 (6 wks) | 30 | +0.451 |
| ex-2026 | 498 | **+0.091** |

Overall t: 1.96 @2bps, **1.16 @5bps**. 2025 had no edge. The in-sample case
rests on 2024 plus a 30-trade stub adjacent to this holdout.

**Honest prior going in: ~+0.1R/trade, not distinguishable from zero.**

---

## READ THIS BEFORE LOOKING AT THE RESULT

Expected n ≈ 130. R-standard-deviation ≈ 2.3, so the standard error is
≈ 0.20R. Demanding t > 1.5 would require expR > 0.30 — higher than any
in-sample year except the 30-trade stub.

**A significance test on this holdout is not achievable.** It tests SIGN and
CONSISTENCY, nothing stronger. Passing does not mean "has an edge"; it means
"not yet refuted, worth paper-trading". This paragraph exists so that neither
of us moves the bar after seeing the number.

---

## Criteria — all judged at 5 bps, all must pass

1. **expR > 0** overall.
2. **expR ≥ +0.045** (half the ex-2026 in-sample figure).
3. **Both TQQQ and SQQQ positive in R.** A one-sided result means it was a
   directional bet on the period, not a strategy.
4. **maxDD < 30%.**
5. **Still positive after dropping the top 2 trades.**

**Fail any one → NO-GO.** No paper trading, no further tuning of this
variant. Do not re-run the holdout with different parameters; that converts it
into a second tuning window and it stops being evidence.

**Pass all five → PAPER ONLY**, on a **second Alpaca paper account** (ORB's
`_flatten_all_verified` and `_close_overnight_leftovers` close ALL positions
unfiltered and would flatten Track B's trades). 3–4 weeks, ~15–20 trades, as a
MECHANICS check: fills, whole-share sizing on a small balance, the EOD exit
firing 5 min before the close, nothing carried overnight. It is not proof of
edge and does not by itself justify funding.

## Not covered by this document

- OR 15 is **not** the paper's strategy (9:45 entry off a 15-min range vs the
  paper's 9:35 off a 5-min range), so it does not inherit Zarattini/Aziz's
  evidence. This is an original finding needing its own out-of-sample proof —
  which is what this holdout is.
- Live slippage on 3x leveraged ETFs at a 9:45 market order is unmeasured.
  5 bps is an assumption, not a measurement.
