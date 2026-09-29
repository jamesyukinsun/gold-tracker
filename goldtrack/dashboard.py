"""Standalone HTML dashboard — self-contained, no network, no CDN.

Regenerate with:  python -m goldtrack dashboard

The snapshot is embedded as JSON; all rendering is vanilla JS with inline SVG
so the file works offline and can be emailed as-is.
"""
from __future__ import annotations

import json
import os

from . import snapshot as snap_mod

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gold Big-Player Tracker</title>
<style>
  :root{
    --bg:#0b0e13; --panel:#131822; --panel2:#171d29; --line:#232c3d;
    --fg:#e8edf7; --dim:#8b98b0; --dim2:#5f6b80;
    --up:#2fd07f; --down:#ff5f6d; --gold:#f5c542; --accent:#4aa8ff;
    --warn:#ffab3d;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:13px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
       padding:18px 20px 40px}
  h1{font-size:19px;margin:0;letter-spacing:-.2px}
  h2{font-size:13px;margin:0 0 10px;color:var(--dim);text-transform:uppercase;
     letter-spacing:.09em;font-weight:600}
  .top{display:flex;flex-wrap:wrap;gap:22px;align-items:flex-end;
       border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:16px}
  .sub{color:var(--dim);font-size:11.5px;margin-top:3px}
  .px{margin-left:auto;text-align:right}
  .px .big{font-size:30px;font-weight:600;letter-spacing:-1px;
           font-variant-numeric:tabular-nums}
  .px .ccy{font-size:13px;color:var(--dim);font-weight:400}
  .grid{display:grid;gap:14px}
  .g4{grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}
  .g2{grid-template-columns:repeat(auto-fit,minmax(400px,1fr))}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;
         padding:14px 16px}
  .panel.pad0{padding:14px 0 6px}
  table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
  th{text-align:left;color:var(--dim2);font-weight:600;font-size:10.5px;
     text-transform:uppercase;letter-spacing:.07em;padding:5px 8px;
     border-bottom:1px solid var(--line);white-space:nowrap}
  td{padding:6px 8px;border-bottom:1px solid rgba(35,44,61,.45)}
  tr:last-child td{border-bottom:none}
  td.r,th.r{text-align:right}
  td.lbl{white-space:nowrap}
  .up{color:var(--up)} .down{color:var(--down)}
  .gold{color:var(--gold)} .dim{color:var(--dim)} .acc{color:var(--accent)}
  .mono{font-family:ui-monospace,"SF Mono",Consolas,monospace;font-size:12px}
  .gauge{display:flex;align-items:center;gap:20px}
  .score{font-size:44px;font-weight:700;letter-spacing:-2px;line-height:1}
  .label{font-size:12px;color:var(--dim);margin-top:4px}
  .bar{height:6px;background:var(--panel2);border-radius:3px;overflow:hidden;
       margin-top:5px}
  .bar span{display:block;height:100%;border-radius:3px}
  .pill{display:inline-block;padding:2px 8px;border-radius:20px;font-size:10.5px;
        font-weight:600;letter-spacing:.04em}
  .p-crit{background:rgba(255,95,109,.16);color:var(--down)}
  .p-note{background:rgba(255,171,61,.16);color:var(--warn)}
  .p-info{background:rgba(74,168,255,.16);color:var(--accent)}
  .alert{border-left:2px solid var(--line);padding:8px 0 8px 12px;margin-bottom:8px}
  .alert.crit{border-color:var(--down)} .alert.note{border-color:var(--warn)}
  .alert .m{font-size:12.5px;margin-top:3px}
  .drv{display:flex;align-items:baseline;gap:8px;margin-bottom:9px}
  .drv .n{flex:0 0 172px;color:var(--dim);font-size:11.5px}
  .drv .v{flex:1}
  .drv .d{flex:0 0 250px;color:var(--dim2);font-size:11px;text-align:right}
  svg{display:block;width:100%;overflow:visible}
  .lg{display:flex;gap:14px;flex-wrap:wrap;color:var(--dim);font-size:11px;
      margin-top:8px}
  .lg i{display:inline-block;width:9px;height:9px;border-radius:2px;
        margin-right:5px;vertical-align:-1px}
  .note{color:var(--dim2);font-size:11px;margin-top:9px;line-height:1.55}
  .err{background:rgba(255,95,109,.09);border:1px solid rgba(255,95,109,.3);
       border-radius:8px;padding:9px 12px;margin-bottom:14px;font-size:11.5px;
       color:#ffc9cd}
</style></head><body>

<div class="top">
  <div>
    <h1>Gold — Big-Player Tracker</h1>
    <div class="sub" id="gen"></div>
  </div>
  <div class="px">
    <div class="big mono" id="spot">—</div>
    <div class="sub" id="spotsub"></div>
  </div>
</div>
<div id="errs"></div>
<div id="body"></div>
<div class="note" style="margin-top:20px;border-top:1px solid var(--line);
     padding-top:12px">
  <b>Sources &amp; honesty note.</b> Positioning from the CFTC Commitments of
  Traders (weekly, released Friday 15:30 ET — report date shown above; this is
  the only legal disclosure of large-trader books). ETF tonnage from the World
  Gold Council. Shanghai prices from sge.com.cn; London benchmark from
  prices.lbma.org.uk; spot from api.gold-api.com; regional venues via Yahoo
  Finance. COMEX real-time depth and order-book identity require a CME data
  licence and are not available publicly — the tape panel infers size from
  volume anomalies, not from identified orders. COT reports name categories,
  never firms. 0 on the index = distribution, 100 = accumulation.
</div>

<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const $ = (h) => document.createDocumentFragment();
function el(tag, cls, txt){const e=document.createElement(tag);
  if(cls)e.className=cls; if(txt!=null)e.textContent=txt; return e;}
function n(x,d=2){ if(x==null||isNaN(x))return '—';
  return Number(x).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});}
function i0(x){ if(x==null||isNaN(x))return '—';
  return Number(x).toLocaleString('en-US',{maximumFractionDigits:0});}
function sgn(x,d=2){ if(x==null||isNaN(x))return '—';
  return (x>0?'+':'')+n(x,d);}
function cls(x){ return x>0?'up':(x<0?'down':''); }
function panel(title){ const p=el('div','panel'); if(title)p.appendChild(el('h2',null,title)); return p;}

/* ---------- header ---------- */
document.getElementById('gen').textContent =
  'generated ' + (D.generated_at||'').replace('T',' ').slice(0,19) + ' UTC · basis: '
  + ((D.positions&&D.positions.basis_label)||D.basis||'');

const B = D.bullion||{}, SP = B.SPOT||{}, CX = B.COMEX||{};
if(SP.usd_oz||CX.usd_oz){
  document.getElementById('spot').innerHTML =
    '<span class="gold">' + n(SP.usd_oz||CX.usd_oz) + '</span>'
    + '<span class="ccy"> USD/oz spot</span>';
  const bits=[];
  if(CX.usd_oz) bits.push('COMEX '+n(CX.usd_oz));
  if(B.SGE&&B.SGE.usd_oz) bits.push('Shanghai '+n(B.SGE.usd_oz));
  if(B.LBMA_PM&&B.LBMA_PM.usd_oz) bits.push('LBMA PM '+n(B.LBMA_PM.usd_oz));
  document.getElementById('spotsub').textContent = bits.join('  ·  ');
}
const errs = D.errors||{};
if(Object.keys(errs).length){
  const box=el('div','err');
  box.appendChild(el('b',null,'Feeds unavailable this run: '));
  box.appendChild(document.createTextNode(
    Object.entries(errs).map(([k,v])=>k+' ('+v+')').join(' · ')));
  document.getElementById('errs').appendChild(box);
}

/* ---------- svg helpers ---------- */
function lineChart(series, opts){
  // series: [{name, pts:[[label,value]], color}]
  const o = Object.assign({h:150, pad:[8,10,22,52], yfmt:n, zero:false}, opts||{});
  const w = 900, [pt,pr,pb,pl]=o.pad;
  const all=series.flatMap(s=>s.pts.map(p=>p[1]));
  if(!all.length) return el('div','dim','no data');
  let lo=Math.min(...all), hi=Math.max(...all);
  if(o.zero){ lo=Math.min(lo,0); hi=Math.max(hi,0); }
  const sp=(hi-lo)||1; lo-=sp*.06; hi+=sp*.06;
  const N=Math.max(...series.map(s=>s.pts.length));
  const X=i=>pl+(w-pl-pr)*(N>1?i/(N-1):.5);
  const Y=v=>pt+(o.h-pt-pb)*(1-(v-lo)/(hi-lo));
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 '+w+' '+o.h);
  svg.setAttribute('style','height:'+(o.h+8)+'px');
  const mk=(t,a)=>{const e=document.createElementNS('http://www.w3.org/2000/svg',t);
    for(const k in a)e.setAttribute(k,a[k]); return e;};
  for(let g=0;g<=4;g++){
    const v=lo+(hi-lo)*g/4, y=Y(v);
    svg.appendChild(mk('line',{x1:pl,x2:w-pr,y1:y,y2:y,stroke:'#232c3d','stroke-width':1}));
    const t=mk('text',{x:pl-7,y:y+3.5,fill:'#5f6b80','font-size':10,'text-anchor':'end'});
    t.textContent=o.yfmt(v); svg.appendChild(t);
  }
  if(lo<0&&hi>0) svg.appendChild(mk('line',{x1:pl,x2:w-pr,y1:Y(0),y2:Y(0),
    stroke:'#3d4a63','stroke-width':1,'stroke-dasharray':'3 3'}));
  series.forEach(s=>{
    const d=s.pts.map((p,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(p[1]).toFixed(1)).join(' ');
    svg.appendChild(mk('path',{d:d,fill:'none',stroke:s.color,
      'stroke-width':s.w||1.7,'stroke-linejoin':'round'}));
  });
  const L=series[0].pts.map(p=>p[0]);
  [0,Math.floor(N/2),N-1].forEach(k=>{
    if(k<0||k>=L.length)return;
    const t=mk('text',{x:X(k),y:o.h-6,fill:'#5f6b80','font-size':10,
      'text-anchor':k===0?'start':(k===N-1?'end':'middle')});
    t.textContent=String(L[k]).slice(0,7); svg.appendChild(t);
  });
  return svg;
}
function legend(items){
  const d=el('div','lg');
  items.forEach(it=>{ const s=el('span'); const a=el('i');
    a.style.background=it.color; s.appendChild(a);
    s.appendChild(document.createTextNode(it.name)); d.appendChild(s); });
  return d;
}

/* ---------- body ---------- */
const root=document.getElementById('body');
const g1=el('div','grid g2'); root.appendChild(g1);

/* index */
(function(){
  const X=D.index; const p=panel('Big-player pressure index');
  if(!X||X.score==null){ p.appendChild(el('div','dim','insufficient data'));
    g1.appendChild(p); return; }
  const col = X.score>=65?'var(--up)':X.score>=55?'#8fd6a8':
              X.score>45?'var(--gold)':X.score>35?'#ffa07a':'var(--down)';
  const g=el('div','gauge');
  const left=el('div');
  const big=el('div','score'); big.innerHTML='<span style="color:'+col+'">'
    +Math.round(X.score)+'</span><span class="dim" style="font-size:16px">/100</span>';
  left.appendChild(big);
  const lb=el('div','label'); lb.innerHTML='<b style="color:'+col+'">'+X.label+'</b>';
  left.appendChild(lb);
  const track=el('div','bar'); const fill=el('span');
  fill.style.width=X.score+'%'; fill.style.background=col; track.appendChild(fill);
  left.appendChild(track);
  g.appendChild(left); p.appendChild(g);

  (X.components||[]).forEach(c=>{
    const d=el('div','drv');
    d.appendChild(el('div','n',c.name));
    const mid=el('div','v');
    const v=Math.round(c.value*100);
    const col2=v>15?'var(--up)':v<-15?'var(--down)':'var(--dim)';
    const tr=el('div','bar'); const fl=el('span');
    fl.style.width=Math.abs(v)+'%'; fl.style.background=col2;
    fl.style.marginLeft=v<0?'auto':'0';
    tr.appendChild(fl); mid.appendChild(tr);
    const w=el('div','dim','weight '+Math.round(c.weight*100)+'%');
    w.style.fontSize='10.5px'; mid.appendChild(w);
    d.appendChild(mid);
    d.appendChild(el('div','d',c.detail));
    p.appendChild(d);
  });
  p.appendChild(el('div','note',
    'Each driver is scaled to −100…+100, then blended by weight. It answers one '
    +'question: on balance, is size accumulating or distributing metal?'));
  g1.appendChild(p);
})();

/* positions */
(function(){
  const P=D.positions; const p=panel('Who holds what — CFTC Commitments of Traders');
  if(!P||!P.groups){ p.appendChild(el('div','dim','unavailable')); g1.appendChild(p); return; }
  const meta=el('div','sub');
  meta.style.marginBottom='10px';
  meta.textContent='report date '+P.report_date+' (released Friday 15:30 ET) · '
    +i0(P.open_interest)+' contracts = '+n(P.open_interest_tonnes,0)+' t · '
    +(P.total_traders||'?')+' reporting traders';
  p.appendChild(meta);
  const t=el('table'); const hr=el('tr');
  ['Group','Long','Short','Spread','Net','Δ week','Z (3y)','Pctl']
    .forEach((h,i)=>hr.appendChild(el('th',i?'r':null,h)));
  t.appendChild(hr);
  ['swap_dealer','managed_money','other_rept','prod_merc'].forEach(k=>{
    const g=P.groups[k]; if(!g)return;
    const r=el('tr');
    r.appendChild(el('td','lbl',g.label));
    r.appendChild(el('td','r mono',i0(g.long)));
    r.appendChild(el('td','r mono',i0(g.short)));
    r.appendChild(el('td','r mono',g.spread?i0(g.spread):'—'));
    r.appendChild(el('td','r mono '+(g.net>0?'up':'down'),sgn(g.net,0)));
    r.appendChild(el('td','r mono '+cls(g.change),sgn(g.change,0)));
    r.appendChild(el('td','r mono',g.net_zscore==null?'—':sgn(g.net_zscore,2)));
    r.appendChild(el('td','r mono',g.net_percentile==null?'—'
      :Math.round(g.net_percentile)+'%'));
    t.appendChild(r);
  });
  p.appendChild(t);
  p.appendChild(el('div','note','Net = outright long − short; spreads are counted on '
    +'neither side. Z is measured against the last three years — above ~+2 or below '
    +'~−2 means positioning sits at a historical extreme, which is where turns begin.'));

  const C=P.concentration||{};
  if(Object.keys(C).length){
    const t2=el('table'); const h2=el('tr');
    ['Concentration','Share of open interest'].forEach(h=>h2.appendChild(el('th',null,h)));
    t2.appendChild(h2);
    [['4 largest longs','gross_4_long'],['4 largest shorts','gross_4_short'],
     ['8 largest longs','gross_8_long'],['8 largest shorts','gross_8_short']]
      .forEach(([lab,key])=>{
        if(C[key]==null)return;
        const r=el('tr'); r.appendChild(el('td',null,lab));
        const cell=el('td'); cell.appendChild(document.createTextNode(n(C[key],1)+'%'));
        const bar=el('div','bar'); const f=el('span');
        f.style.width=Math.min(100,C[key]*1.6)+'%'; f.style.background='var(--gold)';
        bar.appendChild(f); cell.appendChild(bar); r.appendChild(cell); t2.appendChild(r);
      });
    const wrap=el('div'); wrap.style.marginTop='12px';
    wrap.appendChild(el('h2',null,'Concentration — the few who move it'));
    wrap.appendChild(t2); p.appendChild(wrap);
  }
  g1.appendChild(p);
})();

/* net positioning chart */
(function(){
  const P=D.positions; const p=panel('Large-trader net positioning — three years');
  if(!P||!P.net_history){ p.appendChild(el('div','dim','unavailable')); g1.appendChild(p); return; }
  const defs=[['managed_money','Managed money','#4aa8ff'],
              ['swap_dealer','Swap dealers','#f5c542'],
              ['prod_merc','Producer / merchant','#2fd07f'],
              ['other_rept','Other reportables','#b07cff']];
  const series=defs.filter(d=>P.net_history[d[0]]&&P.net_history[d[0]].length)
    .map(d=>({name:d[1],color:d[2],pts:P.net_history[d[0]]}));
  if(!series.length){ p.appendChild(el('div','dim','unavailable')); g1.appendChild(p); return; }
  p.appendChild(lineChart(series,{h:170,zero:true,yfmt:v=>i0(v/1000)+'k'}));
  p.appendChild(legend(series.map(s=>({name:s.name,color:s.color}))));
  p.appendChild(el('div','note','Swap dealers are structurally short — they warehouse '
    +'the world\'s hedging flow. When their short shrinks, they are less willing to cap '
    +'the market. Managed money is the momentum crowd; extremes mark turns.'));
  g1.appendChild(p);
})();

/* second row */
const g2=el('div','grid g2'); root.appendChild(g2);

/* ETF */
(function(){
  const E=D.etf; const p=panel('ETF tonnage — institutional allocation');
  if(!E||!E.history){ p.appendChild(el('div','dim','unavailable')); g2.appendChild(p); return; }
  const meta=el('div','sub'); meta.style.marginBottom='8px';
  meta.innerHTML='<b>'+n(E.total_tonnes,1)+' t</b> held · week to '+E.as_of
    +' · <span class="'+cls(E.wow_tonnes)+'">'+sgn(E.wow_tonnes,2)+' t</span>'
    +' · 13w '+sgn(E.flow_13w,1)+' t · 52w '+sgn(E.flow_52w,1)+' t';
  p.appendChild(meta);
  if(E.history.length>3)
    p.appendChild(lineChart([{name:'tonnes',color:'#f5c542',
      pts:E.history.map(h=>[h.date,h.total])}],{h:130,yfmt:v=>i0(v)+'t',zero:false}));
  if(E.flow_history&&E.flow_history.length>3)
    p.appendChild(lineChart([{name:'weekly flow',color:'#4aa8ff',
      pts:E.flow_history.map(h=>[h.date,h.total])}],{h:110,zero:true,yfmt:v=>sgn(v,0)+'t'}));
  p.appendChild(legend([{name:'total tonnes held',color:'#f5c542'},
                        {name:'weekly flow (t)',color:'#4aa8ff'}]));
  p.appendChild(el('div','note','A trust must publish its tonnage, so this is real '
    +'metal, not a proxy. It is the cleanest public read on whether institutions are '
    +'allocating or liquidating.'));
  g2.appendChild(p);
})();

/* premium */
(function(){
  const H=(D.premium_history)||[]; const p=panel('Shanghai premium — physical bid from Asia');
  const cur=D.premium;
  if(!H.length){ p.appendChild(el('div','dim','unavailable')); g2.appendChild(p); return; }
  if(cur){
    const meta=el('div','sub'); meta.style.marginBottom='8px';
    meta.innerHTML='now <b class="'+cls(cur.premium_usd)+'">'+sgn(cur.premium_usd,2)
      +' USD/oz</b> ('+sgn(cur.premium_pct,2)+'%) vs '+cur.reference;
    p.appendChild(meta);
  }
  p.appendChild(lineChart([{name:'premium',color:'#2fd07f',
    pts:H.map(r=>[r.date,r.premium_usd])}],{h:130,zero:true,yfmt:v=>sgn(v,0)}));
  p.appendChild(el('div','note','SGE Au99.99 converted to USD/oz against the London PM '
    +'auction of the same date. SGE closes 15:30 Shanghai and the auction fixes 15:00 '
    +'London, so read the trend, not the level. A persistent positive premium means '
    +'Chinese buyers are paying up for physical metal.'));
  g2.appendChild(p);
})();

/* third row */
const g3=el('div','grid g2'); root.appendChild(g3);

/* venues */
(function(){
  const p=panel('Price by venue — one clock, all major markets');
  const t=el('table'); const hr=el('tr');
  ['Venue','USD/oz','Native','Change','Feed'].forEach((h,i)=>hr.appendChild(el('th',i?'r':null,h)));
  t.appendChild(hr);
  const order=['COMEX','COMEX_MICRO','SPOT','SGE','LBMA_PM'];
  order.forEach(k=>{ const v=(D.bullion||{})[k]; if(!v)return;
    const r=el('tr'); r.appendChild(el('td',null,k));
    if(v.error){ r.appendChild(el('td','r dim','err')); r.appendChild(el('td','r dim',v.error.slice(0,28)));
      r.appendChild(el('td','r','')); r.appendChild(el('td','dim',''));
    } else {
      r.appendChild(el('td','r mono gold',n(v.usd_oz)));
      r.appendChild(el('td','r mono dim',n(v.native)+' '+v.unit));
      r.appendChild(el('td','r mono '+cls(v.change_pct),
        v.change_pct==null?'—':sgn(v.change_pct,2)+'%'));
      r.appendChild(el('td','dim',v.note||''));
    }
    t.appendChild(r); });
  (D.quotes||[]).forEach(q=>{
    const r=el('tr'); r.appendChild(el('td',null,q.venue));
    r.appendChild(el('td','r dim','—'));
    r.appendChild(el('td','r mono',n(q.price)+' '+q.currency));
    r.appendChild(el('td','r mono '+cls(q.change_pct),
      q.change_pct==null?'—':sgn(q.change_pct,2)+'%'));
    r.appendChild(el('td','dim',(q.delay_note||'').split('(')[0].trim()));
    t.appendChild(r); });
  p.appendChild(t);
  const d=D.dispersion||{};
  const cx=(D.bullion||{}).COMEX||{}, sp=(D.bullion||{}).SPOT||{};
  let txt='';
  if(cx.usd_oz&&sp.usd_oz){
    txt+='COMEX basis over spot '+sgn(cx.usd_oz-sp.usd_oz,2)+' USD/oz — term structure '
      +'(carry, storage and rates), not an arbitrage. Backwardation signals physical '
      +'tightness. ';
  }
  if(d.spread_usd!=null){
    txt+='Spread across live bullion feeds '+n(d.spread_usd,2)+' USD ('
      +n(d.spread_pct,2)+'%), '+d.low_venue+' → '+d.high_venue+'.';
  }
  if(txt) p.appendChild(el('div','note',txt));
  g3.appendChild(p);
})();

/* tape */
(function(){
  const T=D.tape; const p=panel('Real-time tape — large-print footprints');
  if(!T||T.error){ p.appendChild(el('div','dim',(T&&T.error)||'unavailable'));
    g3.appendChild(p); return; }
  const meta=el('div','sub'); meta.style.marginBottom='8px';
  meta.innerHTML='<b>'+T.symbol+'</b> · '+T.window+' · session volume '+i0(T.session_volume)
    +' · median 1-min bar '+i0(T.typical_volume)+' · <b>'+T.flagged_count
    +'</b> anomalous bars'
    +(T.buy_ratio!=null?' · <span class="'+(T.buy_ratio>.5?'up':'down')+'">'
      +Math.round(T.buy_ratio*100)+'% of flagged volume on the bid</span>':'');
  p.appendChild(meta);
  const t=el('table'); const hr=el('tr');
  ['Time UTC','Lots','vs median','Z','Price','Move','Side']
    .forEach((h,i)=>hr.appendChild(el('th',i?'r':null,h)));
  t.appendChild(hr);
  (T.spikes||[]).forEach(s=>{
    const r=el('tr'); r.appendChild(el('td','mono',(s.ts||'').slice(11,16)));
    r.appendChild(el('td','r mono',i0(s.volume)));
    r.appendChild(el('td','r mono',n(s.volume_multiple,1)+'x'));
    r.appendChild(el('td','r mono',sgn(s.volume_z,1)));
    r.appendChild(el('td','r mono',n(s.close)));
    r.appendChild(el('td','r mono '+cls(s.move),sgn(s.move,1)));
    r.appendChild(el('td','r '+(s.direction==='buy'?'up':'down'),s.direction));
    t.appendChild(r); });
  p.appendChild(t);
  p.appendChild(el('div','note','A volume spike proves size traded; the price response '
    +'reveals who was aggressive. Public feeds do not identify counterparties — this is '
    +'inference from the tape, and it is labelled as such.'));
  g3.appendChild(p);
})();

/* alerts */
(function(){
  const A=D.alerts||[]; const p=panel('Alerts ('+A.length+')');
  if(!A.length){ p.appendChild(el('div','dim','Nothing crossed its threshold — '
    +'positioning, flow, premium and tape are all within normal ranges.'));
    document.getElementById('body').appendChild(p); return; }
  A.forEach(a=>{
    const c=a.level==='CRITICAL'?'crit':(a.level==='NOTABLE'?'note':'');
    const d=el('div','alert '+c);
    const head=el('span','pill '+(a.level==='CRITICAL'?'p-crit':
      a.level==='NOTABLE'?'p-note':'p-info'),a.level);
    d.appendChild(head);
    const code=el('span','dim'); code.style.fontSize='10.5px';
    code.style.marginLeft='8px'; code.textContent=a.code;
    d.appendChild(code);
    d.appendChild(el('div','m',a.message));
    p.appendChild(d); });
  root.appendChild(p);
})();
</script></body></html>
"""


def write(snap_jsonable: dict, out_path: str) -> str:
    html = TEMPLATE.replace("__DATA__", json.dumps(snap_jsonable))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_path


def build(out_path: str, basis: str = "combined", tape_symbol: str = "GC=F",
          premium_days: int = 40, verbose: bool = True) -> tuple[str, dict]:
    snap = snap_mod.build(basis=basis, tape_symbol=tape_symbol,
                          premium_days=premium_days, verbose=verbose)
    js = snap_mod.to_jsonable(snap)
    path = write(js, out_path)
    return path, js
