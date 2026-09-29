"""Live web monitor.

A tiny stdlib HTTP server (no framework, no CDN) that serves a page which
polls `/api/snapshot` on a timer and updates the DOM in place. Numbers flash
when they change, so the page reads as a live feed rather than a chart that
happens to reload.

    python -m goldtrack serve                 # http://127.0.0.1:8787
    python -m goldtrack serve --port 9000 --refresh 2

Endpoints:
    /                 the live page
    /api/snapshot     the full state as JSON
    /api/alerts       recent alerts only
    /health           plain-text one-liner, for external monitoring
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .engine import LiveEngine, jsonable

PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gold Live Monitor</title>
<style>
  :root{
    --bg:#080b10; --panel:#11161f; --panel2:#161d29; --line:#222b3b;
    --fg:#e9eef8; --dim:#8b98b0; --dim2:#5c687d;
    --up:#25d07a; --down:#ff5566; --gold:#f5c542; --acc:#4aa8ff; --warn:#ffab3d;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
    font:13px/1.45 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
    padding:14px 18px 36px}
  h1{font-size:17px;margin:0;letter-spacing:-.2px}
  h2{font-size:11.5px;margin:0 0 9px;color:var(--dim);text-transform:uppercase;
     letter-spacing:.1em;font-weight:600}
  .top{display:flex;flex-wrap:wrap;gap:18px;align-items:flex-end;
       border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:14px}
  .sub{color:var(--dim);font-size:11px;margin-top:3px}
  .px{margin-left:auto;text-align:right}
  .px .big{font-size:32px;font-weight:600;letter-spacing:-1.2px;
           font-variant-numeric:tabular-nums;transition:color .25s}
  .px .ccy{font-size:12px;color:var(--dim);font-weight:400}
  .grid{display:grid;gap:13px}
  .g2{grid-template-columns:repeat(auto-fit,minmax(420px,1fr))}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;
         padding:13px 15px}
  table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
  th{text-align:left;color:var(--dim2);font-weight:600;font-size:10px;
     text-transform:uppercase;letter-spacing:.07em;padding:4px 7px;
     border-bottom:1px solid var(--line);white-space:nowrap}
  td{padding:5px 7px;border-bottom:1px solid rgba(34,43,59,.4);white-space:nowrap}
  tr:last-child td{border-bottom:none}
  td.r,th.r{text-align:right}
  .up{color:var(--up)} .down{color:var(--down)} .gold{color:var(--gold)}
  .dim{color:var(--dim)} .dim2{color:var(--dim2)} .acc{color:var(--acc)}
  .mono{font-family:ui-monospace,"SF Mono",Consolas,monospace;font-size:12px}
  .flash{animation:fl .7s ease-out}
  @keyframes fl{0%{background:rgba(74,168,255,.28)}100%{background:transparent}}
  .dot{display:inline-block;width:7px;height:7px;border-radius:50%;
       margin-right:5px;vertical-align:1px}
  .score{font-size:40px;font-weight:700;letter-spacing:-2px;line-height:1}
  .bar{height:6px;background:var(--panel2);border-radius:3px;overflow:hidden;margin-top:4px}
  .bar span{display:block;height:100%;border-radius:3px;transition:width .4s}
  .pill{display:inline-block;padding:2px 7px;border-radius:20px;font-size:10px;
        font-weight:600}
  .p-crit{background:rgba(255,85,102,.16);color:var(--down)}
  .p-note{background:rgba(255,171,61,.16);color:var(--warn)}
  .p-info{background:rgba(74,168,255,.16);color:var(--acc)}
  .alert{border-left:2px solid var(--line);padding:7px 0 7px 11px;margin-bottom:7px}
  .alert.crit{border-color:var(--down)} .alert.note{border-color:var(--warn)}
  .alert .m{font-size:12px;margin-top:2px;white-space:normal}
  .drv{display:grid;grid-template-columns:170px 1fr 250px;gap:9px;
       align-items:center;margin-bottom:8px;font-size:11.5px}
  .note{color:var(--dim2);font-size:10.5px;margin-top:8px;line-height:1.5}
  .lg{display:flex;gap:13px;flex-wrap:wrap;color:var(--dim);font-size:10.5px;margin-top:6px}
  .lg i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px}
  svg{display:block;width:100%}
  .stale{color:var(--warn)}
</style></head><body>

<div class="top">
  <div>
    <h1>Gold — Live Monitor</h1>
    <div class="sub" id="meta">connecting…</div>
    <div class="sub" id="timer"></div>
  </div>
  <div class="px">
    <div class="big mono" id="spot">—</div>
    <div class="sub" id="spotsub"></div>
  </div>
</div>

<div class="grid g2" id="row1">
  <div class="panel" id="p-index"><h2>Big-player pressure index</h2></div>
  <div class="panel" id="p-prices"><h2>Live prices</h2></div>
</div>
<div class="grid g2" id="row2">
  <div class="panel" id="p-spot"><h2>Spot &amp; COMEX — session trend</h2></div>
  <div class="panel" id="p-struct"><h2>Cross-venue structure</h2></div>
</div>
<div class="panel" id="p-candles" style="margin-bottom:13px"><h2>Spot — 1-minute chart</h2></div>
<div class="grid g2" id="row3">
  <div class="panel" id="p-pos"><h2>Who holds what — CFTC</h2></div>
  <div class="panel" id="p-tape"><h2>Real-time tape</h2></div>
</div>
<div class="panel" id="p-feeds" style="margin-bottom:13px"><h2>Feed health</h2></div>
<div class="panel" id="p-alerts"><h2>Alerts</h2></div>
<div class="note" id="foot"></div>

<script>
const REFRESH = __REFRESH__;
let prev = {};

function n(x,d=2){ if(x==null||isNaN(x))return '—';
  return Number(x).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});}
function i0(x){ if(x==null||isNaN(x))return '—';
  return Number(x).toLocaleString('en-US',{maximumFractionDigits:0});}
function sgn(x,d=2){ if(x==null||isNaN(x))return '—';
  return (x>0?'+':'')+n(x,d);}
function cls(x){ return x>0?'up':(x<0?'down':''); }
function esc(s){ return String(s==null?'':s).replace(/[&<>]/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function age(s){ if(s==null)return '—';
  if(s<90)return Math.round(s)+'s';
  if(s<5400)return Math.round(s/60)+'m';
  if(s<172800)return (s/3600).toFixed(1)+'h';
  return (s/86400).toFixed(1)+'d'; }
function tfmt(iso){ const d=new Date(iso);
  return ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2); }

/* set innerHTML and flash if the visible text changed */
function put(id, html, key){
  const el = document.getElementById(id);
  if(!el) return;
  const sig = html;
  if(prev[key||id] !== sig){
    el.innerHTML = html;
    const host = el.closest('.panel') || el.parentElement;
    if(host){ host.classList.remove('flash'); void host.offsetWidth;
      host.classList.add('flash'); }
    prev[key||id] = sig;
  }
}

function sparkline(values, w, h, color){
  if(!values || values.length<2) return '';
  const lo=Math.min(...values), hi=Math.max(...values), sp=(hi-lo)||1;
  const pts = values.map((v,i)=>{
    const x = (i/(values.length-1))*(w-2)+1;
    const y = h-2 - ((v-lo)/sp)*(h-6);
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(' ');
  const last = values[values.length-1];
  const up = last >= values[0];
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="height:${h}px">
    <polyline points="${pts}" fill="none" stroke="${up?'var(--up)':'var(--down)'}"
      stroke-width="1.6" stroke-linejoin="round"/></svg>`;
}

/* Multi-series line chart for the weekly COT net-positioning series.

   All four groups share one y-scale on purpose: swap dealers are structurally
   short by an order of magnitude more than anyone else, and that asymmetry IS
   the story. A per-series scale would normalise it away. Text lives in HTML
   around the SVG, not inside it, so the viewBox scaling cannot distort it. */
function multiLine(series, W, H, yfmt){
  const all = [];
  series.forEach(s=>s.pts.forEach(v=>all.push(v)));
  if(!all.length) return '';
  let lo = Math.min(...all), hi = Math.max(...all);
  const sp = (hi-lo)||1; lo -= sp*0.06; hi += sp*0.06;
  const padR = 56, padL = 2;
  const N = Math.max(...series.map(s=>s.pts.length));
  const X = i => padL + (W-padL-padR)*(N>1 ? i/(N-1) : 0.5);
  const Y = v => 8 + (H-16)*(1-(v-lo)/(hi-lo));
  let g = '';
  for(let k=0;k<=4;k++){
    const y = 8 + (H-16)*k/4;
    g += '<line x1="'+padL+'" x2="'+(W-padR)+'" y1="'+y.toFixed(1)+'" y2="'+
         y.toFixed(1)+'" stroke="var(--line)" stroke-width="1"/>';
    g += '<text x="'+(W-padR+6)+'" y="'+(y+3).toFixed(1)+
         '" fill="var(--dim2)" font-size="10">'+yfmt(hi-(hi-lo)*k/4)+'</text>';
  }
  if(lo < 0 && hi > 0){
    const y = Y(0);
    g += '<line x1="'+padL+'" x2="'+(W-padR)+'" y1="'+y.toFixed(1)+'" y2="'+
         y.toFixed(1)+'" stroke="#3d4a63" stroke-width="1" stroke-dasharray="3 3"/>';
  }
  series.forEach(s=>{
    const pts = s.pts.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)).join(' ');
    g += '<polyline points="'+pts+'" fill="none" stroke="'+s.color+
         '" stroke-width="1.8" stroke-linejoin="round"/>';
  });
  return '<svg viewBox="0 0 '+W+' '+H+'" style="height:'+H+'px">'+g+'</svg>';
}

/* Spot 1-minute chart.

   Candlesticks are the wrong instrument here. gold-api prints roughly one new
   price per minute, so within any minute open==high==low==close and every
   candle degenerates into a flat dash. What the data does support is a
   per-minute high-low envelope plus the close, drawn as a step line because
   the series is a sequence of discrete observations, not a continuous path.

   preserveAspectRatio="none" so the timeline fills the panel; all text lives
   in HTML around the SVG so it is not stretched by that scaling. */
function spotChart(bars, ref, W, H){
  if(!bars || bars.length < 2) return '';
  const vals = [];
  bars.forEach(b=>{ vals.push(b.high, b.low); });
  (ref||[]).forEach(p=> vals.push(p.close));
  let lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  const pad = (hi-lo)||1;
  lo -= pad*0.08; hi += pad*0.08;
  const n = bars.length;
  const X = i => (i+0.5)*(W/n);
  const Y = v => H - 6 - ((v-lo)/(hi-lo))*(H-12);
  let s = '';

  for(let g=0; g<=3; g++){
    const y = 6 + (H-12)*g/3;
    s += '<line x1="0" x2="'+W+'" y1="'+y.toFixed(1)+'" y2="'+y.toFixed(1)+
         '" stroke="var(--line)" stroke-width="1"/>';
  }

  // COMEX GC 1-min closes — same axis, no dual scale, so the basis is visible
  if(ref && ref.length>1){
    const pts = ref.map((p,i)=>{
      const j = Math.min(n-1, Math.round(i*(n-1)/(ref.length-1)));
      return ((j+0.5)*(W/n)).toFixed(1)+','+Y(p.close).toFixed(1);
    }).join(' ');
    s += '<polyline points="'+pts+'" fill="none" stroke="var(--acc)" '+
         'stroke-width="1" stroke-dasharray="3 3" opacity="0.7"/>';
  }

  // per-minute high-low envelope
  const bw = Math.max(1.5, (W/n)*0.72);
  bars.forEach((b,i)=>{
    const x = X(i), y1 = Y(b.high), y2 = Y(b.low);
    s += '<rect x="'+(x-bw/2).toFixed(1)+'" y="'+y1.toFixed(1)+'" width="'+bw.toFixed(1)+
         '" height="'+Math.max(1, y2-y1).toFixed(1)+'" fill="var(--gold)" opacity="0.22"/>';
  });

  // spot close
  const pts = bars.map((b,i)=>X(i).toFixed(1)+','+Y(b.close).toFixed(1)).join(' ');
  s += '<polyline points="'+pts+'" fill="none" stroke="var(--gold)" stroke-width="2" '+
       'stroke-linejoin="round"/>';

  const last = bars[n-1];
  s += '<line x1="0" x2="'+W+'" y1="'+Y(last.close).toFixed(1)+'" y2="'+
       Y(last.close).toFixed(1)+'" stroke="var(--fg)" stroke-width="1" '+
       'stroke-dasharray="1 4" opacity="0.45"/>';

  return '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" '+
         'style="height:'+H+'px">'+s+'</svg>';
}

function render(D){
  const B=D.bullion||{}, P=D.premium, BX=D.basis, IDX=D.index||{};
  const S=B.SPOT||{}, C=B.COMEX||{};

  // header
  const up = (C.usd_oz||0) >= (C.usd_oz||0);
  document.getElementById('meta').innerHTML =
    'uptime '+Math.floor(D.uptime_s/60)+'m '+Math.floor(D.uptime_s%60)+'s · '+
    'ticks '+i0(D.tick_count)+' · alerts '+i0(D.alert_count)+' · '+
    '<span id="conn" class="acc">live</span> · '+
    esc(D.basis_label||'');
  const spotEl = document.getElementById('spot');
  spotEl.innerHTML = '<span class="gold">'+n(S.usd_oz)+'</span>'+
    '<span class="ccy"> USD/oz spot</span>';
  const bits=[];
  if(C.usd_oz) bits.push('COMEX '+n(C.usd_oz));
  if(B.SGE&&B.SGE.usd_oz) bits.push('Shanghai '+n(B.SGE.usd_oz));
  if(B.LBMA_PM&&B.LBMA_PM.usd_oz) bits.push('LBMA PM '+n(B.LBMA_PM.usd_oz));
  document.getElementById('spotsub').textContent = bits.join('  ·  ');

  // index
  let ih='';
  if(IDX.score!=null){
    const sc=IDX.score;
    const col = sc>=65?'var(--up)':sc>=55?'#8fd6a8':sc>45?'var(--gold)':
                sc>35?'#ffa07a':'var(--down)';
    ih += '<div class="score" style="color:'+col+'">'+Math.round(sc)+
          '<span class="dim" style="font-size:15px">/100</span></div>'+
          '<div class="sub" style="margin-bottom:9px"><b style="color:'+col+'">'+
          esc(IDX.label)+'</b></div>';
    (IDX.components||[]).forEach(c=>{
      const v=Math.round(c.value*100);
      const cl = v>15?'var(--up)':v<-15?'var(--down)':'var(--dim)';
      ih += '<div class="drv"><div class="dim">'+esc(c.name)+'</div>'+
        '<div><div class="bar"><span style="width:'+Math.abs(v)+'%;background:'+cl+
        ';margin-left:'+(v<0?'auto':'0')+'"></span></div>'+
        '<div class="dim2" style="font-size:10px">weight '+Math.round(c.weight*100)+
        '%</div></div><div class="dim2" style="text-align:right">'+esc(c.detail)+
        '</div></div>';
    });
  } else { ih='<div class="dim">waiting for positioning data…</div>'; }
  put('p-index', '<h2>Big-player pressure index</h2>'+ih, 'index');

  // prices
  let ph='<table><tr><th>Venue</th><th class="r">Price</th><th class="r">Chg day</th>'+
         '<th class="r">Age</th><th>Session</th></tr>';
  const sess = (D.sessions||{}).detail||{};
  const SESSOF={COMEX:'COMEX',COMEX_MICRO:'COMEX',SPOT:'SPOT',SGE:'SGE',LBMA_PM:'LBMA_PM'};
  ['COMEX','COMEX_MICRO','SPOT','SGE','LBMA_PM'].forEach(code=>{
    const b=B[code]; if(!b) return;
    const s=sess[SESSOF[code]]||{};
    let st = s.auctions ? '<span class="acc">auction</span>'
      : (s.open? '<span class="up">open</span>' : '<span class="dim2">closed</span>');
    let ag='—';
    if(b.data_ts){
      const secs=(Date.now()-new Date(b.data_ts).getTime())/1000;
      ag='<span class="'+(secs>5400?'stale':'dim')+'">'+age(secs)+'</span>';
    } else if(code==='LBMA_PM') ag='<span class="dim">daily</span>';
    else ag='<span class="up">live</span>';
    ph += '<tr><td>'+esc(code)+'</td><td class="r mono gold">'+
      (b.usd_oz?n(b.usd_oz):(b.native?n(b.native)+' '+esc(b.unit):'—'))+
      '</td><td class="r mono '+(b.change_pct>0?'up':b.change_pct<0?'down':'')+'">'+
      (b.change_pct==null?'—':sgn(b.change_pct)+'%')+'</td>'+
      '<td class="r">'+ag+'</td><td class="dim2">'+st+' '+
      esc(s.local||'')+' '+esc(s.zone||'')+'</td></tr>';
  });
  Object.keys(D.quotes||{}).forEach(code=>{
    const q=D.quotes[code]; if(!q) return;
    const sk = code==='GLD'||code==='IAU'||code==='GLDM' ? 'NYSE' : code;
    const s=sess[sk]||{};
    const st = s.open?'<span class="up">open</span>':'<span class="dim2">closed</span>';
    ph += '<tr><td>'+esc(code)+'</td><td class="r mono">'+n(q.price)+' '+
      esc(q.currency)+'</td><td class="r mono '+cls(q.change_pct)+'">'+
      (q.change_pct==null?'—':sgn(q.change_pct)+'%')+'</td>'+
      '<td class="r dim2">—</td><td class="dim2">'+st+' '+
      esc((q.delay_note||'').split('(')[0].trim())+'</td></tr>';
  });
  ph+='</table>';
  put('p-prices','<h2>Live prices</h2>'+ph,'prices');

  // trend
  const sh=(D.spot_hist||[]).map(p=>p[1]);
  const ch2=(D.comex_hist||[]).map(p=>p[1]);
  let th='<div class="dim" style="font-size:11px">spot</div>'+sparkline(sh,600,54)+
    '<div class="dim" style="font-size:11px;margin-top:8px">COMEX</div>'+
    sparkline(ch2,600,54)+
    '<div class="lg"><span>'+sh.length+' spot ticks</span><span>'+ch2.length+
    ' comex ticks</span></div>'+
    '<div class="note">Values are as polled by the monitor — one point per feed cycle, '+
    'so this is the monitor\'s own view of the session, not an exchange chart.</div>';
  put('p-spot','<h2>Spot &amp; COMEX — session trend</h2>'+th,'spot');

  // 1-minute spot chart
  const sbars = D.spot_bars||[];
  let kh='';
  if(sbars.length < 2){
    kh = '<div class="dim">Collecting — '+sbars.length+' of 2 minutes so far. '+
      'There is no free intraday spot history to backfill from, so this chart is '+
      'built from the monitor\'s own polling and fills as it runs. It persists '+
      'across restarts via the local database.</div>';
  } else {
    const hi = Math.max.apply(null, sbars.map(b=>b.high));
    const lo = Math.min.apply(null, sbars.map(b=>b.low));
    const last = sbars[sbars.length-1], first = sbars[0];
    const chg = last.close - first.open;
    const samples = sbars.reduce((a,b)=>a+(b.n||0),0);
    kh = '<div class="sub" style="margin-bottom:7px">'+sbars.length+' minutes · '+
      samples+' spot samples · high <b>'+n(hi)+'</b> · low <b>'+n(lo)+'</b> · last <b class="'+
      (chg>=0?'up':'down')+'">'+n(last.close)+'</b> <span class="'+(chg>=0?'up':'down')+
      '">'+sgn(chg)+' since '+tfmt(first.ts)+'</span></div>';
    // Spot owns the full vertical range. The COMEX future is deliberately NOT
    // overlaid: it sits ~30 USD above spot on a structural basis, so sharing an
    // axis would squash the series being monitored into a flat band at the
    // bottom. Two levels that far apart belong in separate plots — see the
    // session-trend panel above, which stacks them.
    kh += '<div style="display:flex;gap:9px">'+
      '<div style="display:flex;flex-direction:column;justify-content:space-between;'+
      'font-size:10px;color:var(--dim2);text-align:right;width:58px;padding:7px 0">'+
      '<span>'+n(hi)+'</span><span>'+n((hi+lo)/2)+'</span><span>'+n(lo)+'</span></div>'+
      '<div style="flex:1;min-width:0">'+spotChart(sbars, null, 1000, 190)+'</div></div>';
    kh += '<div class="lg" style="justify-content:space-between;margin-top:2px;'+
      'margin-left:67px">'+
      '<span>'+tfmt(first.ts)+'</span><span>'+tfmt(sbars[Math.floor(sbars.length/2)].ts)+
      '</span><span>'+tfmt(last.ts)+' (latest minute)</span></div>';
    kh += '<div class="lg"><span><i style="background:var(--gold)"></i>spot close</span>'+
      '<span><i style="background:rgba(245,197,66,.3)"></i>high-low observed that minute</span>'+
      '<span><i style="background:var(--fg);opacity:.5"></i>latest spot</span></div>';
    kh += '<div class="note">Not candlesticks, deliberately: gold-api prints roughly '+
      'one new price per minute, so within any minute open, high, low and close are '+
      'identical and every candle would collapse to a flat dash. The line steps at '+
      'each new print and the envelope shows the range actually observed, which is '+
      'what a discretely-sampled series supports. There is no free 1-minute spot '+
      'history to backfill from, so the chart is built from this monitor\'s own '+
      'polling and persists across restarts. The COMEX 1-minute series is real OHLC '+
      'but is a different instrument ~30 USD away — it lives in the session-trend '+
      'panel above rather than being forced onto this axis.</div>';
  }
  put('p-candles','<h2>Spot — 1-minute chart</h2>'+kh,'candles');

  // structure
  let xh='<table>';
  if(BX) xh+='<tr><td>COMEX vs spot (futures basis / EFP)</td><td class="r mono">'+
    sgn(BX.basis_usd)+' USD/oz</td></tr>';
  if(P){
    xh+='<tr><td>Shanghai premium vs implied spot'+
      (P.matched?'':' <span class="down">(unmatched)</span>')+
      '</td><td class="r mono '+cls(P.premium_usd)+'">'+sgn(P.premium_usd)+
      ' <span class="dim2">('+sgn(P.premium_pct)+'%)</span></td></tr>';
    if(P.premium_vs_comex!=null)
      xh+='<tr><td class="dim2">…vs same-hour COMEX future</td><td class="r mono dim2">'+
        sgn(P.premium_vs_comex)+'</td></tr>';
    if(P.implied_spot) xh+='<tr><td class="dim2">implied spot at SGE print</td>'+
      '<td class="r mono dim2">'+n(P.implied_spot)+'</td></tr>';
    xh+='<tr><td class="dim2">reference</td><td class="r dim2">'+
      esc(P.reference||'')+'</td></tr>';
  }
  xh+='</table><div class="note">Shanghai is compared against a reference at the SAME '+
    'moment, then the futures basis is subtracted to imply spot — otherwise the '+
    'comparison books hours of market drift as a premium. During the SGE night session '+
    'the print is hours old; its age is shown above.</div>';
  put('p-struct','<h2>Cross-venue structure</h2>'+xh,'struct');

  // positioning
  const PS=D.positions;
  let qh='';
  if(PS&&PS.groups){
    qh='<div class="sub" style="margin-bottom:8px">report '+esc(PS.report_date)+
      ' · '+i0(PS.open_interest)+' ct · '+esc(PS.basis_label||'')+'</div>'+
      '<table><tr><th>Group</th><th class="r">Long</th><th class="r">Short</th>'+
      '<th class="r">Net</th><th class="r">Δ wk</th><th class="r">Z</th>'+
      '<th class="r">Pctl</th></tr>';
    ['swap_dealer','managed_money','other_rept','prod_merc'].forEach(k=>{
      const g=PS.groups[k]; if(!g) return;
      qh+='<tr><td>'+esc(g.label)+'</td><td class="r mono">'+i0(g.long)+
        '</td><td class="r mono">'+i0(g.short)+'</td><td class="r mono '+
        (g.net>0?'up':'down')+'">'+sgn(g.net,0)+'</td><td class="r mono '+
        cls(g.change)+'">'+sgn(g.change,0)+'</td><td class="r mono">'+
        (g.net_zscore==null?'—':sgn(g.net_zscore))+'</td><td class="r mono">'+
        (g.net_percentile==null?'—':Math.round(g.net_percentile)+'%')+
        '</td></tr>';
    });
    qh+='</table>';
    const C2=PS.concentration||{};
    if(C2.gross_4_short!=null)
      qh+='<div class="dim2" style="margin-top:8px;font-size:11px">4 largest shorts hold '+
        n(C2.gross_4_short,1)+'% of open interest · 8 largest '+
        n(C2.gross_8_short,1)+'%</div>';
    const NH=PS.net_history;
    if(NH){
      const ndefs=[['managed_money','Managed money','var(--acc)'],
                   ['swap_dealer','Swap dealers','var(--gold)'],
                   ['prod_merc','Producer / merchant','var(--up)'],
                   ['other_rept','Other reportables','#b07cff']];
      const nser=ndefs.filter(d=>NH[d[0]]&&NH[d[0]].length)
        .map(d=>({name:d[1],color:d[2],pts:NH[d[0]]}));
      if(nser.length){
        qh+='<div class="sub" style="margin:13px 0 5px">Large-trader net positioning '+
          '— '+nser[0].pts.length+' weekly reports</div>';
        qh+=multiLine(nser, 1000, 165, v=>i0(v/1000)+'k');
        qh+='<div class="lg">'+nser.map(s=>
          '<span><i style="background:'+s.color+'"></i>'+esc(s.name)+'</span>')
          .join('')+'</div>';
        qh+='<div class="note">Net = outright long minus short. Swap dealers are '+
          'structurally short: they warehouse global hedging flow, so when that '+
          'short shrinks they are less willing to cap the market, and when it '+
          'grows they are capping harder. Managed money is the momentum crowd, '+
          'and its extremes mark turns. One shared axis, deliberately — the gap '+
          'between the two is the point.</div>';
      }
    }
  } else { qh='<div class="dim">loading weekly COT data…</div>'; }
  put('p-pos','<h2>Who holds what — CFTC</h2>'+qh,'pos');

  // tape
  const T=D.tape;
  let uh='';
  if(T&&!T.error){
    uh='<div class="sub" style="margin-bottom:7px">'+esc(T.symbol)+' · session vol '+
      i0(T.session_volume)+' · median bar '+i0(T.typical_volume)+' · <b>'+
      T.flagged_count+'</b> anomalies'+
      (T.buy_ratio!=null?' · <span class="'+(T.buy_ratio>.5?'up':'down')+'">'+
        Math.round(T.buy_ratio*100)+'% on the bid</span>':'')+'</div>'+
      '<table><tr><th>Time UTC</th><th class="r">Lots</th><th class="r">vs med</th>'+
      '<th class="r">Z</th><th class="r">Price</th><th class="r">Move</th>'+
      '<th class="r">Side</th></tr>';
    (T.spikes||[]).forEach(s=>{
      const t=(s.ts||'').slice(11,16);
      uh+='<tr><td class="mono">'+t+'</td><td class="r mono">'+i0(s.volume)+
        '</td><td class="r mono">'+n(s.volume_multiple,1)+'x</td><td class="r mono">'+
        sgn(s.volume_z,1)+'</td><td class="r mono">'+n(s.close)+'</td><td class="r mono '+
        cls(s.move)+'">'+sgn(s.move,1)+'</td><td class="r '+
        (s.direction==='buy'?'up':'down')+'">'+esc(s.direction)+'</td></tr>';
    });
    uh+='</table>';
  } else { uh='<div class="dim">'+(T&&T.error||'waiting for tape…')+'</div>'; }
  put('p-tape','<h2>Real-time tape</h2>'+uh,'tape');

  // feeds
  let fh='<table><tr><th>Feed</th><th>State</th><th class="r">Age</th>'+
    '<th class="r">Cadence</th><th class="r">OK</th><th class="r">Errors</th>'+
    '<th class="r">Latency</th><th>Latest</th></tr>';
  const F=D.feeds||{};
  Object.keys(F).forEach(k=>{
    const f=F[k];
    const col = f.state==='live'?'var(--up)':(f.state==='down'||f.state==='dead')?
                'var(--down)':f.state==='pending'?'var(--dim2)':'var(--warn)';
    fh+='<tr><td>'+esc(f.label||k)+'</td><td><span class="dot" style="background:'+
      col+'"></span><span style="color:'+col+'">'+esc(f.state)+'</span></td>'+
      '<td class="r mono">'+age(f.age_s)+'</td><td class="r dim2">'+
      age(f.interval)+'</td><td class="r mono">'+i0(f.ok)+'</td>'+
      '<td class="r mono '+(f.err?'down':'dim2')+'">'+i0(f.err)+'</td>'+
      '<td class="r mono dim2">'+Math.round(f.latency_ms||0)+'ms</td>'+
      '<td class="dim2">'+esc((f.last_error?('! '+f.last_error):(f.note||'')).slice(0,70))+
      '</td></tr>';
  });
  fh+='</table><div class="note">One thread per feed, each on its own cadence. A slow '+
    'or hanging feed fails alone and backs off; the others keep streaming. '+
    'Requests: '+i0((D.http_stats||{}).requests)+', errors: '+
    i0((D.http_stats||{}).errors)+', cache hits: '+i0((D.http_stats||{}).cache_hits)+
    '.</div>';
  put('p-feeds','<h2>Feed health</h2>'+fh,'feeds');

  // alerts
  const A=(D.alerts||[]).slice().reverse();
  let ah='';
  if(!A.length) ah='<div class="dim">nothing crossed a threshold yet</div>';
  A.forEach(a=>{
    const c=a.level==='CRITICAL'?'crit':a.level==='NOTABLE'?'note':'';
    const pc=a.level==='CRITICAL'?'p-crit':a.level==='NOTABLE'?'p-note':'p-info';
    ah+='<div class="alert '+c+'"><span class="pill '+pc+'">'+esc(a.level)+'</span>'+
      '<span class="dim2" style="font-size:10px;margin-left:7px">'+
      esc((a.ts||'').slice(11,19))+' '+esc(a.code)+'</span>'+
      '<div class="m">'+esc(a.message)+'</div></div>';
  });
  put('p-alerts','<h2>Alerts ('+A.length+' shown of '+i0(D.alert_count)+
    ')</h2>'+ah,'alerts');

  document.getElementById('foot').innerHTML =
    'Source of positioning: CFTC Commitments of Traders, weekly, released Friday '+
    '15:30 ET — the only legal disclosure of large-trader books. ETF tonnage from '+
    'the World Gold Council. Shanghai from sge.com.cn, London from prices.lbma.org.uk, '+
    'spot from api.gold-api.com, venues via Yahoo Finance. COMEX real-time depth '+
    'requires a CME licence and is unavailable publicly; the tape infers size from '+
    'volume anomalies, not identified orders. Not investment advice.';
}

let lastOk = 0;
async function tick(){
  try{
    const r = await fetch('/api/snapshot',{cache:'no-store'});
    if(!r.ok) throw new Error('HTTP '+r.status);
    const D = await r.json();
    render(D);
    lastOk = Date.now();
    const c=document.getElementById('conn');
    if(c){ c.textContent='live'; c.className='acc'; }
  }catch(e){
    const c=document.getElementById('conn');
    if(c){ c.textContent='reconnecting…'; c.className='down'; }
  }
}
tick();
setInterval(tick, REFRESH*1000);

/* A slow cadence must not look like a frozen page: count down to the next
   fetch and show when the data on screen was actually taken. */
setInterval(function(){
  const el = document.getElementById('timer');
  if(!el) return;
  if(!lastOk){ el.textContent = 'waiting for first update…'; return; }
  const since = (Date.now()-lastOk)/1000;
  const nextIn = Math.max(0, Math.ceil(REFRESH - since));
  el.textContent = 'data as of ' + new Date(lastOk).toLocaleTimeString() +
    ' · next refresh in ' + nextIn + 's · every ' + REFRESH + 's';
}, 1000);
</script></body></html>
"""


def make_handler(engine: LiveEngine, refresh: int, page: bytes):
    class Handler(BaseHTTPRequestHandler):
        server_version = "goldtrack-live"

        def log_message(self, format, *args):   # noqa: A002 - stdlib signature
            if engine.verbose:
                print(f"  http {self.address_string()} {format % args}")

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError):
                pass

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/":
                return self._send(200, page, "text/html; charset=utf-8")
            if path == "/api/snapshot":
                snap = jsonable(engine.snapshot())
                return self._send(200, json.dumps(snap, default=str).encode(),
                                  "application/json")
            if path == "/api/alerts":
                snap = engine.snapshot()
                body = json.dumps([{"ts": a["ts"].isoformat(), "level": a["level"],
                                    "code": a["code"], "message": a["message"]}
                                   for a in snap.get("alerts", [])]).encode()
                return self._send(200, body, "application/json")
            if path == "/health":
                snap = engine.snapshot()
                feeds = snap.get("feeds") or {}
                # Health means "broken", not "slow": a feed on a 30-minute
                # cadence is not unhealthy between polls.
                bad = [k for k, f in feeds.items() if not f.get("healthy", True)]
                stalled = [k for k, f in feeds.items() if f.get("state") == "stalled"]
                txt = (f"ok={not bad} uptime={snap['uptime_s']:.0f}s "
                       f"ticks={snap['tick_count']} alerts={snap['alert_count']} "
                       f"feeds_healthy={len(feeds)-len(bad)}/{len(feeds)}"
                       + (f" unhealthy={','.join(bad)}" if bad else "")
                       + (f" stalled={','.join(stalled)}"
                          if stalled and not bad else "") + "\n")
                return self._send(200 if not bad else 503, txt.encode(),
                                  "text/plain; charset=utf-8")
            self._send(404, b'{"error":"not found"}', "application/json")

    return Handler


class _Server(ThreadingHTTPServer):
    """Refuse to share a port.

    On Windows, SO_REUSEADDR lets a SECOND server bind a port that is already
    listening — so a restarted monitor silently races the old one and clients
    get whichever instance answers. That produced stale responses during
    development. Disabling it makes a duplicate start fail loudly instead.
    """
    allow_reuse_address = False
    daemon_threads = True


def serve(engine: LiveEngine, host: str = "127.0.0.1", port: int = 8787,
          refresh: int = 3, open_browser: bool = False) -> None:
    page = PAGE.replace("__REFRESH__", str(max(1, refresh))).encode("utf-8")
    try:
        httpd = _Server((host, port), make_handler(engine, refresh, page))
    except OSError as e:
        print(f"\n  Cannot bind {host}:{port} — {e}")
        print(f"  Another monitor is probably already running there.")
        print(f"  Use it as-is, or start this one on a different port:")
        print(f"      python -m goldtrack serve --port {port + 1}")
        raise SystemExit(1)
    url = f"http://{host}:{port}/"
    print(f"  live monitor serving on {url}")
    print(f"  api: {url}api/snapshot   health: {url}health")
    print("  Ctrl-C to stop")
    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.7), _open(url)),
                         daemon=True).start()
    try:
        httpd.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
        print("\n  server stopped.")


def _open(url: str) -> None:
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
