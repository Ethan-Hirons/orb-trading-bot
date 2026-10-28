// Headless smoke test: run the dashboard's own JS against the real embedded
// data with a minimal DOM shim. Catches render errors that a syntax check
// cannot.
const fs = require('fs');
const html = fs.readFileSync('dashboard.html', 'utf8');

const m = html.match(/window\.__SESSIONS__=(\[.*?\]);\n/s);
if (!m) { console.error('FAIL: could not find embedded data'); process.exit(1); }
const data = JSON.parse(m[1]);
console.log(`data parses: ${data.length} sessions`);

const js = html.split('window.__SESSIONS__=')[1].replace(/^.*?;\n/s, '')
               .replace(/<\/script>[\s\S]*$/, '');

const store = {};
function fakeEl(id){
  return { set innerHTML(v){ store[id]=v; }, get innerHTML(){ return store[id]||''; },
           setAttribute(){}, onclick:null };
}
const buttons = data.map(()=>({ setAttribute(k,v){ this[k]=v; }, onclick:null }));
global.window = { __SESSIONS__: data };
global.document = {
  getElementById: id => fakeEl(id),
  querySelectorAll: sel => sel === 'nav button' ? buttons : [],
  documentElement: { setAttribute(){}, style:{} },
};
global.getComputedStyle = () => ({ getPropertyValue: () => '#fbfbfa' });

try { eval(js); } catch (e) { console.error('FAIL: JS threw:', e.message); process.exit(1); }
console.log('initial render ok');

// render every session, not just the default one
let bad = 0;
for (let i = 0; i < data.length; i++) {
  try { buttons[i].onclick ? buttons[i].onclick() : null; }
  catch (e) { console.error(`FAIL session ${data[i].date}: ${e.message}`); bad++; }
}
console.log(`rendered all ${data.length} sessions, ${bad} errors`);

for (const k of ['kpis','status','equity','targets','trades','events']) {
  const v = store[k] || '';
  console.log(`  ${k.padEnd(8)} ${v.length.toString().padStart(6)} chars  ${v?'ok':'EMPTY'}`);
  if (!v) bad++;
}
if (/undefined|NaN|\[object/.test(store.targets + store.kpis + store.trades)) {
  console.error('FAIL: undefined/NaN leaked into output'); bad++;
}
console.log(bad ? '\nSMOKE TEST FAILED' : '\nSMOKE TEST PASSED');
process.exit(bad ? 1 : 0);
