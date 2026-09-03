# Validation Week Go/No-Go Criteria

_Locked 2026-07-29, before the Aug 1–2 tune and the Aug 3–7 validation week.
The point of writing these down now: the funding decision on Aug 7–8 gets made
against THIS list, not against how the week felt. No edits to this file during
the week._

**Setup being validated:** Saturday-tuned config (trail active, `allow_shorts:
false`, whatever the Aug 1–2 sweep decides on stop/tp/hold/arming), paper
account, Aug 3–7. Live account application + bank transfer start Mon–Tue Aug
3–4 in parallel; money stays unarmed until the decision.

**Expected sample:** long-only cut the trial to ~1 trade/day, so plan on 5–8
completed trades. That is far too few to prove an edge — the week validates
*mechanics*, not profitability. Criteria are weighted accordingly.

## Hard fails — any ONE of these = NO-GO, regardless of P&L

1. **Ops integrity:** an unverified flatten, an overnight carry, an unpaired
   trade in the CSV, or a crash the watchdog/restart layer didn't recover.
2. **Trail malfunction:** `move_stop`/replace-order errors on real bracket
   stops, or a position whose MFE clearly exceeded the arming threshold with
   no TRAIL ratchet line in the log.
3. **Risk containment:** any single trade losing more than ~1.5× its intended
   risk (e.g. worse than −4.5% on a −3% stop — stop-limit slippage blowout),
   or the daily breaker firing due to a malfunction rather than ordinary stops.
4. **Config leak:** any attempted short entry (cash-account mirror broken), or
   any mid-week config/code change (breaks the sample — restart the week).

## Quantitative checks — judged jointly, small-n humility

5. **Sample floor:** ≥5 completed trades. Fewer → automatic EXTEND (another
   paper week), not a judgment either way.
6. **P&L floor:** week net better than −2% of equity. Not "must be
   green" — 5–8 trades is noise — but a floor under "something is off."
7. **Trail does its one job:** ZERO trades that peaked above ~+1.5% MFE and
   still exited at the full initial stop. This is the exact failure mode the
   trail was added to kill (SMCX/SMCL/SNXX 07-23, INTC/DRAM 07-28). One
   occurrence = the trail isn't ratcheting in practice → NO-GO on #2 terms.
8. **Trail slippage sanity:** on trail exits, realized exit within ~0.4% of
   the theoretical `peak × (1 − trail%)` on average. Worse means the 30s poll
   lag makes the effective trail materially looser than the swept value — the
   sweep's config choice no longer applies → EXTEND and reconsider (server-
   side trailing orders become the fix, not a tighter number).

## Decision table (Aug 7 evening / Aug 8)

- **GO:** no hard fails + #5–8 pass → fund the first tranche (⅓–½ of the
  the planned live size), `allow_shorts: false`, live keys in `.env`, first armed session
  Mon Aug 10. Scale-up only after ~30 live trades hold up.
- **EXTEND:** no hard fails, but <5 trades or #6/#8 marginal → one more paper
  week on the same config, money stays parked. Costs nothing but time.
- **NO-GO:** any hard fail → fix, re-validate a full week. No partial credit.

