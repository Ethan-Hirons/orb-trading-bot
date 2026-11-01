# PREREG: does E16's mechanism predict where the noise-area rule works? (2026-11-01)

**Written and committed before any price data for these ETFs was fetched.** Only fund
assets and average volumes (below, with sources) were looked at. Results are appended below
the line at the end, and nothing above it changes after the run.

## Why

E16's mechanism (PREREG-QQQ-NOISE-PAPER-2026-10-27.md) is this:
- Leveraged and inverse ETFs must rebalance at the end of each day in the direction of the
  day's move.
- The flow is roughly AUM × (L² − L) × the day's return.
- So where that complex is large relative to the underlying's trading, big intraday moves
  should keep going.

QQQ can't confirm this because it was picked after looking. ETFs the rule has never been run
on can. **Nothing here changes E16's rules or schedule.**

## The predictor, fixed now

`R = Σ over the complex of AUM × (L² − L)`, divided by the traded ETF's average daily dollar
volume. With L² − L = 6 for 3x, 12 for −3x, 2 for 2x, 6 for −2x and 2 for −1x, R is the
end-of-day rebalancing flow as a multiple of daily volume, per 1.0 of daily return. For
example, R = 10 and a 1% day means flow ≈ 10% of a day's volume.

**What counts as the complex:** ProShares and Direxion funds that track the same broad index
or the same sector as the ETF. Sub-index funds are left out: biotech, regional banks,
"top 5", high beta, equal weight, mega-cap and single-stock funds.

| ETF | complex (leverage) | complex AUM | Σ AUM×(L²−L) | ADV $ | **R** | role |
|---|---|---|---|---|---|---|
| **SMH** | SOXL 3, SOXS −3, USD 2, SSG −2 | $30.84B | $198.8B | $3.91B | **50.8** | treatment |
| **XLK** | TECL 3, TECS −3, ROM 2, REW −2 | $7.30B | $39.1B | $1.37B | **28.6** | treatment |
| QQQ | TQQQ 3, SQQQ −3, QLD 2, QID −2, PSQ −1 | $53.81B | $273.8B | $24.25B | 11.3 | E15, seen |
| **XLF** | FAS 3, FAZ −3, UYG 2, SKF −2, SEF −1 | $3.07B | $15.7B | $1.84B | **8.53** | treatment |
| DIA | UDOW 3, SDOW −3, DDM 2, DXD −2, DOG −1 | $1.69B | $8.5B | $1.72B | 4.92 | E15, seen |
| SPY | UPRO 3, SPXU −3, SPXL 3, SPXS −3, SSO 2, SDS −2, SPUU 2, SH −1, SPDN −1 | $23.98B | $107.0B | $31.33B | 3.41 | E15, seen |
| IWM | TNA 3, TZA −3, URTY 3, SRTY −3, UWM 2, TWM −2, RWM −1 | $2.39B | $14.3B | $6.03B | 2.38 | E15, seen |
| **XLV** | CURE 3, RXL 2, RXD −2 | $0.31B | $1.44B | $1.59B | **0.90** | control |
| **XLU** | UTSL 3, UPW 2, SDP −2 | $0.08B | $0.45B | $1.13B | **0.40** | control |
| **XLP** | UGE 2, SZK −2 | $0.01B | $0.03B | $1.03B | **0.03** | control |

**Fund-level AUM** ($B, 10-30 close): SOXL 24.31, SOXS 3.97, USD 2.52, SSG 0.0387 · TECL 5.95,
TECS 0.0725, ROM 1.27, REW 0.0039 · FAS 2.17, FAZ 0.0863, UYG 0.787, SKF 0.0097, SEF 0.0139 ·
TQQQ 36.79, SQQQ 1.79, QLD 14.30, QID 0.286, PSQ 0.643 · UDOW 0.870, SDOW 0.146, DDM 0.519,
DXD 0.0408, DOG 0.110 · UPRO 5.51, SPXU 0.419, SPXL 7.05, SPXS 0.313, SSO 8.77, SDS 0.370,
SPUU 0.300, SH 0.953, SPDN 0.291 · TNA 1.40, TZA 0.190, URTY 0.293, SRTY 0.0674, UWM 0.224,
TWM 0.0499, RWM 0.166 · CURE 0.199, RXL 0.113, RXD 0.0030 · UTSL 0.0694, UPW 0.0127,
SDP 0.0020 · UGE 0.0111, SZK 0.0007.

**ADV $** = Yahoo Finance "Avg. Volume" (its 3-month average) × the 2026-10-30 close:
SMH 7,483,179 × 522.49; XLK 7,636,489 × 179.51; XLF 32,599,378 × 56.51; XLU 26,023,450 ×
43.43; XLP 11,498,104 × 89.42; XLV 8,586,360 × 184.87; QQQ 35,113,323 × 690.49; SPY
41,462,520 × 755.72; DIA 3,332,318 × 517.13; IWM 21,603,453 × 279.33.

**Sources, retrieved 2026-11-01** (figures as of the 2026-10-30 close):
- AUM: <https://stockanalysis.com/etf/provider/proshares/>,
  <https://stockanalysis.com/etf/provider/direxion/>, and <https://stockanalysis.com/etf/soxl/>,
  which confirms "Oct 30, 2026".
- Volume: `https://finance.yahoo.com/quote/<TICKER>/` for each of the ten ETFs.

**Groups.**
- **Treatment:** SMH, XLK, XLF. These are the three named in the task, and their R (8.5–51)
  is in the same range as QQQ's 11.3 or higher.
- **Controls:** XLV, XLU, XLP, all with R < 1, so their complexes are 10× to 1,500× smaller
  relative to trading.
- **No other ETFs were added.** The only other sizeable complexes the AUM pages showed are
  biotech (LABU/LABD, about $0.75B) and regional banks (DPST, about $0.33B). Both are
  sub-sector funds and would sit in the middle (R ≈ 2–5), so they add nothing to either group.

## The rule: exactly E16's, no tuning

`index_scan.h3_rows` / `_h3_day` as frozen for E16, run per ETF with no per-ETF changes:
- σ over the previous 14 full sessions; bands `max/min(Open, prevClose) × (1 ± σ)`;
  checkpoints 10:00…15:30; enter or flip on a breach; flat at the close.
- No VWAP stop. Same half-day calendar and feed-gap handling.
- Cost: **0.5 bp per side scored**, 2 bp per side as a stress run.
- Metric: net return per **active** session (a session with at least one position).
- Data: Alpaca SIP 1-min bars, `adjustment=all`, regular hours, as in E15.

**Periods:**
- **Discovery:** 2016-01-04 → 2021-12-31.
- **Holdout:** 2022-01-03 → the latest complete session available.
- **Post-publication:** 2024-05-10 → latest, descriptive.

All ten ETFs are rerun over the same window. QQQ, SPY, DIA and IWM therefore get a few more
holdout days than in E15.

## Pass criteria, fixed now (all on the holdout, at 0.5 bp)

- **(a) Treatment works:** the pooled treatment group (SMH, XLK, XLF) has a holdout net mean
  per active session **> 0 with a one-sided bootstrap p < 0.05**.
  - The bootstrap resamples **trading days**, keeping all of that day's treatment sessions
    together (the three ETFs move together). 10,000 resamples, seed 7.
  - p = the share of resampled means ≤ 0.
- **(b) Treatment beats control:** pooled treatment holdout mean − pooled control holdout mean
  **> 0**. The CI of the difference is reported but not scored.
- **(c) Ranking:** the Spearman rank correlation between R and the holdout mean across all ten
  ETFs, QQQ, SPY, DIA and IWM included, is **> 0**.

**Verdict:** **SUPPORTED** if (a), (b) and (c) all pass; **PARTIAL** if (a) passes and (b) or
(c) fails; **NOT SUPPORTED** if (a) fails.

## What each outcome means for E16 (decided now)

| outcome | meaning | effect on E16 |
|---|---|---|
| **SUPPORTED** | The rule works where leveraged flow is large and not where it's small, on ETFs never used to choose it. The QQQ result is more likely real, and the mechanism has independent support. | None to the rules or schedule. Criterion 5 of the promotion gate (a mechanism) gets independent evidence. A treatment-basket forward test could be a separate later PREREG. |
| **PARTIAL** | The rule makes money on these ETFs, but the size of the leveraged complex doesn't explain where. Something else (sector trend, tech momentum) is doing the work. | None to the rules or schedule. The leveraged-ETF mechanism in E16's PREREG is marked **not supported**, and a future E16 PASS needs a different criterion-5 argument. |
| **NOT SUPPORTED** | The rule doesn't hold up on unseen high-flow ETFs. The QQQ lead looks more like a selection artefact. | None to the rules or schedule: E16 runs to its own gate. The prior that E16 passes drops, and no other ETFs get added on this mechanism. |

## Weaknesses, stated before the data

1. **R is a 2026 snapshot.** The holdout covers 2022–2026, and complexes such as SOXL grew a
   lot over that time. Only ranks and a ≥ 9× group split are used, and no historical AUM was
   available for free.
2. **The denominator is the ETF's own volume, as the task specified.** Rebalancing is hedged
   largely in the constituent stocks and swaps. Measured against constituent volume, sector
   ETFs (SMH especially) would score lower relative to QQQ and SPY.
3. **Four of the ten ETFs already have E15 holdout results** (QQQ +11.8, SPY +3.5, DIA −2.6,
   IWM −8.8 bp). They're in (c) because the task says so. Their R values came only from the
   AUM and volume above. Only (a) and (b) are clean, and the clean part of (c) is the six new
   ETFs.
4. **Costs are understated for the cheaper ETFs.** Low-priced ETFs have wider relative spreads:
   XLF at $57 and XLU at $43 are about 1–2 bp per half-spread, so 0.5 bp is too low for them.
   The 2 bp stress run is reported next to every number.
5. **(c) is weak.** A Spearman sign with n = 10 is weak evidence, so it's one of three
   conditions, not the verdict.

## Run discipline

- `index_scan.py` gets extra symbols and a `mechanism` report. The E15 and E16 code paths and
  their outputs stay byte-identical.
- Fetch once, run once. Anything not listed above is labelled **post-hoc** in the results.

---

## Results

*(appended after the run)*
