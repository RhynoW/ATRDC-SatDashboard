'use strict';
/* StoryMaps 式敘事渲染器：
   /story        → 故事清單卡片
   /story/<id>   → 依 /api/story/<id> 之 JSON 渲染章節：
     text      {title, body}
     table     {title, columns, rows, row_anchors?, note?}
     sat       {title, norads[], body, anchor, start?, row?, autoplay?, height?}
     positions {title, body, mode, val?, ids?, sequence?, height?}   世界地圖位置分布
     embed     {title, body, url, height}                            任意頁面內嵌
   ?autoplay=<總秒數> → 自動導覽：依各節 dur 權重分配時間、平滑捲動走完全篇。 */

function esc(s){
  return String(s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function $id(i){ return document.getElementById(i); }

/* ── 資料口徑（provenance）：頁面載入時抓一次，供口徑列／各區塊註記 ── */
let PROV = null;
async function loadProv(){
  try{ PROV = await (await fetch('/api/story/provenance')).json(); }catch(e){ PROV = null; }
  return PROV;
}
function provHtml(){
  if(!PROV) return '';
  const age = PROV.tle_age_days == null ? '—' : PROV.tle_age_days + ' 天';
  const stale = PROV.tle_age_days != null && PROV.tle_age_days > 3;
  const row = (k, v) => '<div class="pv"><span class="k">' + k + '</span><span class="v">' + esc(v || '—') + '</span></div>';
  return '<details class="prov"' + (stale ? ' open' : '') + '><summary>資料口徑' +
    (stale ? '<b class="stale">⚠ TLE 資料齡 ' + age + '</b>' : '<span class="ok">TLE 最新 epoch ' + (PROV.tle_epoch_latest_past || PROV.tle_epoch_max || '').slice(0, 10) + '・資料齡 ' + age + '</span>') +
    '</summary><div class="pgrid">' +
    row('資料來源', PROV.source) + row('TLE epoch 範圍', (PROV.tle_epoch_min || '').slice(0, 10) + ' ～ ' + (PROV.tle_epoch_max || '').slice(0, 10) + '（' + fmtN(PROV.valid_sat_count) + ' 顆' + (PROV.tle_epoch_max > (PROV.tle_epoch_latest_past || '') ? '；含未來 epoch' : '') + '）') +
    row('資料庫更新', (PROV.db_updated_at || '').slice(0, 16).replace('T', ' ') + ' UTC') + row('傳播模型', PROV.propagator) +
    row('座標系', PROV.frame) + row('精度等級', PROV.accuracy) + row('碰撞機率', PROV.pc_model) + row('機動候選', PROV.maneuver_method) +
    '</div></details>';
}

/* ── 故事清單 ── */
async function renderList(){
  const wrap = $id('wrap');
  const r = await fetch('/api/story/list');
  const list = await r.json();
  // 清單頁不用滿頁吸附（否則 hero 100vh + mandatory snap 會把頁面吸回頂端、卡片永遠捲不到）
  document.documentElement.classList.add('nosnap');
  let h = '<div class="hero list"><h2>太空態勢敘事</h2>' +
          '<div class="sub">StoryMaps 式互動故事 — 以軌道資料說故事</div></div>' +
          '<div class="cards">';
  list.forEach(s => {
    h += '<a class="card" href="/story/' + esc(s.id) + '"><h4>' + esc(s.title) +
         '</h4><p>' + esc(s.subtitle) + '</p>' +
         (s.updated ? '<div class="up">更新：' + esc(s.updated) + '</div>' : '') + '</a>';
  });
  h += '</div>';
  if(!list.length) h += '<div style="color:#8b949e;padding:30px 0">尚無故事。</div>';
  wrap.innerHTML = h;
}

/* ── orbit 內嵌網址 ── */
function orbitUrl(norad, sec){
  let u = '/orbit?norad=' + norad + '&embed=1';
  if(sec.start) u += '&start=' + sec.start;
  if(sec.row) u += '&row=1';
  if(sec.autoplay) u += '&autoplay=1';
  return u;
}

function satFrame(sec, idx){
  const ns = sec.norads || [];
  const hgt = sec.height || (sec.row ? 760 : 1250);
  let btns = '';
  if(ns.length > 1){
    btns = ns.map((n, i) =>
      '<button class="nbtn' + (i === 0 ? ' on' : '') + '" data-fr="fr' + idx +
      '" data-norad="' + n + '" data-cfg="' + idx + '">NORAD ' + n + '</button>').join('');
  }else if(ns.length === 1){
    btns = '<span style="font-size:12.5px;color:#8b949e">NORAD ' + ns[0] + '</span>';
  }
  return '<div class="sat-head">' + btns +
    '<a class="open" href="/orbit?norad=' + ns[0] + '" target="_blank">開啟完整頁面 ↗</a></div>' +
    '<div class="frame" id="fr' + idx + '" data-src="' + orbitUrl(ns[0], sec) +
    '" data-h="' + hgt + '"><div class="ph">捲動至此載入軌道視圖…</div></div>';
}

/* 上一步／下一步：以視窗高度為一步（每節恰為 100vh；封面已併入第一節） */
function stepTargets(){ return [...document.querySelectorAll('.sec')]; }
function currentStep(){
  const ts = stepTargets(), y = window.scrollY + window.innerHeight * 0.3;
  let cur = 0;
  ts.forEach((t, i) => { if(t.offsetTop <= y) cur = i; });
  return cur;
}
function goStep(delta){
  const ts = stepTargets();
  const i = Math.max(0, Math.min(ts.length - 1, currentStep() + delta));
  ts[i].scrollIntoView({behavior: 'smooth', block: 'start'});
}
function setupStepNav(){
  const nav = $id('stepnav'); if(!nav) return;
  nav.style.display = '';
  $id('nav-prev').onclick = () => goStep(-1);
  $id('nav-next').onclick = () => goStep(1);
  const upd = () => {
    const i = currentStep(), n = stepTargets().length;
    $id('nav-prev').disabled = i <= 0; $id('nav-next').disabled = i >= n - 1;
  };
  window.addEventListener('scroll', upd, {passive: true}); upd();
  document.addEventListener('keydown', e => {
    if(e.target.matches('input,select,textarea,button')) return;
    if(e.key === 'ArrowDown' || e.key === 'PageDown' || e.key === ' '){ e.preventDefault(); goStep(1); }
    if(e.key === 'ArrowUp' || e.key === 'PageUp'){ e.preventDefault(); goStep(-1); }
  });
}

function frameHeight(px){ return Math.min(px || 1250, window.innerHeight - 150) + 'px'; }

function loadFrame(fr){
  if(fr.querySelector('iframe')) return;
  const ifr = document.createElement('iframe');
  ifr.src = fr.dataset.src;
  ifr.style.height = frameHeight(+fr.dataset.h);
  fr.appendChild(ifr);
  const ph = fr.querySelector('.ph'); if(ph) ph.remove();
}

/* ── positions 世界地圖／3D 球體 ── */
const PM = {};   // pmId -> {sats, names, focus, cv, seqTimer, …3D: units, rot}
const RE_KM = 6378.137;

async function initPosMap(box){
  if(box.classList.contains('inited')) return;
  box.classList.add('inited');
  const pmId = box.id;
  let url = '/api/story/positions?mode=' + box.dataset.mode;
  if(box.dataset.val) url += '&val=' + encodeURIComponent(box.dataset.val);
  if(box.dataset.ids) url += '&val=' + box.dataset.ids + '&mode=ids';
  let d;
  try{
    const r = await fetch(url);
    d = await r.json();
  }catch(e){ box.innerHTML = '<div class="ph">位置資料載入失敗</div>'; return; }
  const st = PM[pmId] = {sats: d.sats || [], names: d.names || {}, focus: -1};
  box.innerHTML = '<canvas></canvas>';
  st.cv = box.querySelector('canvas');
  const capEl = box.parentElement.querySelector('.pm-cap');
  if(capEl) capEl.textContent = '衛星數：' + d.count + '｜TLE 傳播位置，計算時刻 ' +
    new Date(d.timestamp).toISOString().slice(0, 16).replace('T', ' ') + ' UTC' +
    (PROV && PROV.tle_epoch_max ? '｜TLE 最新 epoch ' + (PROV.tle_epoch_latest_past || PROV.tle_epoch_max).slice(0, 10) + '（資料齡 ' + PROV.tle_age_days + ' 天）' : '');

  if(box.dataset.globe === '1'){ initGlobe3D(pmId); return; }

  // 依序聚焦模式：按鈕 + 自動輪播
  if(box.dataset.seq === '1' && st.sats.length){
    const bar = box.parentElement.querySelector('.pm-seq');
    bar.innerHTML = st.sats.map((s, i) =>
      '<button class="nbtn" data-i="' + i + '">' +
      esc(st.names[String(s[0])] || ('NORAD ' + s[0])) + '</button>').join('');
    bar.querySelectorAll('button').forEach(b =>
      b.addEventListener('click', () => {
        clearInterval(st.seqTimer);
        focusPos(pmId, +b.dataset.i);
      }));
    focusPos(pmId, 0);
    st.seqTimer = setInterval(() => {
      focusPos(pmId, (st.focus + 1) % st.sats.length);
    }, 3500);
  }
  const bg = new Image();
  bg.onload = () => { st.bg = bg; drawPosMap(pmId); };
  bg.onerror = () => drawPosMap(pmId);
  bg.src = '/api/globe_texture';
  drawPosMap(pmId);
}

/* ── 3D 球體（單純深色底、無貼圖、自動旋轉）── */
function initGlobe3D(pmId){
  const st = PM[pmId];
  // 預計算單位向量與徑向距離（地球半徑=1）
  st.units = st.sats.map(s => {
    const la = s[1] * Math.PI / 180, lo = s[2] * Math.PI / 180;
    return [Math.cos(la) * Math.cos(lo), Math.cos(la) * Math.sin(lo),
            Math.sin(la), 1 + s[3] / RE_KM];
  });
  st.rMax = Math.max(1.25, ...st.units.map(u => u[3]));
  // 經緯網格點（30° 間隔，每條 90 取樣）
  st.grid = [];
  for(let lo = 0; lo < 360; lo += 30)
    for(let j = 0; j < 90; j++){
      const la = (-90 + j * 2) * Math.PI / 180, lor = lo * Math.PI / 180;
      st.grid.push([Math.cos(la) * Math.cos(lor), Math.cos(la) * Math.sin(lor), Math.sin(la)]);
    }
  for(let la = -60; la <= 60; la += 30)
    for(let j = 0; j < 120; j++){
      const lar = la * Math.PI / 180, lor = j * 3 * Math.PI / 180;
      st.grid.push([Math.cos(lar) * Math.cos(lor), Math.cos(lar) * Math.sin(lor), Math.sin(lar)]);
    }
  st.rot = 0;
  let tick = 0;
  const loop = () => {
    if(!st.cv.isConnected) return;          // 節點移除即停
    if(tick++ % 2 === 0){ st.rot += 0.005 / 3; drawGlobe3D(pmId); }  // ~30fps，旋轉速度為原本 1/3
    requestAnimationFrame(loop);
  };
  loop();
}

function drawGlobe3D(pmId){
  const st = PM[pmId];
  const cv = st.cv, box = cv.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const w = box.clientWidth || 1100;
  const h = Math.max(240, Math.round(box.clientHeight > 60 ? box.clientHeight : Math.min(w * 0.52, 600)));
  if(cv.width !== w * dpr || cv.height !== h * dpr){ cv.width = w * dpr; cv.height = h * dpr; cv.style.height = h + 'px'; }
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  // 單純深色底
  ctx.fillStyle = '#05080f'; ctx.fillRect(0, 0, w, h);
  const cx = w / 2, cy = h / 2;
  const Rpx = (h / 2 - 16) / st.rMax;       // 最遠衛星恰好入框
  const cosR = Math.cos(st.rot), sinR = Math.sin(st.rot);
  const T = 20 * Math.PI / 180, cosT = Math.cos(T), sinT = Math.sin(T);
  const proj = (u) => {                      // 繞 Z 旋轉 → 繞水平軸傾角 → 正交投影
    const x1 = u[0] * cosR - u[1] * sinR, y1 = u[0] * sinR + u[1] * cosR, z1 = u[2];
    return [y1, -(z1 * cosT - x1 * sinT), x1 * cosT + z1 * sinT];  // [sx, sy, depth]
  };
  // 地球圓盤（微弱放射漸層）＋輪廓
  const g = ctx.createRadialGradient(cx - Rpx * .3, cy - Rpx * .3, Rpx * .1, cx, cy, Rpx);
  g.addColorStop(0, '#182234'); g.addColorStop(1, '#0b111d');
  ctx.fillStyle = g;
  ctx.beginPath(); ctx.arc(cx, cy, Rpx, 0, 2 * Math.PI); ctx.fill();
  ctx.strokeStyle = '#2b3a52'; ctx.lineWidth = 1;
  ctx.stroke();
  // 經緯網（正面）
  ctx.fillStyle = 'rgba(80,100,135,.5)';
  st.grid.forEach(u => {
    const p = proj(u);
    if(p[2] > 0) ctx.fillRect(cx + p[0] * Rpx - .5, cy + p[1] * Rpx - .5, 1, 1);
  });
  // 參考環：MEO（GPS 高度 ≈4.16 R⊕）與 GEO（≈6.61 R⊕），赤道面投影為橢圓
  const drawRing = (rr, color, label) => {
    if(rr > st.rMax + 0.3) return;
    ctx.strokeStyle = color; ctx.setLineDash([4, 5]); ctx.lineWidth = 1; ctx.beginPath();
    for(let k = 0; k <= 120; k++){
      const a = k / 120 * 2 * Math.PI, p = proj([Math.cos(a), Math.sin(a), 0]);
      const X = cx + p[0] * rr * Rpx, Y = cy + p[1] * rr * Rpx;
      k ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y);
    }
    ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = color; ctx.font = '10px Segoe UI'; ctx.textAlign = 'left';
    const p0 = proj([1, 0, 0]);
    ctx.fillText(label, cx + p0[0] * rr * Rpx + 4, cy + p0[1] * rr * Rpx - 4);
  };
  drawRing(4.164, 'rgba(63,185,80,.45)', 'MEO 20,200 km');
  drawRing(6.611, 'rgba(255,215,71,.45)', 'GEO 35,786 km');
  // 衛星（依軌道域配色）
  const big = st.sats.length > 200;
  const hasNames = Object.keys(st.names).length > 0;
  const REG = [['LEO', 2000, '#58d0ff'], ['MEO', 30000, '#3fb950'], ['GEO/IGSO', 40000, '#ffd747'], ['HEO/其他', 1e9, '#f778ba']];
  const regOf = alt => REG.find(r => alt < r[1]);
  const regCount = {};
  const MAX_LABELS = big ? 150 : 100000;           // 大星系：正面不重疊標籤上限
  const cellW = 74, cellH = 13, occ = new Set();   // 標籤佔位格（避免重疊）
  let nLabels = 0;
  const labels = [];
  for(let i = 0; i < st.units.length; i++){
    const u = st.units[i], p = proj(u), r = u[3];
    const X = cx + p[0] * r * Rpx, Y = cy + p[1] * r * Rpx;
    const inFront = p[2] > 0;
    const reg = regOf(st.sats[i][3]);
    regCount[reg[0]] = (regCount[reg[0]] || 0) + 1;
    const scrDist = Math.hypot(X - cx, Y - cy);
    if(!inFront && scrDist < Rpx) continue;          // 被地球遮蔽
    ctx.globalAlpha = inFront ? .92 : .35;
    ctx.fillStyle = reg[2];
    if(big) ctx.fillRect(X - 1, Y - 1, 2, 2);
    else{ ctx.beginPath(); ctx.arc(X, Y, 4, 0, 2 * Math.PI); ctx.fill(); }
    if(hasNames && inFront && nLabels < MAX_LABELS){
      const nm = st.names[String(st.sats[i][0])];
      if(!nm) continue;
      const gx = Math.floor((X + 7) / cellW), gy = Math.floor(Y / cellH);
      if(X + 7 > w - 4 || Y < 8 || Y > h - 4) continue;
      let clash = false;                              // 八方向鄰接格皆須空
      for(let dx = -1; dx <= 1 && !clash; dx++)
        for(let dy = -1; dy <= 1; dy++) if(occ.has((gx + dx) + ':' + (gy + dy))){ clash = true; break; }
      if(clash) continue;
      occ.add(gx + ':' + gy); nLabels++;
      labels.push([nm, X + 7, Y + 4]);
    }
  }
  ctx.globalAlpha = 1;
  ctx.fillStyle = big ? 'rgba(201,209,217,.85)' : '#e6edf3';
  ctx.font = (big ? '9.5px' : '11px') + ' Segoe UI'; ctx.textAlign = 'left';
  labels.forEach(([nm, x, y]) => ctx.fillText(nm, x, y));
  if(big && hasNames){
    ctx.fillStyle = '#6e7681'; ctx.font = '10.5px Segoe UI'; ctx.textAlign = 'right';
    ctx.fillText('標籤：正面且不重疊者，上限 ' + MAX_LABELS + '（隨旋轉輪替）', w - 8, h - 8);
  }
  // 圖例：軌道域顆數
  let ly = 14;
  ctx.font = '11px Segoe UI'; ctx.textAlign = 'left';
  REG.forEach(r => {
    const c = regCount[r[0]]; if(!c) return;
    ctx.fillStyle = r[2]; ctx.beginPath(); ctx.arc(14, ly, 4, 0, 2 * Math.PI); ctx.fill();
    ctx.fillStyle = '#c9d1d9'; ctx.fillText(r[0] + '  ' + fmtN(c), 24, ly + 4); ly += 17;
  });
}

function focusPos(pmId, i){
  const st = PM[pmId];
  st.focus = i;
  const bar = st.cv.closest('.pm-wrap').querySelector('.pm-seq');
  if(bar) bar.querySelectorAll('button').forEach((b, bi) =>
    b.classList.toggle('on', bi === i));
  drawPosMap(pmId);
}

function drawPosMap(pmId){
  const st = PM[pmId];
  const cv = st.cv, box = cv.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const w = box.clientWidth || 1100, h = Math.round(w / 2);
  cv.width = w * dpr; cv.height = h * dpr;
  cv.style.height = h + 'px';
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  // 底圖（equirectangular）或深色底＋經緯網
  if(st.bg){ ctx.globalAlpha = .85; ctx.drawImage(st.bg, 0, 0, w, h); ctx.globalAlpha = 1; }
  else{
    ctx.fillStyle = '#0a0f1a'; ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = '#1c2635'; ctx.lineWidth = 1;
    for(let lo = -150; lo <= 150; lo += 30){
      const x = (lo + 180) / 360 * w;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }
    for(let la = -60; la <= 60; la += 30){
      const y = (90 - la) / 180 * h;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
  }
  ctx.fillStyle = 'rgba(0,0,0,.25)'; ctx.fillRect(0, 0, w, h);
  const big = st.sats.length > 200;
  const seq = st.focus >= 0;
  st.sats.forEach((s, i) => {
    const x = (s[2] + 180) / 360 * w, y = (90 - s[1]) / 180 * h;
    if(seq && i !== st.focus){ ctx.fillStyle = 'rgba(139,148,158,.55)'; }
    else ctx.fillStyle = big ? 'rgba(88,208,255,.75)' : '#ffd747';
    ctx.beginPath(); ctx.arc(x, y, big ? 1.1 : 4, 0, 2 * Math.PI); ctx.fill();
    if(!big && !seq && st.names[String(s[0])]){
      ctx.fillStyle = '#e6edf3'; ctx.font = '11px Segoe UI'; ctx.textAlign = 'left';
      ctx.fillText(st.names[String(s[0])], x + 7, y + 4);
    }
  });
  if(seq){
    const s = st.sats[st.focus];
    const x = (s[2] + 180) / 360 * w, y = (90 - s[1]) / 180 * h;
    ctx.strokeStyle = '#ffd747'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, 9, 0, 2 * Math.PI); ctx.stroke();
    ctx.fillStyle = '#ffd747';
    ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 2 * Math.PI); ctx.fill();
    const nm = st.names[String(s[0])] || ('NORAD ' + s[0]);
    ctx.font = 'bold 13px Segoe UI'; ctx.textAlign = 'left'; ctx.fillStyle = '#fff';
    const lx = Math.min(x + 12, w - 220);
    ctx.fillText(nm, lx, Math.max(16, y - 12));
    ctx.font = '11.5px Segoe UI'; ctx.fillStyle = '#ffd747';
    ctx.fillText(s[1].toFixed(1) + '°, ' + s[2].toFixed(1) + '° · ' +
                 Math.round(s[3]).toLocaleString() + ' km', lx, Math.max(30, y + 4));
  }
}

/* ═══════════ 整合展示節型（懶載入：.lazy[data-kind]） ═══════════ */
function kpi(v, l, cls){
  return '<div class="kpi' + (cls ? ' ' + cls : '') + '"><div class="v">' + v + '</div><div class="l">' + esc(l) + '</div></div>';
}
function fmtN(n){ return Number(n).toLocaleString('zh-Hant'); }

/* 細長條圖（color 可為單色或逐根顏色陣列；值標籤：≤14 根全標，否則只標最大） */
function drawBars(cv, labels, values, color, unit){
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 500, h = 170;
  cv.width = w * dpr; cv.height = h * dpr; cv.style.height = h + 'px';
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const n = values.length; if(!n) return;
  const mx = Math.max(...values, 1);
  const padL = 8, padR = 8, padT = 18, padB = 26;
  const bw = (w - padL - padR) / n;
  ctx.strokeStyle = '#21262d'; ctx.beginPath(); ctx.moveTo(padL, h - padB); ctx.lineTo(w - padR, h - padB); ctx.stroke();
  ctx.font = '10.5px Segoe UI'; ctx.textAlign = 'center';
  const imax = values.indexOf(mx);
  values.forEach((v, i) => {
    const bh = (h - padT - padB) * v / mx;
    const x = padL + i * bw + bw * 0.15, y = h - padB - bh;
    ctx.fillStyle = Array.isArray(color) ? color[i] : color;
    ctx.beginPath(); ctx.roundRect(x, y, bw * 0.7, bh, [3, 3, 0, 0]); ctx.fill();
    if(n <= 14 || i === imax){
      ctx.fillStyle = '#c9d1d9';
      ctx.fillText(fmtN(v) + (unit || ''), x + bw * 0.35, Math.max(10, y - 4));
    }
    if(n <= 14 || i % Math.ceil(n / 12) === 0){
      ctx.fillStyle = '#8b949e';
      ctx.fillText(String(labels[i]), x + bw * 0.35, h - padB + 14);
    }
  });
}

async function initGroupStats(el){
  const r = await fetch('/api/story/group_stats?group=' + el.dataset.group);
  const d = await r.json();
  if(d.error){ el.innerHTML = '<div class="ph">' + esc(d.error) + '</div>'; return; }
  const reg = Object.entries(d.regimes).map(([k, v]) => k + ' ' + v).join(' · ');
  const years = Object.keys(d.launch_years), yv = Object.values(d.launch_years);
  const altK = Object.keys(d.alt_hist), altV = Object.values(d.alt_hist);
  el.innerHTML =
    '<div class="kpis">' + kpi(fmtN(d.n), '在軌物體（不含碎片／火箭體）') +
    kpi(esc(reg), '軌道域') + kpi(d.alt_median != null ? fmtN(Math.round(d.alt_median)) + ' km' : '—', '高度中位數') +
    kpi(years.length ? years[0] + '–' + years[years.length - 1] : '—', '發射年份範圍') + '</div>' +
    '<div class="two"><div class="bars"><h5>歷年發射數（依目錄發射日期）</h5><canvas id="' + el.id + '-y"></canvas></div>' +
    '<div class="bars"><h5>高度分佈（km；LEO 每 100 km 一格，MEO／GEO 各一格）</h5><canvas id="' + el.id + '-a"></canvas></div></div>' +
    '<div class="chips">' + d.sample.map(s => '<a class="chip" href="/orbit?norad=' + s.norad + '" target="_blank">' +
      esc(s.name) + ' ↗</a>').join('') + '</div>' +
    '<div class="note">點選衛星開啟逐日軌道歷史（SMA 圓形圖＋Spiral Polar，近一年）。</div>';
  drawBars($id(el.id + '-y'), years, yv, '#58a6ff');
  drawBars($id(el.id + '-a'), altK.map(k => k >= 20000 ? (k == 20000 ? 'MEO' : 'GEO') : k), altV,
           altK.map(k => k >= 30000 ? '#ffd747' : (k >= 20000 ? '#3fb950' : '#58d0ff')));
}

/* 章節總覽磚 */
function tocHtml(sec){
  return '<div class="toc">' + (sec.items || []).map(it =>
    '<a class="tile" href="#' + esc(it.anchor) + '"><div class="ic">' + (it.icon || '') + '</div>' +
    '<div class="t">' + esc(it.label) + '</div><div class="s">' + esc(it.sub || '') + '</div></a>').join('') + '</div>';
}

/* 偵照衛星感測器／光學解析度分類 */
const SENSOR_COLOR = {'光學': '#58a6ff', 'SAR': '#bc8cff', '射頻訊號': '#f0883e', '氣象掩星': '#39c5cf',
                      '技術試驗': '#8b949e', '導航': '#3fb950'};
async function initIsrRes(el){
  const d = await (await fetch('/api/story/isr_resolution?group=' + (el.dataset.group || 'prc_isr'))).json();
  if(d.error){ el.innerHTML = '<div class="ph">' + esc(d.error) + '</div>'; return; }
  const sk = Object.keys(d.sensor), sv = Object.values(d.sensor);
  const rk = Object.keys(d.resolution), rv = Object.values(d.resolution);
  el.innerHTML =
    '<div class="kpis">' + sk.map(k => '<div class="kpi"><div class="v" style="color:' + (SENSOR_COLOR[k] || '#e6edf3') + '">' +
      fmtN(d.sensor[k]) + '</div><div class="l">' + esc(k) + '</div></div>').join('') +
    (d.unknown ? kpi(fmtN(d.unknown), '未分類') : '') + '</div>' +
    '<div class="two"><div class="bars"><h5>感測器類型（顆）</h5><canvas id="' + el.id + '-s"></canvas></div>' +
    '<div class="bars"><h5>成像解析度級別（光學＋SAR，顆）</h5><canvas id="' + el.id + '-r"></canvas></div></div>' +
    '<table class="data"><tr><th>系列</th><th>顆</th><th>感測器</th><th>解析度級別</th><th>註記</th></tr>' +
    d.series.map(s => '<tr><td>' + esc(s.series) + '</td><td>' + s.n + '</td><td style="color:' + (SENSOR_COLOR[s.sensor] || '#c9d1d9') + '">' +
      esc(s.sensor) + '</td><td>' + esc(s.res) + '</td><td style="text-align:left;color:#8b949e">' + esc(s.note) + '</td></tr>').join('') +
    '</table><div class="note">' + esc(d.note) + '</div>';
  drawBars($id(el.id + '-s'), sk, sv, sk.map(k => SENSOR_COLOR[k] || '#8b949e'));
  const seq = ['#1f6feb', '#388bfd', '#58a6ff', '#79c0ff', '#a5d6ff', '#cae8ff'];
  const shortLbl = k => k.replace('（推估）', '*').replace('SAR ≈', 'SAR ').replace(/ m$/, '').replace(' m*', '*');
  drawBars($id(el.id + '-r'), rk.map(shortLbl), rv,
           rk.map((k, i) => k.startsWith('SAR') ? '#bc8cff' : seq[Math.min(i, seq.length - 1)]));
  el.querySelector('.note').textContent += '（圖中 * 為推估級別；單位 m）';
}

/* 前／後配對長條（雷達效益） */
function drawPaired(cv, labels, before, after, units){
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 500, h = 180;
  cv.width = w * dpr; cv.height = h * dpr; cv.style.height = h + 'px';
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const n = labels.length, padL = 8, padT = 20, padB = 30, gw = (w - padL - 8) / n;
  ctx.font = '10.5px Segoe UI'; ctx.textAlign = 'center';
  labels.forEach((lb, i) => {
    const mx = Math.max(before[i], after[i], 1e-9);
    [[before[i], '#6e7681', -1], [after[i], '#3fb950', 1]].forEach(([v, col, side]) => {
      const bh = (h - padT - padB) * v / mx, bw = gw * 0.28;
      const x = padL + i * gw + gw / 2 + (side < 0 ? -bw - 3 : 3), y = h - padB - bh;
      ctx.fillStyle = col; ctx.beginPath(); ctx.roundRect(x, y, bw, bh, [3, 3, 0, 0]); ctx.fill();
      ctx.fillStyle = '#c9d1d9'; ctx.fillText(v + (units[i] || ''), x + bw / 2, Math.max(10, y - 4));
    });
    ctx.fillStyle = '#8b949e'; ctx.fillText(lb, padL + i * gw + gw / 2, h - padB + 14);
  });
  ctx.textAlign = 'left'; ctx.fillStyle = '#6e7681'; ctx.fillRect(padL, h - 10, 10, 8); ctx.fillStyle = '#8b949e'; ctx.fillText('建立前', padL + 14, h - 2);
  ctx.fillStyle = '#3fb950'; ctx.fillRect(padL + 60, h - 10, 10, 8); ctx.fillStyle = '#8b949e'; ctx.fillText('建立後（＋台灣站）', padL + 74, h - 2);
}

async function initManeuvers(el){
  const r = await fetch('/api/story/maneuvers');
  const d = await r.json();
  if(d.error){ el.innerHTML = '<div class="ph">' + esc(d.error) + '</div>'; return; }
  const keys = (el.dataset.groups || '').split(',').filter(k => d.groups[k]);
  let h = '<div class="note">' + esc(d.method.stat) + '；大陸群組另含：' + esc(d.method.prc) + '</div>';
  keys.forEach((k, i) => {
    const g = d.groups[k];
    const months = Object.keys(g.monthly), mv = Object.values(g.monthly);
    const pct = g.n_sats ? Math.round(100 * g.n_sats_with_event / g.n_sats) : 0;
    h += '<h4 style="margin:16px 0 4px;font-size:14px;color:#e6edf3">' + esc(g.label) + '</h4>' +
      '<div class="kpis">' + kpi(fmtN(g.n_sats), '星系衛星數') + kpi(fmtN(g.n_events), '2026 機動候選事件') +
      kpi(fmtN(g.n_sats_with_event) + '（' + pct + '%）', '有事件之衛星') +
      (g.prc_pipeline ? kpi(fmtN(g.prc_pipeline.n_events), 'PRC 管線旗標事件（1–5 月）') : '') + '</div>' +
      '<div class="two"><div class="bars"><h5>月分佈</h5><canvas id="' + el.id + '-m' + i + '"></canvas></div>' +
      '<div><div class="note" style="margin:0 0 4px">最活躍衛星（事件數）</div><div class="chips">' +
      g.top.slice(0, 8).map(t => '<a class="chip" href="/orbit?norad=' + t.norad + '" target="_blank">' +
        esc(t.name) + ' · ' + t.events + '</a>').join('') + '</div></div></div>';
    if(g.events && g.events.length){
      h += '<details class="evd"><summary>最大 |Δa| 事件（前 ' + g.events.length + '）：前後 TLE epoch、間隔、等效 Δv</summary>' +
        '<table class="data"><tr><th>衛星</th><th>TLE 前</th><th>TLE 後</th><th>間隔 (h)</th><th>Δa (km)</th><th>等效 Δv (m/s)</th><th>軌道域</th></tr>' +
        g.events.map(e => '<tr><td><a href="/orbit?norad=' + e.norad + '&start=' + e.epoch_before.slice(0, 10) + '" target="_blank">' + esc(e.name) + '</a><br><span style="color:#6e7681">' + e.norad + '</span></td>' +
          '<td>' + e.epoch_before.slice(0, 16).replace('T', ' ') + '</td><td>' + e.epoch_after.slice(0, 16).replace('T', ' ') + '</td>' +
          '<td>' + e.gap_h + '</td><td>' + (e.da_km > 0 ? '+' : '') + e.da_km + '</td><td>' + e.dv_ms + '</td><td>' + e.regime + '</td></tr>').join('') +
        '</table></details>';
    }
  });
  h += '<div class="note">候選≠確認：Δv 為 Δa 以 Δv≈n·Δa/2 換算之等效值（假設切向脈衝）；替代解釋包括 TLE 品質波動／軌道決定更新、大氣阻力模型誤差（LEO）、資料缺漏造成之跳變。' +
       '確認機動需精密星曆或多來源交叉驗證。</div>';
  el.innerHTML = h;
  keys.forEach((k, i) => {
    const g = d.groups[k];
    drawBars($id(el.id + '-m' + i), Object.keys(g.monthly).map(m => m.slice(5)), Object.values(g.monthly), '#d29922');
  });
}

let RADAR = null;   // 快取 radar_eval 結果供 skyplot 選星
async function initRadar(el){
  const r = await fetch('/api/story/radar_eval?group=' + el.dataset.group + '&n=' + (el.dataset.n || 30));
  const d = await r.json();
  if(d.error){ el.innerHTML = '<div class="ph">' + esc(d.error) + '</div>'; return; }
  RADAR = d;
  const s = d.summary;
  const arrow = (a, b, unit, better) => a + unit + ' → <b style="color:' + (better ? '#3fb950' : '#e6edf3') + '">' + b + unit + '</b>';
  el.innerHTML =
    '<div class="kpis">' +
    kpi(arrow(s.arcs_before, s.arcs_after, '', s.arcs_after > s.arcs_before), '每日追蹤弧段（平均／顆）') +
    kpi(arrow(s.gap_max_before_min, s.gap_max_after_min, ' 分', s.gap_max_after_min < s.gap_max_before_min), '最大無觀測間隙') +
    kpi(arrow(s.track_min_before, s.track_min_after, ' 分', s.track_min_after > s.track_min_before), '累計追蹤時間／24 h') +
    kpi(s.taiwan_only_min + ' 分', '僅台灣站可見（全球站皆不可見）') +
    kpi('+' + s.precision_gain_pct + '%', '定軌精度提升（σ∝1/√N 代理）', 'gain') +
    kpi(s.sats_with_taiwan_arc + '/' + s.n_sats, '台灣站有弧段之衛星') + '</div>' +
    '<div class="bars"><h5>建立前 vs 建立後（樣本平均）</h5><canvas id="' + el.id + '-pb"></canvas></div>' +
    '<div class="two"><div><div class="bars"><h5>地面站佈局：全球已知 SSN 站（' + d.n_stations_before + '）＋台灣假想站</h5>' +
    '<canvas id="' + el.id + '-map"></canvas></div></div>' +
    '<div><table class="data"><tr><th>衛星</th><th>弧段 前→後</th><th>台灣弧段</th><th>最大間隙 前→後（分）</th><th>精度提升</th></tr>' +
    d.sats.slice(0, 8).map(x => '<tr><td><a href="/orbit?norad=' + x.norad + '" target="_blank">' + esc(x.name) + '</a></td>' +
      '<td>' + x.arcs_before + ' → ' + x.arcs_after + '</td><td>' + x.arcs_taiwan + '</td>' +
      '<td>' + x.gap_max_before_min + ' → ' + x.gap_max_after_min + '</td><td>+' + x.precision_gain_pct + '%</td></tr>').join('') +
    '</table></div></div>' +
    '<div class="note">' + esc(d.model_note) + ' 樣本：' + esc(d.label) + ' 低軌 ' + s.n_sats + ' 顆，評估起點 ' + d.t0 + '，仰角遮蔽 ' + d.mask_deg + '°。</div>';
  drawPaired($id(el.id + '-pb'), ['追蹤弧段／日', '最大間隙（分）', '累計追蹤（分）'],
             [s.arcs_before, s.gap_max_before_min, s.track_min_before],
             [s.arcs_after, s.gap_max_after_min, s.track_min_after], ['', '', '']);
  // 站點地圖
  const st = await (await fetch('/api/layers/ssn_stations')).json();
  const cv = $id(el.id + '-map'), dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 500, h = Math.round(w / 2);
  cv.width = w * dpr; cv.height = h * dpr; cv.style.height = h + 'px';
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = '#0a0f1a'; ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = '#1c2635';
  for(let lo = -150; lo <= 150; lo += 30){ const x = (lo + 180) / 360 * w; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
  for(let la = -60; la <= 60; la += 30){ const y = (90 - la) / 180 * h; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
  (st.features || []).forEach(f => {
    const [lon, lat] = f.geometry.coordinates;
    const x = (lon + 180) / 360 * w, y = (90 - lat) / 180 * h;
    ctx.fillStyle = '#58a6ff'; ctx.beginPath(); ctx.arc(x, y, 3.5, 0, 2 * Math.PI); ctx.fill();
  });
  const tw = d.taiwan_station, tx = (tw.lon + 180) / 360 * w, ty = (90 - tw.lat) / 180 * h;
  ctx.fillStyle = '#ffd747'; ctx.beginPath(); ctx.arc(tx, ty, 6, 0, 2 * Math.PI); ctx.fill();
  ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(tx, ty, 9, 0, 2 * Math.PI); ctx.stroke();
  ctx.fillStyle = '#ffd747'; ctx.font = 'bold 11px Segoe UI';
  const right = tx > w - 170;                     // 近右緣時標籤置左，避免裁切
  ctx.textAlign = right ? 'right' : 'left';
  ctx.fillText(tw.name, tx + (right ? -12 : 12), ty + 4);
}

async function initSkyplot(el){
  let sats = [];
  if(el.dataset.norads) sats = el.dataset.norads.split(',').map(x => ({norad: +x, name: 'NORAD ' + x}));
  else{
    if(!RADAR){ const r = await fetch('/api/story/radar_eval?group=' + el.dataset.group + '&n=30'); RADAR = await r.json(); }
    sats = (RADAR.sats || []).filter(x => x.arcs_taiwan > 0).slice(0, 4);
  }
  if(!sats.length){ el.innerHTML = '<div class="ph">無可示範衛星</div>'; return; }
  el.innerHTML = '<div class="pm-seq" id="' + el.id + '-btn"></div>' +
    '<div class="skywrap"><canvas id="' + el.id + '-sky"></canvas><div id="' + el.id + '-info"></div></div>';
  const bar = $id(el.id + '-btn');
  sats.forEach((s, i) => {
    const b = document.createElement('button'); b.className = 'nbtn' + (i === 0 ? ' on' : '');
    b.textContent = s.name; b.onclick = () => { bar.querySelectorAll('.nbtn').forEach(x => x.classList.toggle('on', x === b)); loadTrack(el, s.norad); };
    bar.appendChild(b);
  });
  loadTrack(el, sats[0].norad);
}

let SKY_TIMER = null;
async function loadTrack(el, norad){
  clearInterval(SKY_TIMER);
  const d = await (await fetch('/api/story/track?norad=' + norad)).json();
  const info = $id(el.id + '-info');
  if(d.error || !d.passes.length){ info.innerHTML = '<div class="ph">' + esc(d.error || '24 h 內無過頂') + '</div>'; return; }
  info.innerHTML = '<div class="kpis">' + kpi(esc(d.name), '衛星') + kpi(d.passes.length, '未來 24 h 過頂次數') +
    kpi(Math.max(...d.passes.map(p => p.max_el)).toFixed(1) + '°', '最高仰角') + '</div>' +
    '<table class="data"><tr><th>#</th><th>AOS（UTC）</th><th>LOS</th><th>最大仰角</th><th>時長</th></tr>' +
    d.passes.map((p, i) => '<tr><td>' + (i + 1) + '</td><td>' + p.aos.slice(5, 16).replace('T', ' ') + '</td><td>' +
      p.los.slice(11, 16) + '</td><td>' + p.max_el.toFixed(1) + '°</td><td>' + p.duration_min + ' 分</td></tr>').join('') + '</table>' +
    '<div class="note" id="' + el.id + '-cur">站點：' + esc(d.station.name) + '（' + d.station.lat + '°N, ' + d.station.lon + '°E）；遮蔽 ' + d.mask_deg + '°。</div>';
  const cv = $id(el.id + '-sky'), dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 380;
  cv.width = w * dpr; cv.height = w * dpr; cv.style.height = w + 'px';
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const cx = w / 2, cy = w / 2, R = w / 2 - 26;
  const xy = (az, elv) => { const rr = R * (90 - elv) / 90, a = az * Math.PI / 180; return [cx + rr * Math.sin(a), cy - rr * Math.cos(a)]; };
  const allPts = d.passes.flatMap((p, pi) => p.points.map(q => ({...q, pi})));
  let k = 0;
  const draw = () => {
    ctx.fillStyle = '#0a0f1a'; ctx.fillRect(0, 0, w, w);
    ctx.strokeStyle = '#21262d'; ctx.fillStyle = '#8b949e'; ctx.font = '10.5px Segoe UI'; ctx.textAlign = 'center';
    [0, 30, 60].forEach(e => { ctx.beginPath(); ctx.arc(cx, cy, R * (90 - e) / 90, 0, 2 * Math.PI); ctx.stroke(); ctx.fillText(e + '°', cx + 3, cy - R * (90 - e) / 90 + 11); });
    ['N', 'E', 'S', 'W'].forEach((t, i) => { const [x, y] = xy(i * 90, -8); ctx.fillText(t, x, y + 4); });
    ctx.beginPath(); ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy); ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R); ctx.stroke();
    d.passes.forEach((p, pi) => {
      ctx.strokeStyle = pi === 0 ? '#58a6ff' : '#3b4a5e'; ctx.lineWidth = pi === 0 ? 2 : 1.2; ctx.beginPath();
      p.points.forEach((q, i) => { const [x, y] = xy(q.az, q.el); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.stroke();
    });
    const q = allPts[k % allPts.length];
    const [x, y] = xy(q.az, q.el);
    ctx.fillStyle = '#ffd747'; ctx.beginPath(); ctx.arc(x, y, 5.5, 0, 2 * Math.PI); ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, 8, 0, 2 * Math.PI); ctx.stroke();
    ctx.strokeStyle = 'rgba(255,215,71,.5)'; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x, y); ctx.stroke(); ctx.setLineDash([]);
    const cur = $id(el.id + '-cur');
    if(cur) cur.textContent = '追蹤中（第 ' + (q.pi + 1) + ' 次過頂）' + q.t.slice(11, 19) + ' UTC · Az ' + q.az.toFixed(1) + '° · El ' + q.el.toFixed(1) + '° · 距離 ' + fmtN(Math.round(q.rng)) + ' km';
  };
  draw();
  SKY_TIMER = setInterval(() => { k++; draw(); }, 120);
}

async function initCdm(el){
  const thr = el.dataset.thr || 10;
  const d = await (await fetch('/api/conjunctions?threshold_km=' + thr + '&max_pairs=400')).json();
  const pairs = (d.pairs || []).filter(p => p.miss_km > 0.05)   // 排除對接／共位（距離≈0）
    .sort((a, b) => (b.Pc || 0) - (a.Pc || 0) || a.miss_km - b.miss_km).slice(0, 10);
  const lv = l => l === 'RED' ? '#f85149' : (l === 'AMBER' ? '#d29922' : '#3fb950');
  el.innerHTML = '<div class="kpis">' + kpi(fmtN(d.count), '<' + thr + ' km 幾何接近配對（TLE 傳播）') +
    kpi(fmtN(d.total_scanned), '掃描物體數') + kpi((d.elapsed_sec || 0) + ' s', '向量化 SGP4 掃描耗時') +
    kpi(pairs.filter(p => p.risk_level === 'RED').length + ' / ' + pairs.filter(p => p.risk_level === 'AMBER').length, 'RED / AMBER（前 10）') + '</div>' +
    '<table class="data"><tr><th>主體</th><th>次體</th><th>最接近距離</th><th>Pc（proxy）</th><th>等級</th><th></th></tr>' +
    pairs.map(p => '<tr><td>' + esc(p.primary_name) + '<br><span style="color:#6e7681">' + p.primary_norad + ' · ' + p.primary_alt_km + ' km</span></td>' +
      '<td>' + esc(p.secondary_name) + '<br><span style="color:#6e7681">' + p.secondary_norad + '</span></td>' +
      '<td>' + p.miss_km.toFixed(2) + ' km</td><td>' + p.Pc_str + '</td>' +
      '<td><b style="color:' + lv(p.risk_level) + '">' + p.risk_level + '</b></td>' +
      '<td><button class="nbtn" data-p="' + p.primary_norad + '" data-s="' + p.secondary_norad + '">3D 展開</button></td></tr>').join('') +
    '</table><div class="note">幾何篩選（<' + thr + ' km）≠ 碰撞風險：Pc 為 Chan (2008) 2-D 近似，σ R/T/N 為固定假設值（' + (PROV ? PROV.pc_model.replace(/^.*σ/, 'σ') : '100/500/100 m') + '），非 CDM 協方差，僅供排序；已排除距離≈0 之對接／共位配對。</div>' +
    '<div class="frame" id="' + el.id + '-fr" style="display:none" data-h="820"></div>';
  el.querySelectorAll('button[data-p]').forEach(b => b.addEventListener('click', () => {
    const fr = $id(el.id + '-fr'); fr.style.display = ''; fr.querySelectorAll('iframe').forEach(f => f.remove());
    const ifr = document.createElement('iframe'); ifr.src = '/rpo?primary=' + b.dataset.p + '&secondary=' + b.dataset.s;
    ifr.style.height = frameHeight(820); fr.appendChild(ifr); fr.scrollIntoView({behavior: 'smooth', block: 'start'});
    el.querySelectorAll('button[data-p]').forEach(x => x.classList.toggle('on', x === b));
  }));
  if(pairs.length && new URLSearchParams(location.search).get('autoplay')) el.querySelector('button[data-p]').click();
}

const LAZY_INIT = {groupstats: initGroupStats, maneuvers: initManeuvers, radar: initRadar,
                   skyplot: initSkyplot, cdm: initCdm, isrres: initIsrRes};
function initLazy(el){
  if(el.classList.contains('inited')) return;
  el.classList.add('inited');
  const fn = LAZY_INIT[el.dataset.kind];
  if(fn) fn(el).catch(e => { el.innerHTML = '<div class="ph">載入失敗：' + esc(String(e)) + '</div>'; });
}

/* ── 單一故事 ── */
async function renderStory(sid){
  const wrap = $id('wrap');
  const r = await fetch('/api/story/' + encodeURIComponent(sid));
  if(!r.ok){ wrap.innerHTML = '<div style="padding:60px 0">故事不存在。<a href="/story">回清單</a></div>'; return; }
  const st = await r.json();
  await loadProv();
  document.title = st.title + ' — Story';
  $id('hdr-title').textContent = st.title;
  $id('lnk-list').style.display = '';

  // 封面（標題／副標／說明）併入第一節上方，不再獨立佔一整頁（滿頁吸附下獨立封面會卡在第一頁）
  const heroHtml = '<div class="hero-in"><h2>' + esc(st.title) + '</h2>' +
          '<div class="sub">' + esc(st.subtitle || '') + '</div>' +
          (st.hero_note ? '<div class="note">' + esc(st.hero_note) + '</div>' : '') + provHtml() + '</div>';
  let h = '';

  const SECS = st.sections || [];
  const dotIds = [];
  SECS.forEach((sec, i) => {
    const aid = sec.anchor || ('sec' + i);
    dotIds.push({id: aid, title: sec.title || ''});
    h += '<div class="sec' + (i === 0 ? ' first vis' : '') + '" id="' + esc(aid) + '" data-dur="' + (sec.dur || 1) + '" data-type="' + esc(sec.type || 'text') + '">' +
         (i === 0 ? heroHtml : '') +
         '<h3>' + esc(sec.title || '') + '</h3>';
    if(sec.body) h += '<div class="body">' + esc(sec.body) + '</div>';
    h += '<div class="sec-body">';
    if(sec.type === 'toc'){
      h += tocHtml(sec);
    }else if(sec.type === 'table'){
      h += '<table class="launch"><tr>' +
           sec.columns.map(c => '<th>' + esc(c) + '</th>').join('') + '</tr>';
      sec.rows.forEach((row, ri) => {
        const anchor = (sec.row_anchors || [])[ri] || '';
        h += '<tr class="rw"' + (anchor ? ' data-go="' + esc(anchor) + '"' : '') + '>' +
             row.map(c => '<td>' + esc(c) + '</td>').join('') + '</tr>';
      });
      h += '</table>';
      if(sec.note) h += '<div class="tbl-note">' + esc(sec.note) + '</div>';
    }else if(sec.type === 'sat'){
      h += satFrame(sec, i);
    }else if(sec.type === 'positions'){
      h += '<div class="pm-wrap"><div class="pm-seq"></div>' +
           '<div class="posmap" id="pm' + i + '" data-mode="' + esc(sec.mode || 'all') + '"' +
           (sec.val ? ' data-val="' + esc(sec.val) + '"' : '') +
           (sec.ids ? ' data-ids="' + sec.ids.join(',') + '"' : '') +
           (sec.sequence ? ' data-seq="1"' : '') +
           (sec.globe ? ' data-globe="1"' : '') +
           '><div class="ph">捲動至此載入位置分布…</div></div>' +
           '<div class="pm-cap"></div></div>';
    }else if(sec.type === 'embed'){
      h += '<div class="frame" data-src="' + esc(sec.url) + '" data-h="' +
           (sec.height || 860) + '"><div class="ph">捲動至此載入…</div></div>';
    }else if(LAZY_INIT[sec.type]){
      h += '<div class="lazy" id="lz' + i + '" data-kind="' + sec.type + '"' +
           (sec.group ? ' data-group="' + esc(sec.group) + '"' : '') +
           (sec.groups ? ' data-groups="' + sec.groups.join(',') + '"' : '') +
           (sec.n ? ' data-n="' + sec.n + '"' : '') +
           (sec.norads ? ' data-norads="' + sec.norads.join(',') + '"' : '') +
           (sec.threshold_km ? ' data-thr="' + sec.threshold_km + '"' : '') +
           '><div class="ph">捲動至此載入…</div></div>';
    }
    h += '</div></div>';
  });
  wrap.innerHTML = h;
  setupStepNav();

  // NORAD 對應設定保存（sat 多星切換用）
  const cfgMap = {}; SECS.forEach((sec, i) => cfgMap[i] = sec);

  const dots = $id('dots');
  dots.innerHTML = dotIds.map(d =>
    '<a href="#' + esc(d.id) + '" title="' + esc(d.title) + '" data-for="' + esc(d.id) + '"></a>').join('');

  wrap.querySelectorAll('tr.rw[data-go]').forEach(tr =>
    tr.addEventListener('click', () => {
      const el = document.getElementById(tr.dataset.go);
      if(el) el.scrollIntoView({behavior: 'smooth'});
    }));

  wrap.querySelectorAll('.nbtn[data-fr]').forEach(b =>
    b.addEventListener('click', () => {
      const fr = document.getElementById(b.dataset.fr);
      fr.querySelectorAll('iframe').forEach(f => f.remove());
      const ifr = document.createElement('iframe');
      ifr.src = orbitUrl(b.dataset.norad, cfgMap[b.dataset.cfg] || {});
      ifr.style.height = frameHeight(+fr.dataset.h);
      fr.appendChild(ifr);
      const ph = fr.querySelector('.ph'); if(ph) ph.remove();
      fr.parentElement.querySelectorAll('.nbtn').forEach(x => x.classList.toggle('on', x === b));
    }));

  const io = new IntersectionObserver(entries => {
    entries.forEach(en => {
      if(en.isIntersecting){
        en.target.classList.add('vis');
        const fr = en.target.querySelector('.frame');
        if(fr) loadFrame(fr);
        const pm = en.target.querySelector('.posmap');
        if(pm) initPosMap(pm);
        const lz = en.target.querySelector('.lazy');
        if(lz) initLazy(lz);
        dots.querySelectorAll('a').forEach(a =>
          a.classList.toggle('on', a.dataset.for === en.target.id));
      }
    });
  }, {rootMargin: '300px 0px -30% 0px', threshold: 0.02});
  wrap.querySelectorAll('.sec').forEach(s => io.observe(s));

  // ── 自動導覽（?autoplay=<總秒數>）──
  const total = parseFloat(new URLSearchParams(location.search).get('autoplay') || '0');
  if(total > 0) startTour(total);
}

function startTour(totalSec){
  const secs = [...document.querySelectorAll('.sec')];
  if(!secs.length) return;
  const heroDur = 0;   // 封面已併入第一節
  const weights = secs.map(s => parseFloat(s.dataset.dur) || 1);
  const wsum = weights.reduce((a, b) => a + b, 0);
  const durs = weights.map(w => (totalSec - heroDur) * w / wsum);
  let i = -1;
  function next(){
    i += 1;
    if(i >= secs.length) return;
    secs[i].scrollIntoView({behavior: 'smooth', block: 'start'});
    setTimeout(next, durs[i] * 1000);
  }
  window.scrollTo(0, 0);
  setTimeout(next, heroDur * 1000);
}

if(window.STORY_ID) renderStory(window.STORY_ID);
else renderList();
