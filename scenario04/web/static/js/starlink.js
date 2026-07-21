"use strict";
/* ═══════════════════════════════════════════════════════════════════════════
   Starlink 台灣服務能力分析前端
   功能：可見性時間軸、RTT 傳播延遲、極座標天空密度、遮蔽模擬
   ═══════════════════════════════════════════════════════════════════════════ */

// ── 全域狀態 ──────────────────────────────────────────────────────────────────
let _polling   = false;
let _data      = null;     // 主要可見性計算結果
let _obstData  = null;     // 遮蔽模擬結果
let _polarMode = 'density';
let _blocked   = new Set();  // "az_c,el_c" 字串集合
let _maskDeg   = 25;

// ── 時鐘 ──────────────────────────────────────────────────────────────────────
function _tick(){
  const el=document.getElementById('clock');
  if(el) el.textContent=new Date().toUTCString().replace('GMT','UTC');
}

// ── 城市預設下拉 ──────────────────────────────────────────────────────────────
function togglePreset(){
  document.getElementById('preset-menu').classList.toggle('open');
}
function applyPreset(lat,lon,name){
  document.getElementById('sf-lat').value=lat;
  document.getElementById('sf-lon').value=lon;
  document.getElementById('settings-note').textContent=
    `已選擇：${name}（${lat}N, ${lon}E），遮蔽仰角建議 25°`;
  document.getElementById('preset-menu').classList.remove('open');
}
document.addEventListener('click',e=>{
  if(!e.target.closest('#preset-wrap'))
    document.getElementById('preset-menu').classList.remove('open');
});

// ── 取得輸入參數 ──────────────────────────────────────────────────────────────
function getParams(){
  const lat     = parseFloat(document.getElementById('sf-lat').value)  || 25.033;
  const lon     = parseFloat(document.getElementById('sf-lon').value)  || 121.565;
  const mask    = parseFloat(document.getElementById('sf-mask').value) || 25;
  return {lat, lon, mask};
}

// ── Loading 狀態 ──────────────────────────────────────────────────────────────
function setLoading(on, msg){
  document.getElementById('loading-bar').classList.toggle('active', on);
  if(msg) document.getElementById('loading-msg').textContent = msg;
  document.getElementById('compute-btn').disabled = on;
}

function showResultPanels(show){
  ['stats-row','charts-row','gaps-box'].forEach(id=>{
    document.getElementById(id).style.display = show ? '' : 'none';
  });
}

// ── 觸發計算 ──────────────────────────────────────────────────────────────────
function triggerCompute(){
  if(_polling) return;
  _data = null; _obstData = null;
  _blocked.clear();
  document.getElementById('obst-result').style.display = 'none';
  showResultPanels(false);
  const {lat, lon, mask} = getParams();
  _maskDeg = mask;
  setLoading(true,
    `正在向量化計算 Starlink 可見性（${lat.toFixed(3)}N, ${lon.toFixed(3)}E，仰角 ≥ ${mask}°）...`);
  const url = `/api/starlink/visibility?lat=${lat}&lon=${lon}&mask=${mask}&hours=24&step=15`;
  _poll(url);
}

function _poll(url){
  _polling = true;
  fetch(url)
    .then(r => r.status === 202 ? null : r.json())
    .then(data => {
      if(data === null){ setTimeout(() => _poll(url), 1800); return; }
      _polling = false;
      setLoading(false);
      if(data.error){
        document.getElementById('loading-msg').textContent = '錯誤：' + data.error;
        document.getElementById('loading-bar').classList.add('active');
        return;
      }
      _data = data;
      renderAll(data);
    })
    .catch(err => { _polling = false; setLoading(false); console.error(err); });
}

// ═══════════════════════════════════════════════════════════════════════════════
// 主渲染函數
// ═══════════════════════════════════════════════════════════════════════════════
function renderAll(data){
  showResultPanels(true);
  renderStats(data.stats);
  renderLineChart(data.timeline, data.stats);
  renderRttChart(data.timeline, data.stats);
  renderPolarDensity(data.sky_density, data.mask_deg);
  renderGaps(data.gaps, data.stats);
  const note = document.getElementById('timeline-note');
  if(note){
    const ts = new Date(data.computed_at).toUTCString().replace('GMT','UTC');
    note.textContent = `計算時刻：${ts}　步長：${data.step_min} 分`;
  }
}

// ── 統計卡片 ──────────────────────────────────────────────────────────────────
function renderStats(s){
  const set = (id, val, cls='') => {
    const el = document.getElementById(id);
    if(!el) return;
    el.textContent = val;
    el.className = 'sc-val' + (cls ? ' ' + cls : '');
  };
  set('st-total',    s.total_sats.toLocaleString());
  set('st-mean',     s.mean_visible.toFixed(1));
  set('st-min',      s.min_visible,   s.min_visible >= 1 ? 'good' : 'warn');
  set('st-avail',    s.availability_pct.toFixed(2) + '%',
                     s.availability_pct >= 99 ? 'good' : s.availability_pct >= 95 ? '' : 'warn');
  set('st-gaps',     s.gap_count,      s.gap_count === 0 ? 'good' : 'warn');
  set('st-rtt-mean', s.mean_rtt_floor_ms != null ? s.mean_rtt_floor_ms.toFixed(1) + ' ms' : '—');
  set('st-rtt-range',
      s.min_rtt_floor_ms != null
        ? `${s.min_rtt_floor_ms.toFixed(1)}–${s.max_rtt_floor_ms.toFixed(1)} ms`
        : '—');
}

// ── 空窗列表 ──────────────────────────────────────────────────────────────────
function renderGaps(gaps, stats){
  const box = document.getElementById('gaps-content');
  if(!gaps || gaps.length === 0){
    box.innerHTML = '<div class="gap-ok">&#10003; 覆蓋連續，24 小時內無幾何空窗</div>';
    return;
  }
  let html = '';
  gaps.forEach(g => {
    const t1 = fmtTime(g.start), t2 = fmtTime(g.end);
    html += `<div class="gap-item">
      <span class="gap-time">${t1} – ${t2}</span>
      <span class="gap-dur">&#9673; ${g.duration_min} 分鐘</span>
    </div>`;
  });
  if(stats.gap_total_min > 0)
    html += `<div style="font-size:11px;color:#8b949e;margin-top:6px">
      合計空窗：${stats.gap_total_min} 分鐘（${(stats.gap_total_min/60).toFixed(2)} 小時）</div>`;
  box.innerHTML = html;
}
function fmtTime(iso){ return new Date(iso).toLocaleTimeString('zh-TW',{hour:'2-digit',minute:'2-digit'}); }

// ═══════════════════════════════════════════════════════════════════════════════
// 可見衛星數量時間軸折線圖
// ═══════════════════════════════════════════════════════════════════════════════
function renderLineChart(timeline, stats, obstrTimeline){
  const cvs = document.getElementById('timeline-canvas');
  const W   = (cvs.parentElement.clientWidth - 20) || 680;
  const H   = 160;
  _setupCanvas(cvs, W, H);
  const ctx = cvs.getContext('2d');
  _drawLineChart(ctx, W, H, timeline, stats, obstrTimeline);
}

function _drawLineChart(ctx, W, H, timeline, stats, obstrTimeline){
  const pL=46, pR=12, pT=14, pB=36;
  const cW = W-pL-pR, cH = H-pT-pB, n = timeline.length;
  const maxY = Math.max(stats.max_visible||0, 10) + Math.ceil((stats.max_visible||10)*0.12 + 2);

  ctx.clearRect(0,0,W,H);
  ctx.fillStyle = '#0d1117'; ctx.fillRect(0,0,W,H);

  const xP = i => pL + i/(n-1)*cW;
  const yP = v => pT + cH - (v/maxY)*cH;

  // Y 網格
  const yStep = Math.max(1, Math.ceil(maxY/6/5)*5);
  ctx.font = '9px monospace'; ctx.textAlign = 'right';
  for(let v=0; v<=maxY; v+=yStep){
    const y = yP(v);
    ctx.strokeStyle = '#21262d'; ctx.lineWidth = 1;
    _line(ctx, pL, y, pL+cW, y);
    ctx.fillStyle = '#6e7681'; ctx.fillText(v, pL-4, y+3);
  }

  // X 網格（每 3 小時）
  const stepMin = n>1 ? (24*60/(n-1)) : 15;
  ctx.textAlign = 'center';
  for(let h=0; h<=24; h+=3){
    const i = Math.round(h*60/stepMin);
    if(i>=n) break;
    const x = xP(i);
    ctx.strokeStyle = '#21262d'; ctx.lineWidth = 1; _line(ctx, x, pT, x, pT+cH);
    ctx.fillStyle = '#6e7681'; ctx.fillText(h===0?'現在':`+${h}h`, x, pT+cH+14);
  }

  // 空窗區
  timeline.forEach((e,i) => {
    if(!e.available){
      ctx.fillStyle = 'rgba(244,67,54,.12)';
      const x1 = i>0?xP(i-0.5):xP(0), x2 = i<n-1?xP(i+0.5):xP(n-1);
      ctx.fillRect(x1, pT, x2-x1, cH);
    }
  });

  // 門檻虛線
  ctx.setLineDash([4,4]); ctx.strokeStyle='#F44336'; ctx.lineWidth=1;
  _line(ctx, pL, yP(1), pL+cW, yP(1));
  ctx.setLineDash([]);

  // 原始折線填色
  _fillArea(ctx, timeline, xP, yP, pL, pT, cH, 'rgba(168,85,247,.1)');

  // 遮蔽後折線（若有）
  if(obstrTimeline){
    _fillArea(ctx, obstrTimeline, xP, yP, pL, pT, cH, 'rgba(244,67,54,.08)');
    ctx.strokeStyle = '#F44336'; ctx.lineWidth = 1.5; ctx.setLineDash([5,3]);
    _drawLine(ctx, obstrTimeline, xP, yP);
    ctx.setLineDash([]);
  }

  // 原始折線
  ctx.strokeStyle = '#A855F7'; ctx.lineWidth = 2;
  _drawLine(ctx, timeline, xP, yP);

  // 軸線
  ctx.strokeStyle = '#30363d'; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(pL,pT); ctx.lineTo(pL,pT+cH); ctx.lineTo(pL+cW,pT+cH); ctx.stroke();

  // Y 軸標題
  ctx.save(); ctx.translate(11, pT+cH/2); ctx.rotate(-Math.PI/2);
  ctx.font='10px sans-serif'; ctx.fillStyle='#8b949e'; ctx.textAlign='center';
  ctx.fillText('可見顆數',0,0); ctx.restore();
}
function _fillArea(ctx, tl, xP, yP, pL, pT, cH, color){
  const n = tl.length;
  ctx.fillStyle = color;
  ctx.beginPath();
  tl.forEach((e,i)=>{ const x=xP(i),y=yP(e.visible); i===0?ctx.moveTo(x,y):ctx.lineTo(x,y); });
  ctx.lineTo(xP(n-1),yP(0)); ctx.lineTo(xP(0),yP(0)); ctx.closePath();
  ctx.fill();
}
function _drawLine(ctx, tl, xP, yP){
  ctx.lineJoin='round'; ctx.beginPath();
  tl.forEach((e,i)=>{ const x=xP(i),y=yP(e.visible); i===0?ctx.moveTo(x,y):ctx.lineTo(x,y); });
  ctx.stroke();
}

// ═══════════════════════════════════════════════════════════════════════════════
// RTT 傳播延遲折線圖
// ═══════════════════════════════════════════════════════════════════════════════
function renderRttChart(timeline, stats){
  const cvs = document.getElementById('rtt-canvas');
  if(!cvs) return;
  const W = (cvs.parentElement.clientWidth - 20) || 680;
  const H = 90;
  _setupCanvas(cvs, W, H);
  const ctx = cvs.getContext('2d');

  const pL=46, pR=12, pT=10, pB=28;
  const cW=W-pL-pR, cH=H-pT-pB, n=timeline.length;

  ctx.clearRect(0,0,W,H);
  ctx.fillStyle='#0d1117'; ctx.fillRect(0,0,W,H);

  const rttValues = timeline.map(e=>e.rtt_floor_ms).filter(v=>v!=null);
  if(rttValues.length === 0){
    ctx.fillStyle='#6e7681'; ctx.font='11px sans-serif'; ctx.textAlign='center';
    ctx.fillText('RTT 資料不可用',W/2,H/2); return;
  }
  const maxRtt = Math.ceil((stats.max_rtt_floor_ms||10) * 1.15 / 2) * 2;
  const minRtt = Math.max(0, Math.floor((stats.min_rtt_floor_ms||0) * 0.85 / 2) * 2);

  const xP = i => pL + i/(n-1)*cW;
  const yP = v => pT + cH - ((v-minRtt)/(maxRtt-minRtt||1))*cH;

  // 網格
  ctx.font='9px monospace'; ctx.textAlign='right';
  const steps=[minRtt, (minRtt+maxRtt)/2, maxRtt];
  steps.forEach(v=>{
    const y = yP(v);
    ctx.strokeStyle='#21262d'; ctx.lineWidth=1; _line(ctx,pL,y,pL+cW,y);
    ctx.fillStyle='#6e7681'; ctx.fillText(v.toFixed(1),pL-3,y+3);
  });

  // 參考線：LEO 天頂 RTT（550km）
  const rtt550 = 2*550/299.792;
  if(rtt550 >= minRtt && rtt550 <= maxRtt){
    const y = yP(rtt550);
    ctx.strokeStyle='rgba(88,166,255,.3)'; ctx.lineWidth=1; ctx.setLineDash([3,3]);
    _line(ctx,pL,y,pL+cW,y); ctx.setLineDash([]);
    ctx.fillStyle='#58a6ff'; ctx.font='8px monospace'; ctx.textAlign='left';
    ctx.fillText('天頂',pL+2,y-2);
  }

  // 折線
  ctx.strokeStyle='#58a6ff'; ctx.lineWidth=1.5; ctx.lineJoin='round';
  ctx.beginPath();
  timeline.forEach((e,i)=>{
    const v=e.rtt_floor_ms; if(v==null) return;
    const x=xP(i),y=yP(v); i===0||timeline[i-1].rtt_floor_ms==null?ctx.moveTo(x,y):ctx.lineTo(x,y);
  });
  ctx.stroke();

  // X 標籤（只標 0h 和 24h）
  ctx.fillStyle='#6e7681'; ctx.font='9px monospace'; ctx.textAlign='center';
  ctx.fillText('現在',xP(0),H-2);
  ctx.fillText('+24h',xP(n-1),H-2);

  // 軸
  ctx.strokeStyle='#30363d'; ctx.lineWidth=1.5;
  ctx.beginPath(); ctx.moveTo(pL,pT); ctx.lineTo(pL,pT+cH); ctx.lineTo(pL+cW,pT+cH); ctx.stroke();

  // Y 標題
  ctx.save(); ctx.translate(11,pT+cH/2); ctx.rotate(-Math.PI/2);
  ctx.font='9px sans-serif'; ctx.fillStyle='#8b949e'; ctx.textAlign='center';
  ctx.fillText('RTT ms',0,0); ctx.restore();
}

// ═══════════════════════════════════════════════════════════════════════════════
// 極座標天空圖
// ═══════════════════════════════════════════════════════════════════════════════
function setPolarMode(mode){
  _polarMode = mode;
  document.getElementById('tab-density').classList.toggle('active', mode==='density');
  document.getElementById('tab-obstruct').classList.toggle('active', mode==='obstruct');
  document.getElementById('polar-density-note').style.display = mode==='density' ? '' : 'none';
  document.getElementById('obstruct-controls').style.display  = mode==='obstruct' ? '' : 'none';
  if(_data) _redrawPolar();

  const cvs = document.getElementById('polar-canvas');
  if(mode === 'obstruct'){
    cvs.style.cursor = 'crosshair';
    cvs.onclick = onPolarClick;
  } else {
    cvs.style.cursor = '';
    cvs.onclick = null;
  }
}

function _redrawPolar(){
  if(_polarMode === 'density')
    renderPolarDensity(_data.sky_density, _data.mask_deg);
  else
    renderPolarObstruct(_data.sky_density, _data.mask_deg);
}

function renderPolarDensity(skyDensity, maskDeg){
  const {ctx, cx, cy, maxR} = _polarSetup();
  _drawPolarGrid(ctx, cx, cy, maxR, maskDeg);
  if(skyDensity) skyDensity.forEach(([azC,elC,pct])=>
    _drawPolarCell(ctx,cx,cy,maxR,maskDeg,azC,elC,10,5,pct/100,'168,85,247'));
  _polarZenith(ctx,cx,cy);
}

function renderPolarObstruct(skyDensity, maskDeg){
  const {ctx, cx, cy, maxR} = _polarSetup();
  // 底層密度（半透明）
  if(skyDensity) skyDensity.forEach(([azC,elC,pct])=>
    _drawPolarCell(ctx,cx,cy,maxR,maskDeg,azC,elC,10,5,pct/100*0.3,'168,85,247'));
  // 網格
  const azSteps=Array.from({length:36},(_,i)=>i*10+5);
  const el_min=maskDeg;
  const elSteps=[];
  for(let e=el_min+2.5; e<90; e+=5) elSteps.push(e);
  azSteps.forEach(azC=>elSteps.forEach(elC=>{
    const key=`${azC},${elC}`;
    const blocked=_blocked.has(key);
    _drawPolarCellOutline(ctx,cx,cy,maxR,maskDeg,azC,elC,10,5,blocked?'rgba(244,67,54,.55)':'rgba(255,255,255,.04)');
    if(blocked)
      _drawPolarCell(ctx,cx,cy,maxR,maskDeg,azC,elC,10,5,0.7,'244,67,54');
  }));
  _drawPolarGrid(ctx,cx,cy,maxR,maskDeg);
  _polarZenith(ctx,cx,cy);
}

function onPolarClick(e){
  if(_polarMode !== 'obstruct' || !_data) return;
  const cvs = document.getElementById('polar-canvas');
  const rect = cvs.getBoundingClientRect();
  const dpr  = window.devicePixelRatio || 1;
  const mx   = (e.clientX - rect.left) / rect.width  * (cvs.width  / dpr);
  const my   = (e.clientY - rect.top)  / rect.height * (cvs.height / dpr);
  const SIZE = 240, cx=SIZE/2, cy=SIZE/2, maxR=SIZE/2-10;
  const dx=mx-cx, dy=my-cy;
  const r=Math.sqrt(dx*dx+dy*dy);
  if(r > maxR || r < 1) return;
  const maskDeg = _data.mask_deg;
  const el = 90 - (r/maxR)*(90-maskDeg);
  if(el < maskDeg) return;
  const az = ((Math.atan2(dx,-dy)*180/Math.PI)+360)%360;
  // Snap to cell center
  const azC = Math.floor(az/10)*10+5;
  const elC = Math.floor((el-maskDeg)/5)*5+maskDeg+2.5;
  const key = `${azC},${elC}`;
  if(_blocked.has(key)) _blocked.delete(key); else _blocked.add(key);
  document.getElementById('block-count').textContent = `已選 ${_blocked.size} 格`;
  renderPolarObstruct(_data.sky_density, maskDeg);
}

function clearObstruction(){
  _blocked.clear();
  _obstData = null;
  document.getElementById('block-count').textContent = '已選 0 格';
  document.getElementById('obst-result').style.display = 'none';
  if(_data) renderPolarObstruct(_data.sky_density, _data.mask_deg);
  if(_data) renderLineChart(_data.timeline, _data.stats);
}

function clearObstructionResult(){
  _obstData = null;
  document.getElementById('obst-result').style.display = 'none';
  if(_data) renderLineChart(_data.timeline, _data.stats);
}

function applyObstruction(){
  if(!_data || _blocked.size === 0){
    alert('請先在遮蔽模擬模式下點選要封鎖的天空格子'); return;
  }
  const {lat, lon, mask} = getParams();
  const blocked_cells = [..._blocked].map(k=>{ const [a,e]=k.split(','); return [parseFloat(a),parseFloat(e)]; });
  const btn = document.getElementById('apply-obst-btn');
  btn.disabled = true; btn.textContent='計算中...';

  fetch('/api/starlink/obstruction',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({lat,lon,mask,hours:24,step:15,blocked_cells}),
  })
  .then(r=>r.json())
  .then(data=>{
    btn.disabled=false; btn.textContent='▶ 計算遮蔽影響';
    if(data.error){ alert('遮蔽計算失敗：'+data.error); return; }
    _obstData = data;
    renderObstructionResult(data);
    renderLineChart(_data.timeline, _data.stats, data.timeline);
  })
  .catch(err=>{ btn.disabled=false; btn.textContent='▶ 計算遮蔽影響'; console.error(err); });
}

function renderObstructionResult(data){
  const box = document.getElementById('obst-result');
  box.style.display = '';
  const s = data.stats, o = data.original_stats;
  const delta = s.delta_pct;
  const dColor = delta < 0 ? '#F44336' : '#2ea043';
  document.getElementById('obst-compare').innerHTML = `
    <div class="obst-compare-grid">
      <div class="occ before">
        <div class="occ-title">遮蔽前</div>
        <div class="occ-val">${o.availability_pct.toFixed(2)}%</div>
        <div class="occ-sub">平均可見 ${o.mean_visible} 顆　空窗 ${o.gap_count} 次</div>
      </div>
      <div class="occ arrow">&#8594;</div>
      <div class="occ after">
        <div class="occ-title">遮蔽後（${data.blocked_count} 格封鎖）</div>
        <div class="occ-val">${s.availability_pct.toFixed(2)}%</div>
        <div class="occ-sub">平均可見 ${s.mean_visible} 顆　空窗 ${s.gap_count} 次</div>
      </div>
      <div class="occ delta" style="color:${dColor}">
        &#916; ${delta >= 0?'+':''}${delta.toFixed(2)}%
      </div>
    </div>`;

  // 遮蔽後空窗
  let gHtml = '';
  if(data.gaps && data.gaps.length > 0){
    gHtml = '<div style="margin-top:8px;font-size:11px;color:#e6edf3;font-weight:600">遮蔽後新增空窗</div>';
    data.gaps.forEach(g=>{
      gHtml += `<div class="gap-item"><span class="gap-time">${fmtTime(g.start)} – ${fmtTime(g.end)}</span>
        <span class="gap-dur">&#9673; ${g.duration_min} 分鐘</span></div>`;
    });
  } else {
    gHtml = '<div class="gap-ok" style="margin-top:6px">&#10003; 遮蔽後仍無空窗</div>';
  }
  document.getElementById('obst-gaps').innerHTML = gHtml;

  // 遮蔽後 RTT 比較圖（用 obst-canvas）
  const cvs = document.getElementById('obst-canvas');
  if(cvs && _data){
    const W=(cvs.parentElement.clientWidth-20)||680, H=120;
    _setupCanvas(cvs,W,H);
    _drawLineChart(cvs.getContext('2d'),W,H,_data.timeline,_data.stats,data.timeline);
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// 極座標輔助函數
// ═══════════════════════════════════════════════════════════════════════════════
function _polarSetup(){
  const cvs = document.getElementById('polar-canvas');
  const SIZE = 240;
  const dpr  = window.devicePixelRatio || 1;
  cvs.width  = SIZE*dpr; cvs.height = SIZE*dpr;
  cvs.style.width = SIZE+'px'; cvs.style.height = SIZE+'px';
  const ctx = cvs.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.fillStyle='#0d1117'; ctx.fillRect(0,0,SIZE,SIZE);
  return {ctx, cx:SIZE/2, cy:SIZE/2, maxR:SIZE/2-10};
}

function _drawPolarGrid(ctx, cx, cy, maxR, maskDeg){
  const DIRS=['N','NE','E','SE','S','SW','W','NW'];
  ctx.strokeStyle='#21262d'; ctx.lineWidth=1;
  [30,45,60,75,90].forEach(el=>{
    if(el<maskDeg) return;
    const r=_el2r(el,maskDeg,maxR);
    ctx.beginPath(); ctx.arc(cx,cy,r,0,Math.PI*2); ctx.stroke();
    ctx.fillStyle='#6e7681'; ctx.font='8px monospace'; ctx.textAlign='center';
    ctx.fillText(el+'°',cx,cy-r+9);
  });
  DIRS.forEach((d,i)=>{
    const a=(i*45-90)*Math.PI/180;
    const rInner=_el2r(maskDeg,maskDeg,maxR);
    _line(ctx,cx+rInner*Math.cos(a),cy+rInner*Math.sin(a),cx+maxR*Math.cos(a),cy+maxR*Math.sin(a));
    const lr=maxR+14;
    ctx.fillStyle=d==='N'?'#e6edf3':'#8b949e'; ctx.font='9px sans-serif'; ctx.textAlign='center';
    ctx.fillText(d,cx+lr*Math.cos(a),cy+lr*Math.sin(a)+3);
  });
  ctx.strokeStyle='#F44336'; ctx.lineWidth=1.5; ctx.setLineDash([3,3]);
  ctx.beginPath(); ctx.arc(cx,cy,maxR,0,Math.PI*2); ctx.stroke();
  ctx.setLineDash([]);
}

function _polarZenith(ctx,cx,cy){
  ctx.fillStyle='#A855F7'; ctx.beginPath(); ctx.arc(cx,cy,3,0,Math.PI*2); ctx.fill();
}

function _el2r(el, maskDeg, maxR){ return Math.max(0,(90-el)/(90-maskDeg)*maxR); }

function _drawPolarCell(ctx,cx,cy,maxR,maskDeg,azC,elC,azStep,elStep,alpha,rgb){
  if(alpha<=0) return;
  const rInner=_el2r(Math.min(elC+elStep/2,90),maskDeg,maxR);
  const rOuter=_el2r(Math.max(elC-elStep/2,maskDeg),maskDeg,maxR);
  if(rOuter<=0) return;
  const a1=(azC-azStep/2-90)*Math.PI/180, a2=(azC+azStep/2-90)*Math.PI/180;
  ctx.fillStyle=`rgba(${rgb},${Math.min(alpha*0.9+0.05,0.92).toFixed(3)})`;
  ctx.beginPath();
  if(rInner<1){ ctx.moveTo(cx,cy); ctx.arc(cx,cy,rOuter,a1,a2,false); ctx.closePath(); }
  else { ctx.arc(cx,cy,rOuter,a1,a2,false); ctx.arc(cx,cy,rInner,a2,a1,true); ctx.closePath(); }
  ctx.fill();
}

function _drawPolarCellOutline(ctx,cx,cy,maxR,maskDeg,azC,elC,azStep,elStep,strokeOrFill){
  const rInner=_el2r(Math.min(elC+elStep/2,90),maskDeg,maxR);
  const rOuter=_el2r(Math.max(elC-elStep/2,maskDeg),maskDeg,maxR);
  if(rOuter<=0) return;
  const a1=(azC-azStep/2-90)*Math.PI/180, a2=(azC+azStep/2-90)*Math.PI/180;
  ctx.fillStyle=strokeOrFill;
  ctx.beginPath();
  if(rInner<1){ ctx.moveTo(cx,cy); ctx.arc(cx,cy,rOuter,a1,a2,false); ctx.closePath(); }
  else { ctx.arc(cx,cy,rOuter,a1,a2,false); ctx.arc(cx,cy,rInner,a2,a1,true); ctx.closePath(); }
  ctx.fill();
}

// ── 通用工具 ──────────────────────────────────────────────────────────────────
function _setupCanvas(cvs, w, h){
  const dpr = window.devicePixelRatio || 1;
  cvs.width=w*dpr; cvs.height=h*dpr;
  cvs.style.width=w+'px'; cvs.style.height=h+'px';
  cvs.getContext('2d').scale(dpr,dpr);
}
function _line(ctx,x1,y1,x2,y2){ ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke(); }

// ── 初始化 ────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded',()=>{
  _tick(); setInterval(_tick,1000);
  triggerCompute();
});
