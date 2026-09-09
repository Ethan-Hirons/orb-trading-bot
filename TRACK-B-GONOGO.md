# Track B (qqq_orb.py) — holdout criteria, LOCKED 2026-09-09

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
