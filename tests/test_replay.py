"""Offline unit tests for replay.py — no network, no Alpaca, no bars needed.

Run: python tests/test_replay.py   (exits non-zero on failure)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from replay import Trade, replay, session_vwap, _entry_times_from_logs, has_trading_halt
def bar(t,o,h,l,c,n=50): return {"t":t,"o":o,"h":h,"l":l,"c":c,"v":1000,"n":n}
def mk(bars,side="long",entry=100.0,entry_time="09:39"):
    t=Trade(date="2026-01-02",symbol="TEST",side=side,qty=10,entry=entry,exit=0,pnl=0,
            pnl_pct=0,entry_time=entry_time); t.bars=bars; return t
LIVE={"stop_pct":1.5,"trail_pct":1.25,"trail_arm":1.25}
NOTRAIL={"stop_pct":1.5}
f=[]
def chk(n,got,want,tol=0.01):
    ok=abs(got-want)<tol; print(f"  {'PASS' if ok else 'FAIL'}  {n}: got {got:.3f} want {want:.3f}")
    if not ok: f.append(n)

t=mk([bar("09:40",100,100.1,99,99),bar("09:41",99,99,98,98.2)])
chk("1 straight down -> hard stop",replay(t,LIVE,"touch",3)[0],98.5)

# BE tested in ISOLATION (no trail competing for the stop level)
up=[bar("09:40",100,102,99.9,101.9),bar("09:41",101.9,102,98,98.2)]
chk("2a no-BE, no-trail -> hard stop",replay(mk(up),NOTRAIL,"touch",3)[0],98.5)
chk("2b BE at +1.0 lifts stop to cost",replay(mk(up),{**NOTRAIL,"be_at":1.0},"touch",3)[0],100.0)
chk("2c BE not reached (+3) leaves stop",replay(mk(up),{**NOTRAIL,"be_at":3.0},"touch",3)[0],98.5)
# with the live trail, the trail is tighter than BE and must win
chk("2d trail arms off the CLOSE not the high (v1.15)",replay(mk(up),{**LIVE,"be_at":1.0},"touch",3)[0],101.9*0.9875)

run=[bar("09:40",100,103,99.9,102.9),bar("09:41",102.9,106,102.8,105.9),bar("15:50",105.9,106,105.5,105.7)]
a=replay(mk(run),LIVE,"touch",3)[0]
chk("3 BE never clips a runner",replay(mk(run),{**LIVE,"be_at":1.0},"touch",3)[0],a)
chk("4 scale blends half at +1.25",replay(mk(run),{**LIVE,"scale_at":1.25},"touch",3)[0],0.5*101.25+0.5*a)

wick=[bar("09:40",100,101.5,99.9,100.1),bar("15:50",100.1,100.2,99.9,100.0)]
rem=100.0        # v1.15: close is 100.1, trailing from there locks <entry, so NO arm;
                 # the remainder simply rides to the eod close. The wick cannot arm it.
chk("5a touch fills the wick",replay(mk(wick),{**LIVE,"scale_at":1.25},"touch",3)[0],0.5*101.25+0.5*rem)
chk("5b close ignores the wick",replay(mk(wick),{**LIVE,"scale_at":1.25},"close",3)[0],100.0)
thin=[bar("09:40",100,101.5,99.9,100.1,n=1),bar("15:50",100.1,100.2,99.9,100.0)]
chk("6 thin bar cannot set an extreme",replay(mk(thin),{**LIVE,"scale_at":1.25},"touch",3)[0],100.0)

sh=[bar("09:40",100,100.1,97,97.2),bar("15:50",97.2,97.5,97,97.1)]
t=mk(sh,side="short"); px=replay(t,LIVE,"touch",3)[0]
chk("7a short rides to eod (trail 98.21 untouched)",px,97.1)
print(f"        short P&L {t.pct(px):+.2f}% -- must be POSITIVE")
if t.pct(px)<0: f.append("7b short sign")
sh3=[bar("09:40",100,100.1,97,97.2),bar("15:50",97.2,99,97,98.9)]
chk("7c short trail arms off the close (v1.15)",replay(mk(sh3,side="short"),LIVE,"touch",3)[0],97.2*1.0125)
chk("8 short hard stop",replay(mk([bar("09:40",100,102,99.9,101.9)],side="short"),LIVE,"touch",3)[0],101.5)

# ---------------- VWAP trail ----------------
def vbar(t,o,h,l,c,v=1000,vw=None,n=50):
    d=bar(t,o,h,l,c,n); d["v"]=v; d["vw"]=vw if vw is not None else (h+l+c)/3.0; return d
VW={"stop_pct":1.5,"vwap_exit":True}

# session VWAP must accumulate from the OPEN, not from entry
pre=[vbar("09:30",100,100,100,100,v=1000,vw=100.0),
     vbar("09:35",100,100,100,100,v=1000,vw=100.0)]
# 09:41 closes below VWAP (~100.00) but its LOW stays above the 99.485 stop,
# so only the VWAP rule can fire. Otherwise the test proves nothing.
t=mk(pre+[vbar("09:40",101,101.2,100.9,101.0,v=10,vw=101.0),
          vbar("09:41",101,100.1,99.6,99.7,v=10,vw=99.8)],entry=101.0)
vw=session_vwap(t.bars)
chk("V1 VWAP accumulates from the open",vw["09:35"],100.0)
px,why=replay(t,VW,"touch",3)
chk("V2 long exits on close below VWAP",px,99.7); print(f"        reason={why}")
if why!="vwap": f.append("V2 reason")

# stays above VWAP -> no vwap exit, rides to eod
t2=mk(pre+[vbar("09:40",101,101.5,100.9,101.4,v=10,vw=101.2),
           vbar("15:50",101.4,101.6,101.2,101.5,v=10,vw=101.4)],entry=101.0)
px,why=replay(t2,VW,"touch",3)
chk("V3 no exit while above VWAP",px,101.5)
if why!="eod": f.append("V3 reason")

# NO DEAD BAND: fires while the trade is only barely up - the whole point
t3=mk(pre+[vbar("09:40",100.2,100.3,100.1,100.25,v=10,vw=100.2),
           vbar("09:41",100.25,100.3,99.8,99.9,v=10,vw=100.0)],entry=100.2)
px,why=replay(t3,VW,"touch",3)
chk("V4 fires at +0.05% (trail could never arm)",px,99.9)
if why!="vwap": f.append("V4 reason")

# short: exits when close crosses ABOVE vwap
t4=mk(pre+[vbar("09:40",99,99.1,98.8,98.9,v=10,vw=99.0),
           vbar("09:41",98.9,100.3,99.0,100.2,v=10,vw=100.0)],side="short",entry=99.0)
px,why=replay(t4,VW,"touch",3)
chk("V5 short exits on close above VWAP",px,100.2)
if why!="vwap": f.append("V5 reason")

# hard stop still wins when it is hit first
t5=mk(pre+[vbar("09:40",101,101.1,99.0,99.1,v=10,vw=100.0)],entry=101.0)
px,_=replay(t5,VW,"touch",3)
chk("V6 hard stop still takes precedence",px,101*0.985)

# ---- the entry bar must be EXCLUDED: its low predates the fill ----
# entry at 09:40; that bar dips to 97 (pre-fill action) which would trip the
# 1.5% stop at 98.5. It must be ignored; the trade should ride to the eod bar.
eb=[bar("09:40",100,100.5,97.0,100.2), bar("15:50",100.2,100.4,100.0,100.3)]
px,why=replay(mk(eb,entry_time="09:40"),LIVE,"touch",3)
chk("E1 entry bar ignored (pre-fill low)",px,100.3)
if why!="eod": f.append("E1 reason")
# but a dip in the NEXT bar must still stop
eb2=[bar("09:40",100,100.5,97.0,100.2), bar("09:41",100.2,100.3,97.5,97.6)]
px,_=replay(mk(eb2,entry_time="09:40"),LIVE,"touch",3)
chk("E2 later bar still stops",px,98.5)
# trail exits are now labelled distinctly from hard stops
tr=[bar("09:40",100,101.5,99.9,101.4), bar("09:41",101.4,101.5,100.0,100.1)]
_,why=replay(mk(tr),LIVE,"touch",3)
print(f"  {'PASS' if why=='trail' else 'FAIL'}  E3 trail exit labelled 'trail': got '{why}'")
if why!="trail": f.append("E3 label")

# ---- no-bars must NOT hand back the real answer ----
t=mk([bar("09:40",100,101,99.5,100.5)],entry_time="09:40")   # only the entry bar
t.exit=123.45                                                 # the real exit price
px,why=replay(t,LIVE,"touch",3)
ok = px is None and why=="no-bars"
print(f"  {'PASS' if ok else 'FAIL'}  N1 unreplayable returns None, not t.exit: got {px!r}/{why}")
if not ok: f.append("N1")

# ---- log timezone calibration ----
et=_entry_times_from_logs()
bad=[(k,v) for k,v in et.items() if v>="15:50" or v<"09:30"]
print(f"  {'PASS' if not bad else 'FAIL'}  N2 no entry time outside market hours: {len(bad)} bad")
if bad: f.append(f"N2 {bad[:3]}")
jul=[v for k,v in et.items() if k[0].startswith("2026-07")]
sep=[v for k,v in et.items() if k[0] >= "2026-09-08"]
okj = all("09:30"<=v<"13:00" for v in jul) if jul else True
oks = all("09:30"<=v<"13:00" for v in sep) if sep else True
print(f"  {'PASS' if okj and oks else 'FAIL'}  N3 CEST-era (Jul) and ET-era (Sep) both sane: "
      f"Jul n={len(jul)} Sep n={len(sep)}")
if not (okj and oks): f.append("N3")

# ---- v1.16 N4: the adverse-news exit must fire, and beat the 15:50 flatten ----
# Regression for the 2026-10-15 finding: exit_on_adverse was unmodelled, so
# NKE 10-02 and INTC 10-13 were held to the flatten and credited +2.00%/+1.96%
# of range they never got.
bars=[bar("09:40",100,100.5,99.8,100.2), bar("12:24",100,100.2,99.6,99.9),
      bar("14:00",100,103,100,102.5), bar("15:50",102.5,103,102,103.0)]
t=mk(bars,entry_time="09:40")
t.news_exit_time="12:24"
px,why=replay(t,LIVE,"touch",3)
ok = why=="news" and abs(px-99.9)<0.01
print(f"  {'PASS' if ok else 'FAIL'}  N4 adverse-news exit fires at its bar: got {px!r}/{why} (want 99.9/news)")
if not ok: f.append("N4")

# and WITHOUT the news time it must still ride to the flatten (no regression)
t2=mk(bars,entry_time="09:40")
px2,why2=replay(t2,LIVE,"touch",3)
ok2 = why2 in ("eod","trail") and px2 > 100
print(f"  {'PASS' if ok2 else 'FAIL'}  N5 no news time -> unchanged behaviour: got {px2!r}/{why2}")
if not ok2: f.append("N5")

# ---- v1.16 N6: halted sessions are EXCLUDED, never scored ----
# Regression for WFF 2026-10-12: a resting trail filled on a halt re-open gap
# at +26.71% having armed at +9.97%, and carried 71% of the validation gap.
halt=[bar("09:40",100,101,99.5,100.5), bar("09:41",100.5,101,100,100.8),
      bar("09:55",130,131,129,130.5),   # 14-min gap, +29% jump = halt
      bar("15:50",130,131,129,130.0)]
t3=mk(halt,entry_time="09:40")
px3,why3=replay(t3,LIVE,"touch",3)
ok3 = px3 is None and why3=="halted"
print(f"  {'PASS' if ok3 else 'FAIL'}  N6 halted session excluded: got {px3!r}/{why3}")
if not ok3: f.append("N6")

# a normal session must NOT be flagged as halted
ok4 = not has_trading_halt([bar("09:40",100,101,99.5,100.5),
                            bar("09:41",100.5,101,100,100.8),
                            bar("09:42",100.8,101,100.5,100.9)])
print(f"  {'PASS' if ok4 else 'FAIL'}  N7 normal session not flagged halted")
if not ok4: f.append("N7")

print("\n"+("ALL PASS" if not f else f"FAILURES: {f}"))
sys.exit(1 if f else 0)
