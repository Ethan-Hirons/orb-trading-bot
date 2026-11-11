# ANALYSIS: E19 survey of published daily strategies, 2026-11-11

Pre-registered in `PREREG-SURVEY-2026-11-11.md` (commit `3269bee`, before any data was fetched).
Code: `survey_data.py`, `survey.py`. Outputs: `survey_out.txt`, `survey_posthoc.txt`,
`survey_results.json`, and the workbook **`E19-strategy-survey.xlsx`**.

## Bottom line

- **One of seven tests passed: a trend-following rule on Bitcoin (C5).** It is the candidate
  for the next paper bot, and the runner for it is written and tested (`btc_bot.py`).
- **That pass is weaker than it looks.** Almost all of its edge came before mid-2021. Over the
  last five and a half years it made 5.8% a year with a Sharpe of 0.43, and it has lost a
  little since its source paper came out in 2025.
- **Every stock and ETF rule failed the timing test.** Several made money, but no more than
  holding the same ETF part of the time would have. Buying dips in large-cap stocks lost to
  the index outright.
- **No public daily strategy found here can be proven on paper in a few months.** With edges
  this size it takes years. The BTC bot's forward test is designed around that, and it says
  so in its own pre-registration (`PREREG-BTC-TREND-PAPER-2026-11-11.md`, E20).

Against the ORB bot (about zero before costs, slightly negative after), C5's backtest is far
better. Whether it stays better on new data is what E20 is for.

## What was surveyed

Sources read: the Concretum/Zarattini papers (15 listed, 2024–2026), Quantpedia, Alpha
Architect and CXO Advisory summaries, the Quantitativo and Quantified Strategies write-ups of
mean-reversion rules, QuantConnect replications, the r/LETFs and r/algotrading communities,
and GitHub and TradingView bots. The `Research` sheet of the workbook lists each family, its
evidence and what was done with it.

Three things stood out before any backtest:

1. **No public bot comes with a verified live record.** Repositories describe features; the
   backtests are self-reported. A study of 888 public strategies found backtest Sharpe
   explained 1–2% of out-of-sample results.
2. **Most well-known rules have no clean test after 2020.** The write-ups run one backtest from
   1993 or 2000 to today, which hides decay.
3. **This project had already closed four families** (ORB entries in E14, intraday index
   anomalies in E15, monthly trend in E18, and the E16 rule is forward-only). They were not
   re-tested.

That left six families worth a test, frozen at their sources' parameters:

| # | Rule | Source | Trades |
|---|---|---|---|
| C1a, C1b | RSI(2) pullback | Connors & Alvarez 2008 | SPY, QQQ |
| C2 | IBS band reversion | Quantitativo 2024, after Pagonidis 2013 | QQQ |
| C3 | Dual-signal VIX premium | Zarattini, Mele & Aziz 2025 | short or long VIXY |
| C4 | Leveraged 200-day trend | Gayed & Bilello 2016 | SSO or cash |
| C5 | Donchian trend ensemble | Zarattini, Pagani & Barbon 2025 | BTC |
| C6 | RSI(2) pullback, 10 slots | Connors & Alvarez 2008 | top-200 US stocks |

## The bar

Each rule was regressed on its own underlying, because a long-only rule earns part of the
market's return just by being in it. **Alpha** is what is left. A pass needed all five:
alpha t ≥ 2.45 (5% one-sided, corrected for seven tests); alpha positive in both halves;
still positive at stress costs and with fills delayed to the next open; not carried by the
best 5% of trades; and a drawdown no deeper than −25%. Scored 2016-01-01 to 2026-11-10, net
of costs.

## Results

| # | Rule | Alpha / yr | Alpha t | Halves | Sharpe | CAGR | Max DD | In market | Criteria | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| C1a | RSI(2), SPY | +2.9% | 1.82 | +0.8% / +5.0% | 0.74 | 4.7% | −11.3% | 11% | 4 of 5 | FAIL |
| C1b | RSI(2), QQQ | +1.6% | 0.82 | −2.3% / +5.3% | 0.49 | 4.1% | −9.7% | 13% | 3 of 5 | FAIL |
| C2 | IBS band, QQQ | +3.4% | 1.08 | +1.3% / +5.3% | 0.72 | 9.5% | −16.4% | 18% | 4 of 5 | FAIL |
| C3 | VIX premium | +9.8% | 1.45 | +17.3% / +3.3% | 0.66 | 9.8% | −29.7% | 95% | 3 of 5 | FAIL |
| C4 | 200-day trend, SSO | +4.6% | 0.83 | +8.9% / +0.4% | 0.69 | 16.7% | −43.1% | 84% | 3 of 5 | FAIL |
| **C5** | **Donchian, BTC** | **+9.7%** | **2.75** | **+17.8% / +1.4%** | **1.34** | **23.4%** | **−20.2%** | 86% | **5 of 5** | **PASS** |
| C6 | RSI(2), top-200 stocks | −4.3% | −0.82 | −7.6% / −1.0% | 0.25 | 5.1% | −42.0% | 97% | 1 of 5 | FAIL |

For scale, buy and hold over the same period: SPY 14.6% a year (drawdown −33.7%), QQQ 18.8%
(−35.1%), BTC about 60% a year (−83%). CAGR assumes idle cash earns nothing, as on a paper
account. C6 is scored from 2017.

**Data checks:** all series cover the period; the large daily moves listed by
`survey.py check` are real events (VIXY and VIX spikes, BTC's March 2020 drop). The C6
universe is 572 names, 51 of them since delisted (Twitter, Activision, Celgene and others are
in it). Its worst trades are real crashes in real names (Workhorse, Virgin Galactic, AMC,
Riot), not data errors.

**Replication check:** C2 run on 1999 to May 2024 with no costs gives a 14.3% CAGR, 20.0% of
days invested and a −24.6% drawdown. The article reports 14.6%, 20.0% and −24.7%. The rule is
the article's rule.

## Reading the results

**Mean reversion on index ETFs (C1, C2): real before 2016, mostly beta since.** RSI(2) on SPY
had alpha t = 3.8 from 1993 to 2015 and 1.8 since. C2 on QQQ had t = 4.0 before 2016 and 1.1
since. C2 is the most tempting result in the table: positive in all 11 years, 9.5% a year
while invested 18% of the time. It still fails, because it buys on the most volatile days,
when simply holding QQQ also pays the most. The same rule shows nothing on SPY (t = 0.6) or
IWM (t = 0.2), and the article's own tuned variants do worse than its base rule after 2016.
A result that exists on one ETF and falls apart under its author's improvements is not an
edge to build on.

**Dip-buying large-cap stocks (C6): loses to the index.** 4,917 trades, 65% winners, +0.15%
a trade after costs, and a −42% drawdown, because the losers are large: the high-volume
universe is full of meme and momentum names that keep falling. This matches E14, E15 and E17,
which all found continuation, not reversal, in liquid, high-attention names.

**VIX premium (C3): positive, not significant, too deep a drawdown.** +9.8% a year of alpha at
t = 1.45, with a −29.7% drawdown. Slightly negative since its 2025 publication.

**Leveraged 200-day trend (C4): leverage, not skill.** A 16.7% CAGR with a beta of 0.90 and a
−43% drawdown. Alpha t = 0.83. It makes 2.4 trades a year, so paper could never judge it.
E18's verdict is untouched.

**BTC trend (C5): the one pass.** Alpha +9.7% a year at t = 2.75 with a beta of only 0.18. It
held on average 21% of equity in BTC, took a −20% drawdown where BTC took −83%, and survives
doubled costs, a one-day delay and the removal of its ten best days.

## The four reasons to be careful with C5

1. **The edge is fading.** Period by period:

   | Period | C5 return | C5 Sharpe | Alpha / yr | BTC buy and hold |
   |---|---|---|---|---|
   | 2016–2018 | +201% | 1.95 | +20.1% | +769% |
   | 2019–2021 | +137% | 1.80 | +10.8% | +1,137% |
   | 2022–2024 | +51% | 0.94 | +7.2% | +102% |
   | 2025–2026 | −9.6% | −0.53 | −4.5% | −21% |

   Second-half alpha is +1.4% a year at t = 0.35. The pass rests on 2016 to 2021.
2. **The scored period is mostly the paper's own sample.** It was published in April 2025.
   Since then: alpha −3.3% a year. That is 19 months, too short to settle anything, but it
   points the wrong way.
3. **One asset, one long bull market.** BTC rose about 170-fold. There is no second history
   to check against. Post hoc, the same rule on ETH from 2019 is positive but not significant
   (t = 1.47).
4. **It is not only position sizing, which is the good news.** A vol-targeted buy and hold
   with no trend rule scores t = 1.07 and a −43% drawdown. The trend rule adds something
   beyond sizing.

**Two checks that the result is not an artefact of this code or of one setting:**

- **It reproduces the paper.** The paper's own table for the BTC ensemble (2015-01 to 2025-03,
  no costs, 200% cap) reports a 30% CAGR, 17% volatility, Sharpe 1.58, a 19% drawdown, 14%
  alpha and a beta of 0.17. This run (2016-01 to 2026-11, 30 bp costs, 100% cap) gives 23.4%,
  16.3%, 1.34, 20.2%, 9.7% and 0.18. The entry, the ratcheting midpoint stop and the sizing
  were checked against the paper's text after the run, and they match (the paper exits on a
  close at or below the stop, this code on a close below it). Two stated differences:
  no leverage here, and a simpler rebalance band (0.05 of equity, against the paper's 20%
  band on volatility-driven changes only).
- **It is not a knife-edge.** Post hoc, 13 neighbouring settings (volatility target 20–30%,
  band 0.02–0.10, short lookbacks only, long only, without the 5-day or the 360-day model)
  give alpha t between 2.10 and 2.81, and 12 of the 13 clear 2.45. **Every one of them also
  shows the same fade:** second-half alpha between −0.9% and +2.6% a year.

The honest summary: this is a rule that kept a small, steady BTC position through bull runs
and stepped aside in crashes. It passed the bar that was set. Its recent record is ordinary.

## Post hoc (not pre-registered, cannot produce a pass)

Full output in `survey_posthoc.txt` and the `Post hoc` sheet.

- C5 decomposition and the ETH run: above.
- C2 variants and other ETFs: above.
- **Momentum in C6's stock universe.** Since dip-buying lost there, the opposite trade was
  checked: hold the 20 best performers of the last month (Medhat & Schmeling 2022) or of
  months 2–13 (Jegadeesh & Titman 1993). Neither shows alpha against SPY (t = −0.5 and +0.7),
  and both draw down about 50%. **The first run of this check had a look-ahead bug** and showed
  +772% a year. That was caught as impossible, fixed, and re-run before anything was read
  from it. The scored tests were then audited for the same class of bug:
  `tests/test_survey.py` confirms every signal is unchanged when the future is removed.
- Monthly return correlations: C5 is nearly uncorrelated with every equity rule (0.01 to
  0.25) and with SPY (0.22). A BTC bot diversifies the QQQ bot.

## What was built

| File | What it is |
|---|---|
| `survey_data.py`, `survey.py` | Data fetch and the seven backtests. `python survey.py report` reproduces `survey_out.txt`. |
| `survey_xlsx.py`, `E19-strategy-survey.xlsx` | The workbook: summary, criteria, yearly returns, equity curves with charts, 5,258 trades, post hoc, research notes, method. |
| `btc_bot.py` | The E20 paper runner. `plan` and `shadow` are read-only. `run` does one daily rebalance and refuses on a wrong account, foreign positions, stale data or the disarm file. |
| `tests/test_survey.py`, `tests/test_btc_bot.py` | 30 tests, all passing (plus a static check of the deploy files): no look-ahead, simulator arithmetic, the runner's refusals and orders against a fake broker. |
| `deploy/orb-btc.service`, `deploy/orb-btc.timer`, `deploy/setup-btc.sh` | systemd units (one run a day at 00:10 UTC) and a one-command installer modelled on E16's. |
| `PREREG-BTC-TREND-PAPER-2026-11-11.md` | E20: the frozen rule, the shadow record as evidence from 2026-11-12, looks at 180/365/730/1,095 days, and the power statement. |

`python btc_bot.py plan` today: the target is **6.6% of equity in BTC**. BTC is at about
$73,700, down from a $124,750 peak in October 2025, so seven of the nine models are flat.
On a $10,000 account the first order would be a $663 buy. **Expect the paper account to look
quiet until BTC trends again.** That is the rule working, not the bot failing.

**Alpaca's crypto rules, checked against its docs and the live asset record:** market
orders by dollar amount are accepted, time-in-force `gtc`; the minimum BTC order is about $1;
crypto cannot be shorted or bought on margin, which suits a long-only rule; the taker fee is
0.25% at this account size, charged in the asset received and posted at end of day. The docs
do not say whether paper accounts simulate that fee. Stage A will show it, and the shadow
record charges 30 bp per side either way.

No order was placed and no account was touched in any of this. `run` has only been exercised
against the fake broker in the tests.

## What E20 can and cannot tell you

- **Within a month:** Stage A. Does the daily job hold the target weight, and do Alpaca's
  crypto fees and fills fit inside the 30 bp per side the backtest assumed?
- **Within a year or two:** futility. If the shadow's alpha is ≤ 0 at 365 or 730 days, it
  stops.
- **A PASS takes three years and is less likely than not** even if the 2016–2026 edge is
  fully real (about 40–45%). If the truth is the last five years, about 8%.

E16 remains the better evidence machine: many independent intraday bets and a higher
holdout Sharpe.

## Next steps, in order

1. **Decide whether to run E20.** If yes, it needs a new Alpaca paper account (the dashboard
   allows several; name it "E20 BTC"), with crypto trading enabled. It must not share the
   ORB or E16 accounts.
2. **Merge and push.** This work is on the branch `claude/trading-bot-research-backtest-830d83`,
   committed and not pushed.
3. **Install on the VPS** (after the push). `deploy/setup-btc.sh` does it in one command, the
   way `setup-noise.sh` did for E16: it builds `/opt/orb-btc`, stops for you to paste the keys,
   proves the account is not the ORB or E16 one, shows the day's plan, and enables the timer.
   Each block is one command, run as root:

   ```bash
   systemctl is-active orb-bot
   ```
   ```bash
   cd /opt/orb-bot && git pull && chown -R orb:orb /opt/orb-bot
   ```
   ```bash
   bash /opt/orb-bot/deploy/setup-btc.sh
   ```
   The first run stops and asks for the keys. Paste them with
   `sudo -u orb nano /opt/orb-btc/.env`, then run the same command again. To skip days later:
   `sudo -u orb touch /opt/orb-btc/state/BTC_DISARMED`.
   **The script was written from the E16 one and has not been run on the VPS.** It passes
   `bash -n` and the static checks in `tests/test_deploy_scripts.py`; read each step's output
   the first time.
4. **Not recommended:** a paper bot for C2 (IBS on QQQ). It failed the bar, it is specific to
   one ETF, and at 12 trades a year paper could not tell it from luck before 2030.

## Limits

- Daily bars only. Fills are assumed at the close used for the signal; the next-open run
  brackets that, and no result depended on it.
- Yahoo's BTC series is an aggregate. Alpaca's own prices reproduce the rule's target with a
  0.998 correlation on 2022–2026, but the two differ by more than 0.05 on 31 of 1,680 days.
- VIXY borrow cost and availability were assumed, not measured.
- C6's keyword filter drops real companies whose listing name contains "Shares" or "Trust"
  (many ADRs and REITs). That was in the PREREG and was not changed.
- Seven tests were run and the correction is for seven. The post hoc checks add more looks at
  the same data, which is why none of them can produce a pass.
- Nothing here is checked against live fills, and none of it is financial advice.

## Sources

- Zarattini, Pagani & Barbon (2025), *Catching Crypto Trends*: [SSRN 5209907](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907)
- Zarattini, Mele & Aziz (2025), *The Volatility Edge*: [SSRN 5316487](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5316487); replication notes at [QuantConnect](https://www.quantconnect.com/research/21143/harvesting-the-volatility-risk-premium-with-a-dual-vix-signal/p1)
- Quantitativo (2024), *A Mean Reversion Strategy with 2.11 Sharpe*: [quantitativo.com](https://www.quantitativo.com/p/a-mean-reversion-strategy-with-211)
- Concretum Group paper list: [concretumgroup.com/papers](https://concretumgroup.com/papers/)
- Connors & Alvarez (2008), *Short Term Trading Strategies That Work*
- Gayed & Bilello (2016), *Leverage for the Long Run*
- Medhat & Schmeling (2022), *Short-term Momentum*, Review of Financial Studies
- Blitz et al. on short-term signals after costs: [Alpha Architect summary](https://alphaarchitect.com/alpha-from-short-term-signals/)
- 888 public strategies, in-sample vs out-of-sample: [CXO Advisory](https://www.cxoadvisory.com/big-ideas/in-sample-vs-out-of-sample-performance-of-888-trading-strategies)
- Short-term reversal overview: [Quantpedia](https://quantpedia.com/strategies/short-term-reversal-in-stocks)
