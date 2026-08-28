'use strict';
/* 軌道要素歷史頁：
   - Spiral Polar ×3（inclination / raan / argp）：角度=要素值、半徑=時間螺旋、顏色=時間
     （仿 maneuver_STEM 之 prepare_spiral_polar_data：r = a + b·days）
   - SMA 圓形時間圖：0°=起始（12 點鐘位置）順時針至 360°=結束，
     r =（當日 SMA − 全期最小值）/（全期最大 − 最小）正規化
   - 時間播放：▶ 撥放時四張圖同步高亮「當日」資料點（未來點淡出、當日白圈＋指引線） */

let SEL = null;          // {norad, name}
let DATA = null;         // /api/orbit/history 回應
let HI = null;           // 目前高亮之日索引（null=無）
let TIMER = null;        // 播放計時器
let AUTOPLAY = false;    // ?autoplay=1：載入後自動播放
const PTS = {};          // canvasId -> [{x,y,label}] 供 hover

const SPIRAL_A = 0.18, SPIRAL_B_FRAC = 0.82;   // r_norm = A + B·(day/maxDay)
const DIM = '#2d333b';                          // 播放時未來點的淡出色

function $(id){ return document.getElementById(id); }

/* ── 顏色：時間 → HSV 色環 ── */
function timeColor(t){ return 'hsl(' + Math.round(t * 330) + ',85%,55%)'; }

/* ── canvas 準備（方形、HiDPI）── */
function setupCanvas(cv){
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 340;
  cv.width = w * dpr; cv.height = w * dpr;
  cv.style.height = w + 'px';
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, w);
  return {ctx, w, cx: w / 2, cy: w / 2, R: w / 2 - 34};
}

function polarGrid(g, spokeLabelFn){
  const {ctx, cx, cy, R} = g;
  ctx.strokeStyle = '#21262d'; ctx.fillStyle = '#8b949e';
  ctx.font = '10.5px Segoe UI'; ctx.lineWidth = 1;
  [1/3, 2/3, 1].forEach(f => {
    ctx.beginPath(); ctx.arc(cx, cy, R * f, 0, 2 * Math.PI); ctx.stroke();
  });
  for(let a = 0; a < 360; a += 45){
    const rad = a * Math.PI / 180;
    ctx.beginPath(); ctx.moveTo(cx, cy);
    ctx.lineTo(cx + R * Math.cos(rad), cy - R * Math.sin(rad)); ctx.stroke();
    if(a % 90 === 0 && spokeLabelFn){
      ctx.textAlign = 'center';
      ctx.fillText(spokeLabelFn(a),
        cx + (R + 16) * Math.cos(rad), cy - (R + 16) * Math.sin(rad) + 4);
    }
  }
}

/* ── 當日高亮：白圈＋圓心指引線 ── */
function highlightPoint(g, p, t){
  const {ctx, cx, cy} = g;
  ctx.save();
  ctx.strokeStyle = 'rgba(139,148,158,.55)';
  ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(p.x, p.y); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = timeColor(t);
  ctx.beginPath(); ctx.arc(p.x, p.y, 5, 0, 2 * Math.PI); ctx.fill();
  ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(p.x, p.y, 6.5, 0, 2 * Math.PI); ctx.stroke();
  ctx.restore();
}

/* ── Spiral Polar：角度=要素值（數學慣例 0°=右、逆時針）、半徑=時間 ── */
function drawSpiral(cvId, capId, values, dates, hi){
  const cv = $(cvId), g = setupCanvas(cv);
  polarGrid(g, a => a + '°');
  const n = values.length;
  const pts = [];
  for(let i = 0; i < n; i++){
    const t = n > 1 ? i / (n - 1) : 0;
    const rN = SPIRAL_A + SPIRAL_B_FRAC * t;
    const rad = (values[i] % 360) * Math.PI / 180;
    const x = g.cx + g.R * rN * Math.cos(rad);
    const y = g.cy - g.R * rN * Math.sin(rad);
    g.ctx.fillStyle = (hi != null && i > hi) ? DIM : timeColor(t);
    g.ctx.beginPath(); g.ctx.arc(x, y, 2.6, 0, 2 * Math.PI); g.ctx.fill();
    pts.push({x, y, label: dates[i] + ' · ' + values[i].toFixed(4) + '°'});
  }
  if(hi != null && hi < n){
    highlightPoint(g, pts[hi], n > 1 ? hi / (n - 1) : 0);
    $(capId).textContent = pts[hi].label;
  }
  PTS[cvId] = pts;
  attachHover(cv, cvId, capId);
}

/* ── SMA 圓形時間圖：0°=起始（上方）順時針、r=正規化 SMA 變化 ── */
function drawSmaCircle(cvId, capId, sma, dates, hi){
  const cv = $(cvId), g = setupCanvas(cv);
  const {ctx, cx, cy, R} = g;
  const mn = Math.min(...sma), mx = Math.max(...sma), rg = mx - mn;

  ctx.strokeStyle = '#21262d'; ctx.fillStyle = '#8b949e';
  ctx.font = '10.5px Segoe UI'; ctx.lineWidth = 1;
  [1/3, 2/3, 1].forEach(f => {
    ctx.beginPath(); ctx.arc(cx, cy, R * f, 0, 2 * Math.PI); ctx.stroke();
    ctx.textAlign = 'left';
    ctx.fillText((mn + rg * f).toFixed(1), cx + R * f + 3, cy - 3);
  });
  ctx.fillText(mn.toFixed(1) + ' km (min)', cx + 4, cy + 12);

  // 時間刻度：0/90/180/270° → 起始與四分位日期
  const n = sma.length;
  [0, 0.25, 0.5, 0.75].forEach(f => {
    const rad = 2 * Math.PI * f;                       // 0=上方、順時針
    const x2 = cx + R * Math.sin(rad), y2 = cy - R * Math.cos(rad);
    ctx.strokeStyle = '#30363d';
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x2, y2); ctx.stroke();
    const di = Math.min(n - 1, Math.round(f * (n - 1)));
    const lb = f === 0 ? dates[di] : dates[di].slice(5);   // 起始給全日期，餘 MM-DD
    ctx.fillStyle = '#8b949e';
    ctx.textAlign = f === 0.25 ? 'left' : (f === 0.75 ? 'right' : 'center');
    ctx.fillText(lb,
      cx + (R + 8) * Math.sin(rad), cy - (R + 14) * Math.cos(rad) + 4);
  });

  const pts = [];
  ctx.lineWidth = 1.4;
  let prev = null;
  for(let i = 0; i < n; i++){
    const t = n > 1 ? i / (n - 1) : 0;
    const rN = rg > 0 ? (sma[i] - mn) / rg : 0.5;
    const rad = 2 * Math.PI * t;
    const x = cx + R * rN * Math.sin(rad);
    const y = cy - R * rN * Math.cos(rad);
    if(prev){
      ctx.strokeStyle = (hi != null && i > hi) ? DIM : timeColor(t);
      ctx.beginPath(); ctx.moveTo(prev.x, prev.y); ctx.lineTo(x, y); ctx.stroke();
    }
    prev = {x, y};
    pts.push({x, y, label: dates[i] + ' · SMA ' + sma[i].toFixed(3) +
              ' km（min+' + (sma[i] - mn).toFixed(3) + '）'});
  }
  for(let i = 0; i < n; i++){                          // 點在線之上
    const t = n > 1 ? i / (n - 1) : 0;
    ctx.fillStyle = (hi != null && i > hi) ? DIM : timeColor(t);
    ctx.beginPath(); ctx.arc(pts[i].x, pts[i].y, 2.4, 0, 2 * Math.PI); ctx.fill();
  }
  if(hi != null && hi < n){
    highlightPoint(g, pts[hi], n > 1 ? hi / (n - 1) : 0);
    $(capId).textContent = pts[hi].label;
  }
  PTS[cvId] = pts;
  attachHover(cv, cvId, capId);
}

function attachHover(cv, cvId, capId){
  if(cv._hoverBound) return;
  cv._hoverBound = true;
  cv.addEventListener('mousemove', e => {
    const rc = cv.getBoundingClientRect();
    const mx = e.clientX - rc.left, my = e.clientY - rc.top;
    let best = null, bd = 100;
    for(const p of (PTS[cvId] || [])){
      const d = (p.x - mx) ** 2 + (p.y - my) ** 2;
      if(d < bd){ bd = d; best = p; }
    }
    if(best) $(capId).textContent = best.label;
  });
}

/* ── 全部重繪（hi=高亮日索引或 null）── */
function redrawAll(hi){
  if(!DATA) return;
  HI = hi;
  drawSmaCircle('cv-sma', 'cap-sma', DATA.sma_km, DATA.dates, hi);
  drawSpiral('cv-inc', 'cap-inc', DATA.inclination_deg, DATA.dates, hi);
  drawSpiral('cv-raan', 'cap-raan', DATA.raan_deg, DATA.dates, hi);
  drawSpiral('cv-argp', 'cap-argp', DATA.argp_deg, DATA.dates, hi);
  updateCur(hi);
}

function updateCur(hi){
  if(hi == null || !DATA){ $('cur').textContent = ''; return; }
  const d = DATA;
  $('cur').textContent = d.dates[hi] +
    ' · SMA ' + d.sma_km[hi].toFixed(2) + ' km' +
    ' · 傾角 ' + d.inclination_deg[hi].toFixed(3) + '°' +
    ' · RAAN ' + d.raan_deg[hi].toFixed(2) + '°' +
    ' · ARGP ' + d.argp_deg[hi].toFixed(2) + '°';
}

/* ── 播放控制 ── */
function setPlaying(on){
  if(TIMER){ clearInterval(TIMER); TIMER = null; }
  $('play').innerHTML = on ? '&#10074;&#10074;' : '&#9654;';   // ⏸ / ▶
  if(!on || !DATA) return;
  const tl = $('tl');
  if(+tl.value >= +tl.max) tl.value = 0;                       // 播畢重播
  const spd = +$('spd').value;                                  // 天/秒
  TIMER = setInterval(() => {
    const next = +tl.value + 1;
    if(next > +tl.max){ setPlaying(false); return; }
    tl.value = next;
    redrawAll(next);
  }, Math.max(25, Math.round(1000 / spd)));
}

function bindPlayer(){
  $('play').addEventListener('click', () => setPlaying(TIMER === null));
  $('tl').addEventListener('input', () => redrawAll(+$('tl').value));
  $('spd').addEventListener('change', () => { if(TIMER) setPlaying(true); });
}

/* ── 衛星基本資料卡 ── */
function renderSatInfo(d){
  const box = $('satinfo');
  const inf = d.info || {};
  const last = d.n_days - 1;
  const aKm = d.sma_km[last];
  const period = 2 * Math.PI * Math.sqrt(Math.pow(aKm, 3) / 398600.4418) / 60;
  const rows = [
    ['NORAD', d.norad],
    ['國際編號', inf.intl_code],
    ['國家', inf.country],
    ['用途', inf.purpose],
    ['星座', inf.constellation],
    ['操作單位', inf.operator],
    ['發射日期', inf.launch_date],
    ['年代', inf.era],
    ['最新高度', (aKm - 6378.137).toFixed(0) + ' km'],
    ['最新傾角', d.inclination_deg[last].toFixed(2) + '°'],
    ['軌道週期', period.toFixed(1) + ' 分'],
  ].filter(r => r[1] !== '' && r[1] != null);
  const alt = (inf.name_zh && inf.name_en && inf.name_zh !== inf.name_en)
    ? '<span>' + inf.name_en + '</span>' : '';
  box.innerHTML = '<div class="ttl">' + (inf.name || d.name) + alt + '</div>' +
    '<div class="grid">' +
    rows.map(r => '<div><b>' + r[0] + '</b>' + r[1] + '</div>').join('') +
    '</div>' +
    (inf.notes ? '<div class="notes">' + inf.notes + '</div>' : '');
  box.style.display = 'block';
}

/* ── 統計表 ── */
function renderStats(d){
  const rows = [
    ['半長軸 SMA (km)', d.stats.sma_km],
    ['傾角 inclination (°)', d.stats.inclination_deg],
    ['升交點赤經 RAAN (°)', d.stats.raan_deg],
    ['近地點幅角 ARGP (°)', d.stats.argp_deg],
  ];
  let h = '<table><tr><th>要素</th><th>最小</th><th>最大</th><th>變化範圍</th>' +
          '<th>平均</th><th>標準差</th></tr>';
  rows.forEach(([lb, s]) => {
    h += '<tr><td>' + lb + '</td><td>' + s.min + '</td><td>' + s.max +
         '</td><td>' + s.range + '</td><td>' + s.mean + '</td><td>' + s.std + '</td></tr>';
  });
  h += '</table>';
  $('stats').innerHTML = h;
}

/* ── 載入 ── */
async function load(){
  if(!SEL){ $('status').textContent = '請先搜尋並選取衛星'; return; }
  setPlaying(false);
  $('status').textContent = '載入中…';
  const u = '/api/orbit/history?norad=' + SEL.norad +
            '&start=' + $('d0').value + '&end=' + $('d1').value;
  try{
    const r = await fetch(u);
    const d = await r.json();
    if(!r.ok){ $('status').textContent = d.error || ('HTTP ' + r.status); return; }
    DATA = d;
    $('status').innerHTML = '<b>' + d.name + '</b>（NORAD ' + d.norad + '）　' +
      d.start + ' ~ ' + d.end + '　逐日資料 <b>' + d.n_days + '</b> 天' +
      (d.note ? '　<span style="color:#d29922">' + d.note + '</span>' : '');
    const tl = $('tl');
    tl.max = d.n_days - 1; tl.value = 0;
    $('player').style.display = 'flex';
    renderSatInfo(d);
    redrawAll(null);
    renderStats(d);
    if(AUTOPLAY) setPlaying(true);
  }catch(e){ $('status').textContent = '載入失敗：' + e; }
}

/* ── 搜尋 ── */
let _timer = null;
function bindSearch(){
  const q = $('q'), sug = $('sug');
  q.addEventListener('input', () => {
    clearTimeout(_timer);
    const v = q.value.trim();
    if(v.length < 2){ sug.style.display = 'none'; return; }
    _timer = setTimeout(async () => {
      try{
        const r = await fetch('/api/search?q=' + encodeURIComponent(v));
        const d = await r.json();
        sug.innerHTML = '';
        (d.results || []).forEach(m => {
          const it = document.createElement('div');
          it.className = 'it';
          it.innerHTML = '<b>' + m.name + '</b><span>NORAD ' + m.norad_id +
                         ' · ' + (m.constellation || '—') + '</span>';
          it.onclick = () => {
            SEL = {norad: m.norad_id, name: m.name};
            q.value = m.name;
            $('sel-info').textContent = 'NORAD ' + m.norad_id;
            sug.style.display = 'none';
            load();
          };
          sug.appendChild(it);
        });
        sug.style.display = (d.results || []).length ? 'block' : 'none';
      }catch(e){ sug.style.display = 'none'; }
    }, 300);
  });
  document.addEventListener('click', e => {
    if(!sug.contains(e.target) && e.target !== q) sug.style.display = 'none';
  });
}

function init(){
  const today = new Date();
  const past = new Date(today.getTime() - 365 * 86400 * 1000);
  $('d1').value = today.toISOString().slice(0, 10);
  $('d0').value = past.toISOString().slice(0, 10);
  bindSearch();
  bindPlayer();
  $('go').addEventListener('click', load);
  const qs = new URLSearchParams(location.search);
  if(qs.get('embed') === '1'){        // 故事頁內嵌模式：隱藏頁首/搜尋列
    $('hdr').style.display = 'none';
    $('ctrl').style.display = 'none';
  }
  if(qs.get('row') === '1'){          // 四張 Polar 圖排成一列
    document.getElementById('grid').style.gridTemplateColumns = 'repeat(4,1fr)';
  }
  AUTOPLAY = qs.get('autoplay') === '1';
  const D = /^\d{4}-\d{2}-\d{2}$/;
  if(D.test(qs.get('start') || '')) $('d0').value = qs.get('start');
  if(D.test(qs.get('end') || ''))   $('d1').value = qs.get('end');
  const urlNorad = qs.get('norad');
  if(urlNorad && /^\d+$/.test(urlNorad)){
    SEL = {norad: parseInt(urlNorad), name: 'NORAD ' + urlNorad};
    $('sel-info').textContent = 'NORAD ' + urlNorad;
    load();
  }
  window.addEventListener('resize', () => redrawAll(HI));
}
init();
