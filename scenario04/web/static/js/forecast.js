/* 過頂預報頁（線上版，api/forecast.py）：群組、更名、常用中心點存在瀏覽器（satadmin_store.js），隨請求送到伺服器，伺服器不保存 */
(function () {
  'use strict';
  var JOB = null, TIMER = null, S = window.SAStore;
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function msg(id, t, ok) { var el = $(id); el.textContent = t || ''; el.className = 'msg ' + (ok ? 'ok' : 'bad'); }
  function api(method, url, body) {
    var o = { method: method, headers: {} };
    if (body !== undefined) { o.body = JSON.stringify(body); o.headers['Content-Type'] = 'application/json'; }
    return fetch(url, o).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; });
    });
  }
  function tzh() { return parseFloat($('tz').value); }
  function fmt(iso) {
    if (!iso) return '';
    var d = new Date(new Date(iso).getTime() + tzh() * 3600000);
    return d.toISOString().replace('T', ' ').slice(5, 19);
  }

  // ── 中心點 ──
  function siteRow(s) {
    var tr = document.createElement('tr');
    tr.innerHTML = '<td><input class="s-name" style="width:130px"/></td><td><input class="s-lat" type="number" step="0.0001" style="width:100px"/></td>' +
      '<td><input class="s-lon" type="number" step="0.0001" style="width:100px"/></td><td><input class="s-alt" type="number" step="1" style="width:80px"/></td>' +
      '<td><input class="s-r" type="number" min="0" max="3000" step="1" style="width:80px"/></td><td><input class="s-note" style="width:160px"/></td>' +
      '<td><button class="dan">刪除</button></td>';
    tr.querySelector('.s-name').value = s.name || '';
    tr.querySelector('.s-lat').value = s.lat != null ? s.lat : '';
    tr.querySelector('.s-lon').value = s.lon != null ? s.lon : '';
    tr.querySelector('.s-alt').value = s.alt_m || 0;
    tr.querySelector('.s-r').value = s.radius_km || 0;
    tr.querySelector('.s-note').value = s.note || '';
    tr.querySelector('button').onclick = function () { tr.remove(); };
    document.querySelector('#sites tbody').appendChild(tr);
  }
  function sites() {
    return Array.prototype.map.call(document.querySelectorAll('#sites tbody tr'), function (tr) {
      var g = function (c) { return tr.querySelector(c).value; };
      return { name: g('.s-name').trim(), lat: parseFloat(g('.s-lat')), lon: parseFloat(g('.s-lon')), alt_m: parseFloat(g('.s-alt')) || 0, radius_km: parseFloat(g('.s-r')) || 0, note: g('.s-note') };
    });
  }
  function addSite() { siteRow({ name: '', lat: '', lon: '', alt_m: 0, radius_km: 0 }); }
  function saveSites() {
    var s = sites();
    if (s.some(function (x) { return !x.name || isNaN(x.lat) || isNaN(x.lon); })) { msg('site-msg', '每個中心點都需要名稱與經緯度'); return; }
    if (S.saveSites(s)) msg('site-msg', '已儲存 ' + s.length + ' 個常用中心點（本瀏覽器）', true); else msg('site-msg', '瀏覽器無法儲存');
  }

  // ── 群組 ──
  var SENS = { optical: '光學', radar: '雷達', sigint: '電子偵察', other: '其他' };
  function loadGroups() {
    var gs = S.data().groups.map(function (g) { return { id: g.id, name: g.name, path: S.path(g.id), count_total: S.allMembers(g.id).length, eff_sensor: S.effSensor(g.id), eff_look_deg: S.effLook(g.id) }; })
      .sort(function (a, b) { return a.path.localeCompare(b.path, 'zh-Hant'); });
    $('groups').innerHTML = gs.length ? gs.map(function (g) {
      var depth = (g.path.match(/ \/ /g) || []).length;
      return '<label style="padding-left:' + (depth * 18) + 'px"><input type="checkbox" class="g-chk" value="' + g.id + '"/> ' + esc(g.name) +
        ' <span class="muted">（' + g.count_total + ' 顆' + (g.eff_sensor ? '・' + (SENS[g.eff_sensor] || g.eff_sensor) : '') + (g.eff_look_deg != null ? '・偵照 ' + g.eff_look_deg + '°' : '') + '）</span></label>';
    }).join('') + '<div class="muted" style="margin-top:4px">勾選上層群組即包含所有子群組。</div>' :
      '尚無群組，可到「衛星管理」建立，或直接在下方輸入 NORAD ID。';
  }
  /* 勾選的群組（含子群組）→ 衛星清單，每顆帶直屬群組的路徑、感測器、偵照角度 */
  function selectedSats(groups) {
    var out = {}, list = [];
    groups.forEach(function (gid) {
      S.descendants(gid).forEach(function (sub) {
        var g = S.group(sub); if (!g) return;
        g.members.forEach(function (n) {
          if (out[n]) return;
          out[n] = 1; list.push({ norad: n, group: S.path(sub), sensor: S.effSensor(sub), look_deg: S.effLook(sub) });
        });
      });
    });
    return list;
  }

  // ── 執行 ──
  function setLook(v) { $('look').value = v; }
  function run() {
    var s = sites();
    if (!s.length) { msg('run-msg', '請至少設定一個中心點'); return; }
    if (s.some(function (x) { return !x.name || isNaN(x.lat) || isNaN(x.lon); })) { msg('run-msg', '每個中心點都需要名稱與經緯度'); return; }
    var groups = Array.prototype.map.call(document.querySelectorAll('.g-chk:checked'), function (c) { return c.value; });
    var norads = ($('norads').value.match(/\d+/g) || []).map(Number);
    var start = null;
    if ($('start').value) {
      var local = new Date($('start').value + 'Z').getTime() - tzh() * 3600000;
      start = new Date(local).toISOString().replace('.000Z', 'Z');
    }
    var names = {}; Object.keys(S.data().names).forEach(function (k) { names[k] = S.data().names[k].name; });
    var body = { sites: s, sats: selectedSats(groups), names: names, norads: norads, start: start, days: parseFloat($('days').value), mask_deg: parseFloat($('mask').value),
      look_deg: parseFloat($('look').value), look_override: $('look-ov').checked, only_coverable: $('only-cov').checked, daylight: $('daylight').value,
      sun_min_deg: parseFloat($('sunmin').value) || 0, tz_hours: tzh(), error_estimate: $('err').checked, history_days: parseFloat($('hist').value) || 60 };
    $('run').disabled = true; msg('run-msg', '已送出…', true); $('prog').style.width = '0';
    api('POST', '/api/forecast/run', body).then(function (j) { JOB = j.job_id; poll(); })
      .catch(function (e) { msg('run-msg', e.message); $('run').disabled = false; });
  }
  function poll() {
    clearTimeout(TIMER);
    api('GET', '/api/forecast/jobs/' + JOB).then(function (j) {
      $('prog').style.width = (j.total ? Math.round(100 * j.done / j.total) : 0) + '%';
      $('stage').textContent = j.stage + '（' + j.done + ' / ' + j.total + ' 顆）';
      if (j.state === 'running') { TIMER = setTimeout(poll, 800); return; }
      $('run').disabled = false;
      if (j.state === 'error') { msg('run-msg', j.error); return; }
      msg('run-msg', '完成', true); $('stage').textContent = '';
      show(j);
    }).catch(function (e) { msg('run-msg', e.message); $('run').disabled = false; });
  }
  function show(j) {
    var s = j.summary;
    $('result').style.display = '';
    var base = '/api/forecast/jobs/' + j.id + '/';
    $('dl-passes').href = base + 'passes.csv'; $('dl-sun').href = base + 'sun.csv'; $('dl-err').href = base + 'errors.csv';
    $('sum').innerHTML = '期間 ' + esc(fmt(s.start)) + ' ～ ' + esc(fmt(s.end)) + '（' + s.days + ' 天）・' + s.sats + ' 顆衛星・' + s.sites + ' 個中心點・共 <b>' + s.passes.toLocaleString() + '</b> 筆過頂' +
      (s.not_in_catalog.length ? '<div class="muted">不在目前目錄、未計算：' + s.not_in_catalog.join(', ') + '</div>' : '') +
      '<table style="width:auto;margin-top:8px"><thead><tr><th>中心點</th><th>過頂</th><th>可偵照</th><th>日照</th></tr></thead><tbody>' +
      s.per_site.map(function (p) { return '<tr><td>' + esc(p.site) + '</td><td class="num">' + p.passes + '</td><td class="num">' + p.coverable + '</td><td class="num">' + p.sunlit + '</td></tr>'; }).join('') + '</tbody></table>';
    var fe = s.fleet_error || [];
    $('errbox').innerHTML = fe.length ? '<div class="muted" style="margin:8px 0 4px">歷史 TLE 預報誤差（所選衛星合併）：</div><table style="width:auto;margin-bottom:10px"><thead><tr><th>提前天數</th><th>樣本</th><th>位置誤差中位數 km</th><th>位置 p90 km</th><th>時間誤差中位數 s</th><th>時間 p90 s</th></tr></thead><tbody>' +
      fe.map(function (e) { return '<tr><td class="num">' + e.lead_days + '</td><td class="num">' + e.n + '</td><td class="num">' + e.pos_med_km + '</td><td class="num">' + e.pos_p90_km + '</td><td class="num">' + e.time_med_s + '</td><td class="num">' + e.time_p90_s + '</td></tr>'; }).join('') +
      '</tbody></table>' : '';
    document.querySelector('#pv tbody').innerHTML = (j.preview || []).map(function (p) {
      var img = p.coverable ? fmt(p.img_start).slice(6) + '–' + fmt(p.img_end).slice(6) + '（' + p.img_duration_s + ' s）' : '<span class="no">—</span>';
      return '<tr><td>' + esc(p.site) + '</td><td class="num">' + p.norad_id + '</td><td>' + esc(p.name) + '</td><td>' + esc(p.group) + '</td><td>' + fmt(p.start) + '</td><td>' + fmt(p.tca) +
        '</td><td>' + fmt(p.end).slice(6) + '</td><td class="num">' + p.max_el_deg + '°</td><td class="num">' + p.min_off_nadir_deg + '°</td><td class="num">' + p.look_deg + '°</td><td>' + img +
        '</td><td class="' + (p.sunlit ? 'yes' : 'no') + '">' + (p.sunlit ? '日照' : '夜間') + '</td><td class="num">' + p.sun_el_deg + '°</td><td class="num">' + (p.est_err_s != null ? '±' + p.est_err_s : '') + '</td></tr>';
    }).join('') || '<tr><td colspan="14" class="muted">期間內沒有符合條件的過頂</td></tr>';
  }

  window.FC = { addSite: addSite, saveSites: saveSites, run: run, setLook: setLook };
  var saved = S.loadSites();
  if (saved && saved.length) saved.forEach(siteRow);
  else api('GET', '/api/forecast/sites').then(function (j) { j.sites.forEach(siteRow); });
  loadGroups();
})();
