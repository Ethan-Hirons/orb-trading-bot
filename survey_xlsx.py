"""Build E19-strategy-survey.xlsx from survey.py's outputs. See ANALYSIS-2026-11-11-survey.md.

    python survey.py report && python survey.py posthoc > survey_posthoc.txt
    python survey_xlsx.py

Backtest outputs are written as values (they come from survey.py, not from the sheet).
Aggregates a reader may want to re-check (averages, counts, the PASS column) are formulas.
"""

from __future__ import annotations

import json

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L

OUT = "E19-strategy-survey.xlsx"
FONT = "Arial"
HEAD = PatternFill("solid", fgColor="1F3A5F")
BAND = PatternFill("solid", fgColor="F3F6FA")
# Conditional-format fills need the colour as bgColor too, or Excel shows nothing.
GOOD = PatternFill("solid", start_color="D9EAD3", end_color="D9EAD3")
BAD = PatternFill("solid", start_color="F4CCCC", end_color="F4CCCC")
THIN = Side(style="thin", color="C9D1DB")

SOURCES = {
    "C1a": ("Connors & Alvarez (2008), Short Term Trading Strategies That Work", "SPY", "2008"),
    "C1b": ("Connors & Alvarez (2008), same rule", "QQQ", "2008"),
    "C2": ("Quantitativo (May 2024), after Pagonidis (2013) on IBS", "QQQ", "2024"),
    "C3": ("Zarattini, Mele & Aziz (2025), The Volatility Edge", "VIXY (short/long)", "2025"),
    "C4": ("Gayed & Bilello (2016), Leverage for the Long Run", "SSO / cash", "2016"),
    "C5": ("Zarattini, Pagani & Barbon (2025), Catching Crypto Trends", "BTC-USD", "2025"),
    "C6": ("Connors & Alvarez (2008), portfolio form", "top-200 US stocks, 10 slots", "2008"),
}
BENCH = {"C1a": "SPY", "C1b": "QQQ", "C2": "QQQ", "C3": "SPY", "C4": "SPY", "C5": "BTC", "C6": "SPY"}

RESEARCH = [
    # family, example source, what the literature says, what we did, outcome
    ("Short-term mean reversion, index ETFs", "Connors & Alvarez 2008 (RSI-2); Pagonidis 2013 and Quantitativo 2024 (IBS)",
     "Strong 1993-2010; practitioner tests still show profits but lower. No clean post-2020 study found.",
     "Tested: C1a, C1b, C2", "See Summary"),
    ("Short-term mean reversion, large-cap stocks", "Connors & Alvarez 2008; de Groot, Huij & Zhou 2011; Blitz et al. 2022",
     "Gross reversal profits exist in large caps; after costs single signals are about zero (Blitz 2022).",
     "Tested: C6 (long-only, point-in-time universe)", "See Summary"),
    ("Volatility risk premium (VIX ETPs)", "Zarattini, Mele & Aziz 2025; Cooper 2013",
     "Positive premium with crash risk. Public replications report Sharpe ~0.7 for 2016-2026, sensitive to data and thresholds.",
     "Tested: C3", "See Summary"),
    ("Leveraged trend (200-day SMA)", "Gayed & Bilello 2016; r/LETFs community",
     "Very widely run. No rigorous out-of-sample study found; whipsaw costs are the known problem.",
     "Tested: C4 (a relative of E18; cannot rescue it)", "See Summary"),
    ("Crypto trend-following", "Zarattini, Pagani & Barbon 2025; Liu & Tsyvinski 2021",
     "Long trends robust in BTC history; the authors note shorter trends have weakened recently.",
     "Tested: C5", "See Summary"),
    ("Intraday index momentum (noise area)", "Zarattini, Aziz & Barbon 2024",
     "E15 post-hoc lead on QQQ.", "Not re-tested: this is E16, forward data only", "Running"),
    ("Opening-range breakout, stocks in play", "Zarattini, Barbon & Aziz 2024",
     "E14: the entry has no drift on this project's universe.", "Not re-tested: closed by E14", "Closed"),
    ("Last-half-hour momentum, overnight drift, turn of month", "Gao 2018; Baltussen 2021; Lakonishok & Smidt 1988",
     "E15: 0 of 4 on SPY.", "Not re-tested: closed by E15", "Closed"),
    ("Monthly trend / tactical allocation", "Faber 2007; Moskowitz, Ooi & Pedersen 2012",
     "E18: big drawdown cut, return bar failed.", "Not re-tested: closed by E18", "Closed"),
    ("Industry / stock trend-following", "Zarattini & Antonacci 2024; Zarattini, Pagani & Wilcox 2025",
     "Long holding periods, a few trades carry everything (under 7% of trades).", "Skipped: cannot be judged on paper within years", "Skipped"),
    ("Post-earnings drift", "Bernard & Thomas 1989 and successors",
     "Still documented, mostly in small caps.", "Skipped: no earnings-date or surprise data here", "Skipped"),
    ("Pairs trading / long-short reversal", "Gatev et al. 2006; Do & Faff 2010; Blitz et al. 2022",
     "Profits declined to about zero net of costs.", "Skipped: needs shorting many names, literature is negative", "Skipped"),
    ("LLM news scoring on momentum", "Anic, Barbon, Seiz & Zarattini 2025",
     "Reported improvement over plain momentum; needs a news archive and an LLM budget.", "Skipped: data and cost; ORB's news heuristic already failed as a filter (E14)", "Skipped"),
    ("Open-source bots (freqtrade strategies, Alpaca examples, TradingView scripts)", "GitHub, TradingView, Reddit",
     "No verified live track records found. One study of 888 public strategies: backtest Sharpe explains 1-2% of out-of-sample results.",
     "Used as a source of rules only (IBS, RSI-2, 200-day); none trusted on its own backtest", "n.a."),
]


def _style_header(ws, row, ncol):
    for c in range(1, ncol + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
        cell.fill = HEAD
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=THIN)


def _finish(ws, widths, freeze="A2"):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[L(i)].width = w
    for row in ws.iter_rows():
        for cell in row:
            if cell.font is None or cell.font.name != FONT:
                f = cell.font
                cell.font = Font(name=FONT, size=f.size or 10, bold=f.bold, italic=f.italic,
                                 color=f.color)
    ws.freeze_panes = freeze
    ws.sheet_view.showGridLines = False


def build() -> None:
    res = json.load(open("survey_results.json"))
    keys = sorted(res)
    curves = pd.read_csv("survey_curves.csv", index_col=0, parse_dates=True)
    trades = pd.read_csv("survey_trades.csv")
    wb = Workbook()

    # ------------------------------------------------------------ Summary
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "E19: survey of published daily strategies, 2016-01 to 2026-11 (pre-registered)"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    ws["A2"] = ("One bar for every candidate: timing alpha against its own underlying, t >= 2.45 "
                "(Bonferroni for 7 tests), plus four robustness checks. Net of costs. "
                "Rules and bar were committed (3269bee) before any data was fetched.")
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A2:R2")
    ws.row_dimensions[2].height = 32
    head = ["#", "Strategy", "Instrument", "Published", "Verdict", "Alpha / yr", "Alpha t",
            "Beta", "Alpha 1st half", "Alpha 2nd half", "Sharpe (excess)", "CAGR (cash earns 0)",
            "Max drawdown", "Time in market", "Trades / yr", "Win rate", "Mean trade",
            "Criteria passed (of 5)"]
    r0 = 4
    for c, h in enumerate(head, 1):
        ws.cell(row=r0, column=c, value=h)
    _style_header(ws, r0, len(head))
    ws.row_dimensions[r0].height = 46
    crit_rows = {}
    for i, k in enumerate(keys):
        r = res[k]
        row = r0 + 1 + i
        crit_rows[k] = row
        vals = [k, r["name"], SOURCES[k][1], int(SOURCES[k][2]), None, r["alpha_ann"], r["alpha_t"],
                r["beta"], r["alpha_h1"], r["alpha_h2"], r["sharpe"], r["cagr"], r["mdd"],
                r["exposure"], r.get("trades_per_year"), r.get("win_rate"), r.get("mean_trade"),
                None]
        for c, v in enumerate(vals, 1):
            ws.cell(row=row, column=c, value=v)
        cr = 5 + i                                   # the matching row on the Criteria sheet
        ws.cell(row=row, column=5, value=f'=IF(Criteria!M{cr}=5,"PASS","FAIL")')
        ws.cell(row=row, column=18, value=f"=Criteria!M{cr}")
        for c, fmt in ((6, "0.00%"), (7, "0.00"), (8, "0.00"), (9, "0.00%"), (10, "0.00%"),
                       (11, "0.00"), (12, "0.0%"), (13, "0.0%"), (14, "0%"), (15, "0.0"),
                       (16, "0%"), (17, "0.00%")):
            ws.cell(row=row, column=c).number_format = fmt
        if i % 2:
            for c in range(1, len(head) + 1):
                ws.cell(row=row, column=c).fill = BAND
    last = r0 + len(keys)
    ws.conditional_formatting.add(f"E{r0 + 1}:E{last}", CellIsRule(operator="equal", formula=['"PASS"'], fill=GOOD))
    ws.conditional_formatting.add(f"E{r0 + 1}:E{last}", CellIsRule(operator="equal", formula=['"FAIL"'], fill=BAD))
    n = last + 2
    ws.cell(row=n, column=1, value="Benchmarks, same period (buy and hold, total return)").font = Font(name=FONT, bold=True)
    for j, (name, col) in enumerate((("SPY", "SPY"), ("QQQ", "QQQ"))):
        s = curves[col].dropna()
        yrs = (s.index[-1] - s.index[0]).days / 365.25
        ws.cell(row=n + 1 + j, column=2, value=name)
        ws.cell(row=n + 1 + j, column=12, value=s.iloc[-1] ** (1 / yrs) - 1).number_format = "0.0%"
        ws.cell(row=n + 1 + j, column=13, value=float((s / s.cummax() - 1).min())).number_format = "0.0%"
    notes_row = n + 4
    ws.cell(row=notes_row, column=1, value="How to read this").font = Font(name=FONT, bold=True)
    for j, t in enumerate(NOTES_SUMMARY):
        c = ws.cell(row=notes_row + 1 + j, column=1, value=t)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=notes_row + 1 + j, start_column=1, end_row=notes_row + 1 + j, end_column=18)
        ws.row_dimensions[notes_row + 1 + j].height = 30
    _finish(ws, [6, 34, 27, 10, 9, 10, 8, 7, 11, 11, 9, 12, 10, 9, 8, 8, 9, 10], freeze="C5")

    # ------------------------------------------------------------ Criteria
    wc = wb.create_sheet("Criteria")
    wc["A1"] = "The five pre-registered criteria, value against threshold"
    wc["A1"].font = Font(name=FONT, bold=True, size=12)
    wc["A2"] = "Thresholds (from the PREREG):"
    wc["B2"], wc["C2"] = "alpha t >=", 2.45
    wc["D2"], wc["E2"] = "max drawdown >=", -0.25
    wc["E2"].number_format = "0%"
    for cell in (wc["C2"], wc["E2"]):
        cell.font = Font(name=FONT, color="0000FF")
    head = ["#", "Strategy", "1: alpha t", "2a: alpha 1st half", "2b: alpha 2nd half",
            "3a: mean excess at stress costs", "3b: mean excess, next-open fills",
            "4: total without best trades/days", "5: max drawdown",
            "Crit 1 met", "Crit 2 met", "Crit 3 met", "Passed (of 5)", "Crit 4 met", "Crit 5 met",
            "What criterion 4 removes"]
    # layout: J..L = criteria 1-3 flags, M = count, N..O = criteria 4-5 flags
    for c, h in enumerate(head, 1):
        wc.cell(row=4, column=c, value=h)
    _style_header(wc, 4, len(head))
    wc.row_dimensions[4].height = 42
    for i, k in enumerate(keys):
        r = res[k]
        row = 5 + i
        vals = [k, r["name"], r["alpha_t"], r["alpha_h1"], r["alpha_h2"],
                r.get("stress_mean_ex_ann"), r.get("open_mean_ex_ann"), r["robust_total"], r["mdd"]]
        for c, v in enumerate(vals, 1):
            wc.cell(row=row, column=c, value=v)
        wc.cell(row=row, column=10, value=f"=IF(C{row}>=$C$2,1,0)")
        wc.cell(row=row, column=11, value=f"=IF(AND(D{row}>0,E{row}>0),1,0)")
        wc.cell(row=row, column=12, value=f"=IF(AND(F{row}>0,G{row}>0),1,0)")
        wc.cell(row=row, column=14, value=f"=IF(H{row}>0,1,0)")
        wc.cell(row=row, column=15, value=f"=IF(I{row}>=$E$2,1,0)")
        wc.cell(row=row, column=13, value=f"=J{row}+K{row}+L{row}+N{row}+O{row}")
        wc.cell(row=row, column=16, value=r["robust_rule"])
        wc.cell(row=row, column=3).number_format = "0.00"
        for c in (4, 5, 6, 7, 8, 9):
            wc.cell(row=row, column=c).number_format = "0.00%"
    lastc = 4 + len(keys)
    for rng in (f"J5:L{lastc}", f"N5:O{lastc}"):
        wc.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=["1"], fill=GOOD))
        wc.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=["0"], fill=BAD))
    _finish(wc, [6, 34, 10, 12, 12, 14, 14, 14, 11, 7, 7, 7, 9, 7, 7, 48], freeze="C5")

    # ------------------------------------------------------------ Yearly
    wy = wb.create_sheet("Yearly")
    wy["A1"] = "Calendar-year returns, net of costs, idle cash earning nothing (2026 is to 10 Nov)"
    wy["A1"].font = Font(name=FONT, bold=True, size=12)
    cols = keys + ["SPY", "QQQ"]
    wy.cell(row=3, column=1, value="Year")
    for c, k in enumerate(cols, 2):
        wy.cell(row=3, column=c, value=k if k in ("SPY", "QQQ") else f"{k} {res[k]['name']}")
    _style_header(wy, 3, len(cols) + 1)
    wy.row_dimensions[3].height = 44
    years = sorted({y for k in keys for y in res[k]["yearly"]})
    bh = {b: (curves[b].dropna().groupby(curves[b].dropna().index.year).last() /
              curves[b].dropna().groupby(curves[b].dropna().index.year).first() - 1) for b in ("SPY", "QQQ")}
    bh_full = {}
    for b in ("SPY", "QQQ"):
        s = curves[b].dropna()
        ends = s.groupby(s.index.year).last()
        prev = ends.shift(1).fillna(1.0)
        bh_full[b] = (ends / prev - 1).to_dict()
    for i, y in enumerate(years):
        row = 4 + i
        wy.cell(row=row, column=1, value=str(y))
        for c, k in enumerate(cols, 2):
            v = bh_full[k].get(int(y)) if k in bh_full else res[k]["yearly"].get(y)
            cell = wy.cell(row=row, column=c, value=v)
            cell.number_format = "0.0%;[Red]-0.0%"
    end = 3 + len(years)
    for label, fn, off in (("Average year", "AVERAGE", 1), ("Worst year", "MIN", 2), ("Best year", "MAX", 3)):
        wy.cell(row=end + off, column=1, value=label).font = Font(name=FONT, bold=True)
        for c in range(2, len(cols) + 2):
            cell = wy.cell(row=end + off, column=c, value=f"={fn}({L(c)}4:{L(c)}{end})")
            cell.number_format = "0.0%;[Red]-0.0%"
    wy.cell(row=end + 4, column=1, value="Positive years").font = Font(name=FONT, bold=True)
    for c in range(2, len(cols) + 2):
        wy.cell(row=end + 4, column=c, value=f'=COUNTIF({L(c)}4:{L(c)}{end},">0")')
    wy.cell(row=end + 6, column=1, value="C6 starts in 2017 (Alpaca's history starts in 2016 and the 200-day SMA needs a year). "
                                         "SPY and QQQ are buy and hold, total return.")
    _finish(wy, [14] + [16] * len(cols), freeze="B4")

    # ------------------------------------------------------------ Equity curves
    we = wb.create_sheet("Equity curves")
    wk = curves.resample("W-FRI").last().dropna(how="all")
    we["A1"] = "Growth of 1.00, weekly, net of costs, idle cash earning nothing"
    we["A1"].font = Font(name=FONT, bold=True, size=12)
    we.cell(row=3, column=1, value="Week ending")
    order = [k for k in keys if k in wk.columns] + ["SPY", "QQQ"]
    for c, k in enumerate(order, 2):
        we.cell(row=3, column=c, value=k)
    _style_header(we, 3, len(order) + 1)
    for i, (d, row) in enumerate(wk[order].iterrows()):
        we.cell(row=4 + i, column=1, value=d.date()).number_format = "yyyy-mm-dd"
        for c, k in enumerate(order, 2):
            v = row[k]
            if pd.notna(v):
                we.cell(row=4 + i, column=c, value=round(float(v), 4)).number_format = "0.000"
    nrows = len(wk)

    def chart(title, cols_idx, anchor, log=False):
        ch = LineChart()
        ch.title, ch.height, ch.width = title, 10.5, 22
        ch.title.overlay = False
        ch.legend.position = "b"
        ch.legend.overlay = False
        ch.y_axis.number_format = "0.0"
        if log:
            ch.y_axis.scaling.logBase = 10
        ch.x_axis.number_format = "yyyy"
        ch.x_axis.tickLblSkip = 52
        ch.x_axis.delete = False
        ch.y_axis.delete = False
        for ci in cols_idx:
            ch.add_data(Reference(we, min_col=ci, min_row=3, max_row=3 + nrows), titles_from_data=True)
        ch.set_categories(Reference(we, min_col=1, min_row=4, max_row=3 + nrows))
        for s in ch.series:
            s.smooth = False
            s.graphicalProperties.line.width = 15000
        we.add_chart(ch, anchor)

    idx = {k: i for i, k in enumerate(order, 2)}
    anchor_col = L(len(order) + 3)
    chart("Growth of 1.00: equity and VIX rules vs SPY", [idx[k] for k in ("C1a", "C1b", "C2", "C3", "C6", "SPY") if k in idx], f"{anchor_col}3")
    chart("Growth of 1.00: leveraged trend (C4) and BTC trend (C5) vs QQQ", [idx[k] for k in ("C4", "C5", "QQQ") if k in idx], f"{anchor_col}26")
    _finish(we, [13] + [9] * len(order), freeze="B4")

    # ------------------------------------------------------------ Trades
    wt = wb.create_sheet("Trades")
    wt["A1"] = "Round trips in the scored period, net of costs (C3 and C5 hold continuously and have none)"
    wt["A1"].font = Font(name=FONT, bold=True, size=12)
    for c, h in enumerate(["Candidate", "Entry", "Exit", "Net return", "Symbol"], 1):
        wt.cell(row=3, column=c, value=h)
    _style_header(wt, 3, 5)
    for i, t in enumerate(trades.itertuples(index=False)):
        wt.cell(row=4 + i, column=1, value=t.candidate)
        wt.cell(row=4 + i, column=2, value=pd.Timestamp(t.entry).date()).number_format = "yyyy-mm-dd"
        wt.cell(row=4 + i, column=3, value=pd.Timestamp(t.exit).date()).number_format = "yyyy-mm-dd"
        wt.cell(row=4 + i, column=4, value=float(t.net_return)).number_format = "0.00%;[Red]-0.00%"
        wt.cell(row=4 + i, column=5, value=t.symbol if isinstance(t.symbol, str) else None)
    tend = 3 + len(trades)
    wt["G3"], wt["H3"], wt["I3"], wt["J3"], wt["K3"] = "Candidate", "Trades", "Win rate", "Mean", "Total (sum)"
    for c in range(7, 12):
        cell = wt.cell(row=3, column=c)
        cell.font, cell.fill = Font(name=FONT, bold=True, color="FFFFFF"), HEAD
    for j, k in enumerate(sorted(trades["candidate"].unique())):
        row = 4 + j
        wt.cell(row=row, column=7, value=k)
        wt.cell(row=row, column=8, value=f"=COUNTIF($A$4:$A${tend},G{row})")
        wt.cell(row=row, column=9, value=f'=COUNTIFS($A$4:$A${tend},G{row},$D$4:$D${tend},">0")/H{row}').number_format = "0%"
        wt.cell(row=row, column=10, value=f"=AVERAGEIF($A$4:$A${tend},G{row},$D$4:$D${tend})").number_format = "0.00%"
        wt.cell(row=row, column=11, value=f"=SUMIF($A$4:$A${tend},G{row},$D$4:$D${tend})").number_format = "0.0%"
    _finish(wt, [11, 12, 12, 11, 9, 3, 11, 8, 9, 9, 11], freeze="A4")

    # ------------------------------------------------------------ Post hoc
    import re
    wp = wb.create_sheet("Post hoc")
    wp["A1"] = "POST HOC: not pre-registered, cannot produce a pass. Run after the scored results, to help read them."
    wp["A1"].font = Font(name=FONT, bold=True, size=12, color="9C0006")
    text = open("survey_posthoc.txt", encoding="utf-8", errors="replace").read()
    pat = re.compile(r"ph\s+(.+?)\s+alpha\s+([+-][\d.]+)% t\s+([+-][\d.]+)\s+beta\s+([\d.-]+)\s+halves\s+"
                     r"([+-][\d.]+)%/([+-][\d.]+)%\s+Sharpe\s+([\d.-]+)\s+CAGR\s+([+-][\d.]+)%\s+maxDD\s+"
                     r"([+-][\d.]+)%\s+in-mkt\s+(\d+)%")
    for c, h in enumerate(["Variant", "Alpha / yr", "Alpha t", "Beta", "Alpha 1st half",
                           "Alpha 2nd half", "Sharpe", "CAGR", "Max drawdown", "Time in market"], 1):
        wp.cell(row=3, column=c, value=h)
    _style_header(wp, 3, 10)
    row = 4
    for mt in pat.finditer(text):
        g = mt.groups()
        vals = [g[0], float(g[1]) / 100, float(g[2]), float(g[3]), float(g[4]) / 100,
                float(g[5]) / 100, float(g[6]), float(g[7]) / 100, float(g[8]) / 100, float(g[9]) / 100]
        for c, v in enumerate(vals, 1):
            wp.cell(row=row, column=c, value=v)
        for c, fmt in ((2, "0.00%"), (3, "0.00"), (4, "0.00"), (5, "0.00%"), (6, "0.00%"), (7, "0.00"),
                       (8, "0.0%"), (9, "0.0%"), (10, "0%")):
            wp.cell(row=row, column=c).number_format = fmt
        row += 1
    row += 1
    wp.cell(row=row, column=1, value="C5 (BTC trend) period by period: the edge is fading").font = Font(name=FONT, bold=True)
    row += 1
    for c, h in enumerate(["Period", "C5 total return", "C5 Sharpe", "C5 alpha / yr", "C5 alpha t",
                           "C5 max drawdown", "BTC buy and hold", "BTC max drawdown"], 1):
        wp.cell(row=row, column=c, value=h)
    _style_header(wp, row, 8)
    pat2 = re.compile(r"(\d{4}-\d{4}): C5 total\s+([+-][\d.]+)%\s+Sharpe\s+([\d.-]+)\s+alpha\s+([+-][\d.]+)% t\s+"
                      r"([+-][\d.]+)\s+maxDD\s+([+-][\d.]+)%\s+\(BTC buy and hold\s+([+-][\d.]+)%, maxDD\s+([+-][\d.]+)%\)")
    for mt in pat2.finditer(text):
        row += 1
        g = mt.groups()
        vals = [g[0], float(g[1]) / 100, float(g[2]), float(g[3]) / 100, float(g[4]), float(g[5]) / 100,
                float(g[6]) / 100, float(g[7]) / 100]
        for c, v in enumerate(vals, 1):
            wp.cell(row=row, column=c, value=v)
        for c, fmt in ((2, "0.0%"), (3, "0.00"), (4, "0.0%"), (5, "0.00"), (6, "0.0%"), (7, "0.0%"), (8, "0.0%")):
            wp.cell(row=row, column=c).number_format = fmt
    row += 2
    for line in POSTHOC_NOTES:
        cell = wp.cell(row=row, column=1, value=line)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        wp.merge_cells(start_row=row, start_column=1, end_row=row, end_column=10)
        wp.row_dimensions[row].height = 30
        row += 1
    _finish(wp, [38, 11, 9, 8, 13, 13, 9, 9, 13, 13], freeze="A4")

    # ------------------------------------------------------------ Research
    wr = wb.create_sheet("Research")
    wr["A1"] = "What was surveyed (papers, public bots, this project's own closed experiments)"
    wr["A1"].font = Font(name=FONT, bold=True, size=12)
    for c, h in enumerate(["Strategy family", "Sources", "What the evidence says", "What E19 did", "Status"], 1):
        wr.cell(row=3, column=c, value=h)
    _style_header(wr, 3, 5)
    for i, rowv in enumerate(RESEARCH):
        for c, v in enumerate(rowv, 1):
            cell = wr.cell(row=4 + i, column=c, value=v)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        wr.row_dimensions[4 + i].height = 58
    _finish(wr, [30, 44, 60, 40, 14], freeze="A4")

    # ------------------------------------------------------------ Method
    wm = wb.create_sheet("Method")
    wm["A1"] = "Method, costs and caveats"
    wm["A1"].font = Font(name=FONT, bold=True, size=12)
    for i, (a, b) in enumerate(METHOD):
        wm.cell(row=3 + i, column=1, value=a).font = Font(name=FONT, bold=True)
        cell = wm.cell(row=3 + i, column=2, value=b)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        wm.cell(row=3 + i, column=1).alignment = Alignment(vertical="top")
        wm.row_dimensions[3 + i].height = max(16, 15 * (len(b) // 110 + 1))
    _finish(wm, [26, 120], freeze="A3")

    wb.save(OUT)
    print(f"wrote {OUT}: sheets {wb.sheetnames}")


NOTES_SUMMARY = [
    "Alpha is what is left after removing the return the strategy would have earned just by holding its underlying at "
    "the same average sensitivity (beta). A long-only rule that is in QQQ a fifth of the time earns part of QQQ's return for free; alpha asks whether the timing added anything.",
    "Alpha t is the alpha divided by its standard error (Newey-West, 5 lags). With seven tests, 2.45 is the level one of them reaches by luck 5% of the time.",
    "CAGR and drawdown assume idle cash earns nothing, as on an Alpaca paper account. Sharpe uses returns above T-bills on the money actually invested.",
    "Published = when the rule's source came out. For C2, C3 and C5 most of 2016-2026 was inside the authors' own samples, so a good number there is weaker evidence.",
]

POSTHOC_NOTES = [
    "Vol-targeted buy and hold BTC (no trend rule) scores alpha t 1.07, so the Donchian rule is doing real work beyond position sizing: t 2.75 and half the drawdown.",
    "Thirteen neighbouring settings of C5 (volatility target 20-30%, band 0.02-0.10, short or long lookbacks only, without the 5-day or 360-day model) give alpha t from 2.10 to 2.81; 12 of 13 clear 2.45. All show the same fade in the second half. The paper's own BTC table (2015-2025, no costs) reports Sharpe 1.58, drawdown 19%, alpha 14%, beta 0.17; this run gives 1.34, 20.2%, 9.7%, 0.18 after costs.",
    "The same ensemble on ETH (2019 on) is positive but not significant (t 1.47). ETH was not pre-registered.",
    "The article's own tuned C2 variants (300-day SMA exit or filter) do worse than its base rule from 2016, and the base rule shows nothing on SPY or IWM. The IBS result is specific to QQQ, which is a warning.",
    "Monthly momentum in C6's stock universe (1-month and 12-1) shows no alpha against SPY either. The first run of that check had a look-ahead bug (it showed +772% a year); it was caught, fixed and re-run before anything was read from it.",
]

METHOD = [
    ("Pre-registration", "PREREG-SURVEY-2026-11-11.md, committed as 3269bee before any price data was fetched. Rules, costs, period and the pass bar were fixed there. Nothing was tuned."),
    ("Scored period", "2016-01-01 to 2026-11-10 (C6 from 2017-01-01). Halves split at the calendar midpoint."),
    ("Data", "ETFs, VIX indices and BTC: Yahoo daily chart API, adjusted for splits and dividends. C6 stocks: Alpaca SIP daily bars, adjustment=all, for a point-in-time universe (top 200 by trailing 3-month dollar volume among NYSE/NASDAQ names, including since-delisted ones)."),
    ("Costs, scored", "SPY/QQQ/SSO 2 bp per side; VIXY 5 bp per side plus 3%/yr borrow on shorts; stocks 5 bp per side; BTC 30 bp per side."),
    ("Costs, stress", "SPY/QQQ/SSO 5 bp; VIXY 10 bp and 6%/yr borrow; stocks 10 bp; BTC 60 bp. Each rule is also re-run with every fill delayed to the next open (BTC: one day later)."),
    ("Fills", "Signals are computed from the day's closing values and filled at that close. A live bot would read prices a few minutes before the bell, so the next-open run brackets the error."),
    ("Criterion 1", "Alpha t-statistic >= 2.45 against the candidate's own underlying (SPY, QQQ or BTC)."),
    ("Criterion 2", "Alpha positive in both halves of the scored period."),
    ("Criterion 3", "Mean excess return positive at stress costs and with next-open fills."),
    ("Criterion 4", "Total return still positive without the best 5% of trades (C3, C5: without the best 10 days)."),
    ("Criterion 5", "Maximum drawdown no deeper than -25%."),
    ("Known limits", "Daily bars only: intraday stops and gaps inside the day are not modelled. Yahoo's BTC series is an aggregate, not Alpaca's own venue price. VIXY borrow cost and availability are assumed, not measured. The C6 universe keyword filter also drops some real companies whose listing name contains 'Shares' or 'Trust' (many ADRs and REITs). No backtest here has been checked against live fills."),
    ("Not financial advice", "This is a research record for a paper-trading experiment. No live money is in scope."),
]


if __name__ == "__main__":
    build()
