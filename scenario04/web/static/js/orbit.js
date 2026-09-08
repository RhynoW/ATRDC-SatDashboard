'use strict';
/* 軌道要素歷史頁：
   - Spiral Polar ×3（inclination / raan / argp）：角度=要素值、半徑=時間螺旋、顏色=時間
     （仿 maneuver_STEM 之 prepare_spiral_polar_data：r = a + b·days）
   - SMA 圓形時間圖：0°=起始（12 點鐘位置）順時針至 360°=結束，
     r =（當日 SMA − 全期最小值）/（全期最大 − 最小）正規化
   - 時間播放：▶ 撥放時四張圖同步高亮「當日」資料點（未來點淡出、當日白圈＋指引線） */

// ── 三語字典（中／英／日）──────────────────────────────────────────────────
const I18N = {
  zh: {
    doc_title: '軌道要素歷史 — Spiral Polar',
    page_title: '軌道要素歷史',
    subtitle: 'Spiral Polar（傾角／升交點赤經／近地點幅角）＋ SMA 圓形圖',
    nav_home: '返回主頁',
    q_placeholder: '衛星名稱或 NORAD ID（至少 2 字元）',
    lbl_start: '起', lbl_end: '迄', btn_load: '載入',
    status_default: '請先搜尋並選取衛星（預設日期範圍：最近 1 年）',
    status_select_first: '請先搜尋並選取衛星',
    status_loading: '載入中…',
    status_load_fail: '載入失敗：{error}',
    status_loaded: '<b>{name}</b>（NORAD {norad}）　{start} ~ {end}　逐日資料 <b>{n}</b> 天',
    tip_play: '播放/暫停',
    spd_5: '5 天/秒', spd_10: '10 天/秒', spd_20: '20 天/秒', spd_40: '40 天/秒',
    card_sma_title: '半長軸變化（圓形時間圖）',
    card_sma_hint: '0°=起始時間、順時針演進至 360°=結束；半徑 r =（當日 SMA − 全期最小值）正規化',
    card_inc_title: '傾角 inclination（Spiral Polar）',
    card_inc_hint: '角度=傾角值、半徑=時間螺旋（內→外）、顏色=時間',
    card_raan_title: '升交點赤經 RAAN（Spiral Polar）',
    card_raan_hint: '角度=RAAN 值、半徑=時間螺旋（內→外）、顏色=時間',
    card_argp_title: '近地點幅角 ARGP（Spiral Polar）',
    card_argp_hint: '角度=ARGP 值、半徑=時間螺旋（內→外）、顏色=時間',
    info_norad: 'NORAD', info_intl_code: '國際編號', info_country: '國家',
    info_purpose: '用途', info_constellation: '星座', info_operator: '操作單位',
    info_launch_date: '發射日期', info_era: '年代',
    info_alt: '最新高度', info_inc: '最新傾角', info_period: '軌道週期',
    unit_min: '分', label_inc_short: '傾角',
    stats_th_element: '要素', stats_th_min: '最小', stats_th_max: '最大',
    stats_th_range: '變化範圍', stats_th_mean: '平均', stats_th_std: '標準差',
    stats_row_sma: '半長軸 SMA (km)', stats_row_inc: '傾角 inclination (°)',
    stats_row_raan: '升交點赤經 RAAN (°)', stats_row_argp: '近地點幅角 ARGP (°)',
  },
  en: {
    doc_title: 'Orbital Element History — Spiral Polar',
    page_title: 'Orbital Element History',
    subtitle: 'Spiral Polar (Inclination / RAAN / Argument of Perigee) + SMA Circular Chart',
    nav_home: 'Home',
    q_placeholder: 'Satellite name or NORAD ID (2+ characters)',
    lbl_start: 'From', lbl_end: 'To', btn_load: 'Load',
    status_default: 'Please search and select a satellite first (default range: last 1 year)',
    status_select_first: 'Please search and select a satellite first',
    status_loading: 'Loading…',
    status_load_fail: 'Load failed: {error}',
    status_loaded: '<b>{name}</b> (NORAD {norad})   {start} ~ {end}   <b>{n}</b> days of daily data',
    tip_play: 'Play / Pause',
    spd_5: '5 days/s', spd_10: '10 days/s', spd_20: '20 days/s', spd_40: '40 days/s',
    card_sma_title: 'Semi-Major Axis Change (Circular Time Chart)',
    card_sma_hint: '0° = start time, progressing clockwise to 360° = end; radius r = normalized (daily SMA − period minimum)',
    card_inc_title: 'Inclination (Spiral Polar)',
    card_inc_hint: 'Angle = inclination value, radius = time spiral (inner→outer), color = time',
    card_raan_title: 'RAAN (Spiral Polar)',
    card_raan_hint: 'Angle = RAAN value, radius = time spiral (inner→outer), color = time',
    card_argp_title: 'Argument of Perigee ARGP (Spiral Polar)',
    card_argp_hint: 'Angle = ARGP value, radius = time spiral (inner→outer), color = time',
    info_norad: 'NORAD', info_intl_code: 'International Designator', info_country: 'Country',
    info_purpose: 'Purpose', info_constellation: 'Constellation', info_operator: 'Operator',
    info_launch_date: 'Launch Date', info_era: 'Era',
    info_alt: 'Latest Altitude', info_inc: 'Latest Inclination', info_period: 'Orbital Period',
    unit_min: 'min', label_inc_short: 'Inclination',
    stats_th_element: 'Element', stats_th_min: 'Min', stats_th_max: 'Max',
    stats_th_range: 'Range', stats_th_mean: 'Mean', stats_th_std: 'Std Dev',
    stats_row_sma: 'Semi-Major Axis SMA (km)', stats_row_inc: 'Inclination (°)',
    stats_row_raan: 'RAAN (°)', stats_row_argp: 'Argument of Perigee ARGP (°)',
  },
  ja: {
    doc_title: '軌道要素履歴 — Spiral Polar',
    page_title: '軌道要素履歴',
    subtitle: 'Spiral Polar（軌道傾斜角／昇交点赤経／近地点引数）＋ SMA 円形図',
    nav_home: 'ホームに戻る',
    q_placeholder: '衛星名または NORAD ID（2文字以上）',
    lbl_start: '開始', lbl_end: '終了', btn_load: '読み込み',
    status_default: 'まず衛星を検索して選択してください（デフォルト期間：直近1年）',
    status_select_first: 'まず衛星を検索して選択してください',
    status_loading: '読み込み中…',
    status_load_fail: '読み込みに失敗しました：{error}',
    status_loaded: '<b>{name}</b>（NORAD {norad}）　{start} ～ {end}　日次データ <b>{n}</b> 日分',
    tip_play: '再生 / 一時停止',
    spd_5: '5日/秒', spd_10: '10日/秒', spd_20: '20日/秒', spd_40: '40日/秒',
    card_sma_title: '半長軸変化（円形タイムチャート）',
    card_sma_hint: '0°＝開始時刻、時計回りに進み 360°＝終了；半径 r ＝（当日の SMA － 全期間の最小値）で正規化',
    card_inc_title: '軌道傾斜角 inclination（Spiral Polar）',
    card_inc_hint: '角度＝傾斜角の値、半径＝時間スパイラル（内→外）、色＝時間',
    card_raan_title: '昇交点赤経 RAAN（Spiral Polar）',
    card_raan_hint: '角度＝RAAN 値、半径＝時間スパイラル（内→外）、色＝時間',
    card_argp_title: '近地点引数 ARGP（Spiral Polar）',
    card_argp_hint: '角度＝ARGP 値、半径＝時間スパイラル（内→外）、色＝時間',
    info_norad: 'NORAD', info_intl_code: '国際符号', info_country: '国',
    info_purpose: '用途', info_constellation: 'コンステレーション', info_operator: '運用機関',
    info_launch_date: '打上げ日', info_era: '年代',
    info_alt: '最新高度', info_inc: '最新傾斜角', info_period: '軌道周期',
    unit_min: '分', label_inc_short: '傾斜角',
    stats_th_element: '要素', stats_th_min: '最小', stats_th_max: '最大',
    stats_th_range: '変化範囲', stats_th_mean: '平均', stats_th_std: '標準偏差',
    stats_row_sma: '半長軸 SMA (km)', stats_row_inc: '軌道傾斜角 inclination (°)',
    stats_row_raan: '昇交点赤経 RAAN (°)', stats_row_argp: '近地点引数 ARGP (°)',
  },
};
const ORBIT_LOCALE_MAP = {zh: 'zh-TW', en: 'en-US', ja: 'ja-JP'};
let LANG = localStorage.getItem('orbit_lang') || 'zh';
if(!I18N[LANG]) LANG = 'zh';

function t(key){
  const d = I18N[LANG] || I18N.zh;
  return (key in d) ? d[key] : (I18N.zh[key] !== undefined ? I18N.zh[key] : key);
}
function tpl(key, vars){
  let s = t(key);
  Object.keys(vars || {}).forEach(k => { s = s.replace(new RegExp('\\{' + k + '\\}', 'g'), vars[k]); });
  return s;
}

/* ── 已載入時的狀態列文字（依目前語言重新組字，資料本身不翻譯）── */
function renderLoadedStatus(d){
  let s = tpl('status_loaded', {name: d.name, norad: d.norad, start: d.start, end: d.end, n: d.n_days});
  if(d.note) s += '　<span style="color:#d29922">' + d.note + '</span>';
  $('status').innerHTML = s;
}

function setLang(lang){
  if(!I18N[lang]) return;
  LANG = lang;
  localStorage.setItem('orbit_lang', lang);
  document.documentElement.lang = ORBIT_LOCALE_MAP[lang] || 'zh-TW';
  document.title = t('doc_title');

  document.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.getAttribute('data-i18n'));
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.title = t(el.getAttribute('data-i18n-title'));
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.getAttribute('data-i18n-placeholder'));
  });
  document.querySelectorAll('.lang-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.lang === lang);
  });

  // 若已載入資料，重繪所有用到語系字串的動態內容
  if(DATA){
    renderLoadedStatus(DATA);
    renderSatInfo(DATA);
    renderStats(DATA);
    redrawAll(HI);
  }
}

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
    ' · ' + t('label_inc_short') + ' ' + d.inclination_deg[hi].toFixed(3) + '°' +
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
    [t('info_norad'), d.norad],
    [t('info_intl_code'), inf.intl_code],
    [t('info_country'), inf.country],
    [t('info_purpose'), inf.purpose],
    [t('info_constellation'), inf.constellation],
    [t('info_operator'), inf.operator],
    [t('info_launch_date'), inf.launch_date],
    [t('info_era'), inf.era],
    [t('info_alt'), (aKm - 6378.137).toFixed(0) + ' km'],
    [t('info_inc'), d.inclination_deg[last].toFixed(2) + '°'],
    [t('info_period'), period.toFixed(1) + ' ' + t('unit_min')],
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
    [t('stats_row_sma'), d.stats.sma_km],
    [t('stats_row_inc'), d.stats.inclination_deg],
    [t('stats_row_raan'), d.stats.raan_deg],
    [t('stats_row_argp'), d.stats.argp_deg],
  ];
  let h = '<table><tr><th>' + t('stats_th_element') + '</th><th>' + t('stats_th_min') +
          '</th><th>' + t('stats_th_max') + '</th><th>' + t('stats_th_range') + '</th>' +
          '<th>' + t('stats_th_mean') + '</th><th>' + t('stats_th_std') + '</th></tr>';
  rows.forEach(([lb, s]) => {
    h += '<tr><td>' + lb + '</td><td>' + s.min + '</td><td>' + s.max +
         '</td><td>' + s.range + '</td><td>' + s.mean + '</td><td>' + s.std + '</td></tr>';
  });
  h += '</table>';
  $('stats').innerHTML = h;
}

/* ── 載入 ── */
async function load(){
  if(!SEL){ $('status').textContent = t('status_select_first'); return; }
  setPlaying(false);
  $('status').textContent = t('status_loading');
  const u = '/api/orbit/history?norad=' + SEL.norad +
            '&start=' + $('d0').value + '&end=' + $('d1').value;
  try{
    const r = await fetch(u);
    const d = await r.json();
    if(!r.ok){ $('status').textContent = d.error || ('HTTP ' + r.status); return; }
    DATA = d;
    renderLoadedStatus(d);
    const tl = $('tl');
    tl.max = d.n_days - 1; tl.value = 0;
    $('player').style.display = 'flex';
    renderSatInfo(d);
    redrawAll(null);
    renderStats(d);
    if(AUTOPLAY) setPlaying(true);
  }catch(e){ $('status').textContent = tpl('status_load_fail', {error: e}); }
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
  setLang(LANG);
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
