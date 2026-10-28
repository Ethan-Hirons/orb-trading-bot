#!/usr/bin/env python3
"""Render a self-contained session dashboard.

    python dashboard.py                      # -> dashboard.html
    python dashboard.py --limit 20 -o x.html
    python dashboard.py --open               # also print the file:// URL

One HTML file, no CDN, no network: it has to open on a VPS over SSH or from
a laptop with no internet. Everything is inlined.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline.snapshot import load_sessions, to_dicts  # noqa: E402

CSS = """
*{box-sizing:border-box}
:root{
  --bg:#fbfbfa; --panel:#fff; --ink:#1a1a18; --mute:#6b6b66; --line:#e5e4e0;
  --up:#1a7f5a; --down:#b4443a; --warn:#9a6b1f; --accent:#2f5fd0;
  --chip:#f0efec;
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#17171a; --panel:#1f1f23; --ink:#e9e8e4; --mute:#9a9a94; --line:#303036;
  --up:#4fbf8f; --down:#e2776b; --warn:#d4a24a; --accent:#7aa2f7; --chip:#2a2a30;
}}
:root[data-theme=dark]{
  --bg:#17171a; --panel:#1f1f23; --ink:#e9e8e4; --mute:#9a9a94; --line:#303036;
  --up:#4fbf8f; --down:#e2776b; --warn:#d4a24a; --accent:#7aa2f7; --chip:#2a2a30;
}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:18px 16px 12px;border-bottom:1px solid var(--line)}
h1{margin:0;font-size:17px;font-weight:600;letter-spacing:-.01em}
.sub{color:var(--mute);font-size:12.5px;margin-top:3px}
.wrap{display:grid;grid-template-columns:200px 1fr;gap:16px;padding:16px;
  max-width:1180px;margin:0 auto;align-items:start}
@media(max-width:760px){.wrap{grid-template-columns:1fr}
  nav{display:flex;overflow-x:auto;gap:6px;padding-bottom:6px}
  nav button{flex:0 0 auto}}
nav button{display:block;width:100%;text-align:left;margin-bottom:3px;
  padding:7px 9px;border:1px solid transparent;border-radius:7px;
  background:none;color:var(--ink);cursor:pointer;font:inherit;font-size:13px}
nav button:hover{background:var(--chip)}
nav button[aria-current=true]{background:var(--panel);border-color:var(--line);
  font-weight:600}
nav .d{color:var(--mute);font-size:11.5px;font-variant-numeric:tabular-nums}
section{background:var(--panel);border:1px solid var(--line);border-radius:11px;
  padding:14px 15px;margin-bottom:14px}
h2{margin:0 0 10px;font-size:12px;text-transform:uppercase;
  letter-spacing:.07em;color:var(--mute);font-weight:600}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));
  gap:10px}
.kpi{border:1px solid var(--line);border-radius:9px;padding:9px 11px}
.kpi b{display:block;font-size:19px;font-weight:600;
  font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.kpi span{color:var(--mute);font-size:11.5px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--mute);font-weight:600;font-size:11.5px;
  text-transform:uppercase;letter-spacing:.05em;padding:4px 7px 6px;
  border-bottom:1px solid var(--line)}
td{padding:5px 7px;border-bottom:1px solid var(--line);
  font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.num{text-align:right}
.up{color:var(--up)} .down{color:var(--down)} .warn{color:var(--warn)}
.mute{color:var(--mute)}
.chip{display:inline-block;padding:1px 7px;border-radius:999px;
  background:var(--chip);font-size:11.5px;color:var(--mute)}
.chip.on{background:var(--accent);color:#fff}
.ev{display:grid;grid-template-columns:52px 86px 1fr;gap:9px;padding:3px 0;
  font-size:12.5px;border-bottom:1px solid var(--line)}
.ev:last-child{border-bottom:none}
.ev .t{color:var(--mute);font-variant-numeric:tabular-nums}
.empty{color:var(--mute);font-style:italic;font-size:13px}
footer{color:var(--mute);font-size:11.5px;padding:0 16px 24px;
  max-width:1180px;margin:0 auto}
.toggle{float:right;background:none;border:1px solid var(--line);
  border-radius:7px;color:var(--mute);cursor:pointer;padding:3px 9px;
  font:inherit;font-size:12px}
"""

JS = """
const D = window.__SESSIONS__;
const el = id => document.getElementById(id);
const f = (n,d=2) => (n==null||isNaN(n))?'-':Number(n).toFixed(d);
const sgn = n => n>0?'up':(n<0?'down':'');

function spark(pts){
  if(!pts.length) return '<div class="empty">no equity polls</div>';
  const v = pts.map(p=>p.equity), lo=Math.min(...v), hi=Math.max(...v);
  const W=560,H=54,r=(hi-lo)||1;
  const d = v.map((y,i)=>`${i?'L':'M'}${(i/(v.length-1||1)*W).toFixed(1)},${(H-(y-lo)/r*(H-8)-4).toFixed(1)}`).join('');
  const last=v[v.length-1], first=v[0];
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"
    style="width:100%;height:54px;overflow:visible" role="img"
    aria-label="Equity from ${first} to ${last}">
    <path d="${d}" fill="none" stroke="var(--${last>=first?'up':'down'})"
      stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>
    <div class="mute" style="display:flex;justify-content:space-between;font-size:11.5px">
      <span>${pts[0].t} · ${f(first)}</span>
      <span>low ${f(lo)} · high ${f(hi)}</span>
      <span>${pts[pts.length-1].t} · ${f(last)}</span></div>`;
}

function render(i){
  const s = D[i];
  document.querySelectorAll('nav button').forEach((b,j)=>
    b.setAttribute('aria-current', j===i ? 'true':'false'));
  const pnl = s.day_done ? s.day_done.pnl : null;
  const booked = s.trades.reduce((a,t)=>a+parseFloat(t.pnl||0),0);
  el('kpis').innerHTML = [
    ['targets', s.targets.length + s.rescreened.length],
    ['entries', s.entries.length],
    ['rejected', s.rejected_count],
    ['booked P&L', s.trades.length? f(booked) : '-'],
    ['day P&L', pnl==null?'-':f(pnl)],
    ['errors', s.errors],
  ].map(([k,v])=>{
    const cls = (k.includes('P&L') && v!=='-') ? sgn(parseFloat(v)) : (k==='errors'&&v>0?'down':'');
    return `<div class="kpi"><b class="${cls}">${v}</b><span>${k}</span></div>`;
  }).join('');

  el('status').innerHTML =
    `<span class="chip ${s.day_done?'on':''}">${s.day_done?'Day done':'no Day done'}</span>
     <span class="chip ${s.flatten_verified?'on':''}">${s.flatten_verified?'Flatten verified':'no flatten'}</span>
     ${s.errors?`<span class="chip" style="background:var(--down);color:#fff">${s.errors} errors</span>`:''}`;

  el('equity').innerHTML = spark(s.equity);

  const rv = Object.fromEntries(s.relvol.map(r=>[r.symbol,r.ratio]));
  const entered = new Set(s.entries.map(e=>e.symbol));
  const unconf = new Set(s.unconfirmed.map(u=>u.symbol));
  const rescr = new Set(s.rescreened.map(r=>r.symbol));
  const all = s.targets.concat(s.rescreened.filter(r=>!s.targets.some(t=>t.symbol===r.symbol))
      .map(r=>({symbol:r.symbol,bias:r.bias,gap_pct:null,sentiment:null,articles:null,score:null})));
  el('targets').innerHTML = all.length ? `<table><thead><tr>
      <th>symbol</th><th>bias</th><th class="num">gap %</th>
      <th class="num">sent</th><th class="num">relvol</th>
      <th class="num">score</th><th>outcome</th></tr></thead><tbody>` +
    all.sort((a,b)=>(b.score||0)-(a.score||0)).map(t=>{
      const out = entered.has(t.symbol) ? '<span class="chip on">entered</span>'
        : unconf.has(t.symbol) ? '<span class="chip warn">broke out, not confirmed</span>'
        : '<span class="mute">-</span>';
      return `<tr><td><b>${t.symbol}</b>${rescr.has(t.symbol)?' <span class="chip">re-screen</span>':''}</td>
        <td class="mute">${t.bias||'-'}</td>
        <td class="num ${sgn(t.gap_pct)}">${t.gap_pct==null?'-':f(t.gap_pct,1)}</td>
        <td class="num">${t.sentiment==null?'-':f(t.sentiment)}</td>
        <td class="num">${rv[t.symbol]?f(rv[t.symbol],2)+'x':'-'}</td>
        <td class="num">${t.score==null?'-':f(t.score,3)}</td><td>${out}</td></tr>`;
    }).join('') + '</tbody></table>' : '<div class="empty">no targets</div>';

  el('trades').innerHTML = s.trades.length ? `<table><thead><tr>
      <th>symbol</th><th>side</th><th class="num">qty</th><th class="num">entry</th>
      <th class="num">exit</th><th class="num">P&L</th><th class="num">%</th>
      </tr></thead><tbody>` + s.trades.map(t=>`<tr>
      <td><b>${t.symbol}</b></td><td class="mute">${t.side}</td>
      <td class="num">${t.qty}</td><td class="num">${t.entry_price}</td>
      <td class="num">${t.exit_price}</td>
      <td class="num ${sgn(parseFloat(t.pnl))}">${f(parseFloat(t.pnl))}</td>
      <td class="num ${sgn(parseFloat(t.pnl_pct))}">${f(parseFloat(t.pnl_pct))}</td>
      </tr>`).join('') + '</tbody></table>'
    : '<div class="empty">nothing booked for this date (trades are written at day end)</div>';

  const ev = []
    .concat(s.entries.map(e=>[e.t,'ENTER',`${e.side} ${e.symbol} qty=${e.qty} ref=${e.ref} SL=${e.sl} (${f(e.sl_pct,2)}%)`]))
    .concat(s.fills.map(x=>[x.t,'FILL',`${x.symbol} @ ${x.fill} (${f(x.slippage_pct,2)}% slip)${x.reanchored?' · re-anchored':' · ref-anchored stop'}`]))
    .concat(s.unconfirmed.map(u=>[u.t,'NO CONFIRM',`${u.symbol} broke out ${u.side}, rejected`]))
    .concat(s.trail.map(t=>[t.t,t.kind.toUpperCase(),t.line]))
    .concat(s.news.map(n=>[n.t,'NEWS',`${n.symbol} ${f(n.score)} · ${n.headline}`]))
    .concat(s.vetoes.map(v=>[v.t,'VETO',v.line]))
    .sort((a,b)=>a[0].localeCompare(b[0]));
  el('events').innerHTML = ev.length ? ev.map(([t,k,txt])=>
    `<div class="ev"><span class="t">${t.slice(0,5)}</span>
     <span class="mute">${k}</span><span>${txt.replace(/</g,'&lt;')}</span></div>`).join('')
    : '<div class="empty">no events</div>';
}

document.querySelectorAll('nav button').forEach((b,i)=>
  b.onclick = ()=>render(i));
el('themer').onclick = ()=>{
  const r = document.documentElement;
  const dark = getComputedStyle(r).getPropertyValue('--bg').trim().startsWith('#1');
  r.setAttribute('data-theme', dark ? 'light':'dark');
};
render(D.length-1);
"""


def build(sessions: list[dict]) -> str:
    nav = "".join(
        f'<button type="button"><div>{s["date"]}</div>'
        f'<div class="d">{len(s["entries"])} entry · '
        f'{s["rejected_count"]} rej</div></button>'
        for s in sessions)
    first, last = sessions[0]["date"], sessions[-1]["date"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ORB sessions</title><style>{CSS}</style></head><body>
<header>
  <button class="toggle" id="themer" type="button">theme</button>
  <h1>ORB session dashboard</h1>
  <div class="sub">{len(sessions)} sessions · {first} to {last} ·
    generated from logs and trade_history.csv</div>
</header>
<div class="wrap">
  <nav aria-label="Sessions">{nav}</nav>
  <main>
    <section><h2>Session</h2><div id="status"></div>
      <div style="height:10px"></div><div class="kpis" id="kpis"></div></section>
    <section><h2>Equity</h2><div id="equity"></div></section>
    <section><h2>What the screener saw</h2><div id="targets"></div></section>
    <section><h2>Booked trades</h2><div id="trades"></div></section>
    <section><h2>Timeline</h2><div id="events"></div></section>
  </main>
</div>
<footer>Research and operations view only. Nothing here feeds a trade
decision.</footer>
<script>window.__SESSIONS__={json.dumps(sessions, separators=(",", ":"))};
{JS}</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("-o", "--out", default="dashboard.html")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    sessions = to_dicts(load_sessions(a.limit))
    if not sessions:
        print("no logs found")
        return 1
    out = Path(a.out)
    out.write_text(build(sessions), encoding="utf-8")
    kb = out.stat().st_size / 1024
    print(f"{out} written: {len(sessions)} sessions, {kb:.0f} KB")
    if a.open:
        print(f"file://{out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
