/* 衛星管理頁（線上版）：更名與群組存在瀏覽器（satadmin_store.js），查詢／DECAY／星歷狀態走伺服器唯讀 API（api/satadmin.py） */
(function () {
  'use strict';
  var S = window.SAStore, SEL = null, LAST_IDS = [], CACHE = {};
  var SENSOR_LABEL = { '': '（沿用上層）', optical: '光學', radar: '雷達', sigint: '電子偵察', other: '其他' };
  var ST_LABEL = { decayed: '已再入', stale: 'TLE 停更', no_satcat: 'SATCAT 查無', ok: '正常' };
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function fmtT(s) { return s ? String(s).replace('T', ' ').replace(/(\.\d+)?(Z|[+-]\d\d:\d\d)$/, '') : '—'; }
  function msg(id, t, ok) { var el = $(id); if (!el) return; el.textContent = t || ''; el.className = 'msg ' + (ok ? 'ok' : 'bad'); }
  function api(method, url, body, hdr) {
    var o = { method: method, headers: hdr || {} };
    if (body !== undefined) { o.body = JSON.stringify(body); o.headers['Content-Type'] = 'application/json'; }
    return fetch(url, o).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; });
    });
  }
  function download(name, text, type) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: type || 'text/csv;charset=utf-8' }));
    a.download = name; document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }
  function stamp() { var d = new Date(); return d.getFullYear() + ('0' + (d.getMonth() + 1)).slice(-2) + ('0' + d.getDate()).slice(-2) + '_' + ('0' + d.getHours()).slice(-2) + ('0' + d.getMinutes()).slice(-2); }
  function stBadge(r, showOk) {
    if (!r || !r.status || (r.status === 'ok' && !showOk)) return '';
    var tip = r.status === 'decayed' ? 'DECAY ' + (r.decay_date || '') : (r.tle_age_days != null ? 'TLE ' + r.tle_age_days + ' 天前' : '');
    return ' <span class="st st-' + r.status + '" title="' + esc(tip) + '">' + ST_LABEL[r.status] + (r.status === 'decayed' && r.decay_date ? ' ' + esc(r.decay_date) : '') + '</span>';
  }
  function dispName(r) { var c = S.data().names[r.norad_id]; return c ? c.name : r.name; }

  document.querySelectorAll('#tabs button').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('#tabs button').forEach(function (x) { x.classList.toggle('on', x === b); });
      document.querySelectorAll('.tab').forEach(function (x) { x.classList.toggle('on', x.id === 't-' + b.dataset.t); });
      if (b.dataset.t === 'groups') renderGroups();
      if (b.dataset.t === 'decay') decayTab();
      if (b.dataset.t === 'ephem') loadEphem();
    });
  });

  // ── 查詢與更名 ──
  function fetchRows(ids) {
    if (!ids.length) return Promise.resolve([]);
    var out = [], chunks = [];
    for (var i = 0; i < ids.length; i += 400) chunks.push(ids.slice(i, i + 400));
    return Promise.all(chunks.map(function (c) { return api('GET', '/api/satadmin/lookup?norad=' + c.join(',')); })).then(function (rs) {
      rs.forEach(function (j) { j.results.forEach(function (r) { CACHE[r.norad_id] = r; out.push(r); }); });
      return out;
    });
  }
  function renderRows(rows) {
    LAST_IDS = rows.map(function (r) { return r.norad_id; });
    var tb = document.querySelector('#res tbody'), names = S.data().names;
    if (!rows.length) { tb.innerHTML = '<tr><td colspan="8" class="muted">沒有符合的衛星</td></tr>'; return; }
    tb.innerHTML = rows.map(function (r) {
      var c = names[r.norad_id];
      var nm = c ? '<span class="cust" title="自訂名稱（' + esc(fmtT(c.at)) + '）">✎ ' + esc(c.name) + '</span>' + (c.status && c.status !== 'manual' ? ' <span class="muted">[' + esc(c.status) + (c.confidence ? '/' + esc(c.confidence) : '') + ']</span>' : '') : esc(r.name);
      nm += stBadge(r);
      if (c && c.note) nm += '<div class="muted">' + esc(c.note) + '</div>';
      var grp = S.groupsOf(r.norad_id).map(function (g) { return '<span class="tag">' + esc(g) + '</span>'; }).join('');
      return '<tr id="r-' + r.norad_id + '"><td class="num">' + r.norad_id + '</td><td>' + nm + '</td><td>' + esc((c && c.orig) || r.name || '—') +
        (r.in_catalog ? '' : ' <span class="muted">（不在目前目錄）</span>') + '</td><td>' + esc(r.intl_code || '—') + '</td><td class="num">' + esc(fmtT(r.tle_epoch)) +
        '</td><td>' + esc(r.country || '—') + '／' + esc(r.purpose || '—') + '</td><td>' + grp + '</td><td style="white-space:nowrap">' +
        '<button onclick="SA.rename(' + r.norad_id + ')">更名</button> ' + (c ? '<button class="dan" onclick="SA.unrename(' + r.norad_id + ')">還原</button> ' : '') +
        '<button onclick="SA.addToGroup(' + r.norad_id + ')">加入群組</button> <button onclick="SA.loadSuggest([' + r.norad_id + '])">建議名稱</button></td></tr>';
    }).join('');
  }
  function lookup(text) {
    var q = text != null ? text : $('q').value.trim();
    if (text != null) $('q').value = q;
    if (!q) return;
    var ids = /^[\d\s,，、]+$/.test(q) ? (q.match(/\d+/g) || []).map(Number) : null;
    msg('q-msg', '查詢中…', true);
    var p = ids ? fetchRows(ids) : api('GET', '/api/satadmin/lookup?q=' + encodeURIComponent(q)).then(function (j) {
      j.results.forEach(function (r) { CACHE[r.norad_id] = r; }); if (j.truncated) msg('q-msg', '僅列前 200 筆，請縮小關鍵字', true); return j.results;
    });
    p.then(function (rows) { renderRows(rows); if (!$('q-msg').textContent.startsWith('僅列')) msg('q-msg', '共 ' + rows.length + ' 筆', true); })
      .catch(function (e) { msg('q-msg', e.message); });
  }
  function relookup() { fetchRows(LAST_IDS).then(renderRows); }
  function rename(n) {
    var r = CACHE[n] || {}, c = S.data().names[n] || {};
    var td = $('r-' + n).children[1];
    td.innerHTML = '<input id="rn-' + n + '" style="width:100%" maxlength="64"/><input id="rnn-' + n + '" style="width:100%;margin-top:4px" placeholder="備註（可空白）"/>' +
      '<div style="margin-top:4px"><button class="pri" id="rns-' + n + '">儲存</button> <button id="rnc-' + n + '">取消</button> <span class="msg" id="rnm-' + n + '"></span></div>';
    $('rn-' + n).value = c.name || r.name || ''; $('rnn-' + n).value = c.note || '';
    $('rn-' + n).focus(); $('rn-' + n).select();
    $('rnc-' + n).onclick = relookup;
    var save = function () {
      try { S.rename(n, $('rn-' + n).value, $('rnn-' + n).value, r.name); msg('q-msg', 'NORAD ' + n + ' 已更名（存在這個瀏覽器）', true); relookup(); }
      catch (e) { msg('rnm-' + n, e.message); }
    };
    $('rns-' + n).onclick = save;
    $('rn-' + n).onkeydown = function (e) { if (e.key === 'Enter') save(); if (e.key === 'Escape') relookup(); };
  }
  function unrename(n) { if (confirm('還原 NORAD ' + n + ' 為 Space-Track 原名？')) { S.unrename(n); relookup(); } }
  function loadDup() {
    var box = $('dup'); box.style.display = ''; box.innerHTML = '<span class="muted">載入中…</span>';
    api('GET', '/api/satadmin/duplicates?limit=300' + ($('dup-nodeb').checked ? '&no_debris=1' : '')).then(function (j) {
      box.innerHTML = '<h2>重複名稱（共 ' + j.total_names + ' 種名稱重複；點名稱列出全部 NORAD）<button style="float:right" onclick="document.getElementById(\'dup\').style.display=\'none\'">關閉</button></h2>' +
        '<div style="max-height:220px;overflow:auto">' + j.results.map(function (d) {
          return '<span class="dup tag" data-ids="' + d.norad_ids.slice(0, 500).join(',') + '">' + esc(d.name) + ' ×' + d.count + '</span>';
        }).join(' ') + '</div>';
      box.querySelectorAll('.dup').forEach(function (el) { el.onclick = function () { lookup(el.dataset.ids); }; });
    }).catch(function (e) { box.textContent = e.message; });
  }
  function loadNamed() {
    var ids = Object.keys(S.data().names).map(Number);
    fetchRows(ids).then(function (rows) { renderRows(rows); msg('q-msg', '已自訂名稱 ' + ids.length + ' 筆', true); });
  }

  // ── 建議名稱 ──
  var SG = [], SG_STATUS = { official: 'official', inferred: 'inferred', group_inferred: 'group_inferred', conflict: 'conflict', unresolved: 'unresolved' };
  var CONF_LABEL = { high: '高', medium: '中', low: '低' };
  var IND_LABEL = { confirmed: '已確認', inferred: '推論', disputed: '有爭議', unconfirmed: '未確認' };
  function sgChanged(r) {
    var c = S.data().names[r.norad_id];
    if (!c) return '';
    if (r.name_status === 'official' && c.status !== 'official') return '可升級：Space-Track 已正式命名';
    if (c.suggest_raw && r.raw_name && c.suggest_raw !== r.raw_name) return '來源名稱已變動：' + c.suggest_raw + ' → ' + r.raw_name;
    return '';
  }
  function renderSuggest() {
    var f = $('sg-filter').value, names = S.data().names;
    var rows = SG.filter(function (r) { return !f || (f === 'upgrade' ? sgChanged(r) : r.name_status === f); });
    var stc = { official: 'st-ok', inferred: 'st-stale', group_inferred: 'st-stale', conflict: 'st-decayed', unresolved: 'st-no_satcat' };
    document.querySelector('#sg tbody').innerHTML = rows.map(function (r) {
      var c = names[r.norad_id], ch = sgChanged(r), L = r.layers || {};
      var mates = (r.mates || []).map(function (m) { return m.intl + ' ' + (m.name || '') + (m.gcat_name ? '（GCAT：' + m.gcat_name + '）' : '') + (m.self ? ' ←' : ''); }).join('\n');
      var warn = (r.warnings || []).concat(ch ? [ch] : []);
      return '<tr data-n="' + r.norad_id + '"><td>' + (r.suggest ? '<input type="checkbox" class="sg-chk" value="' + r.norad_id + '"/>' : '') + '</td><td class="num">' + r.norad_id +
        '<div class="muted">' + esc(r.intl_code || '') + '</div></td><td>' + esc(r.current_name) + (c ? '<div class="cust">✎ ' + esc(c.name) + '</div>' : '') + '</td><td>' +
        (r.suggest ? '<input class="sg-name" style="width:240px" maxlength="64" value="' + esc(r.suggest) + '"/>' + (r.raw_name && r.raw_name !== r.suggest ? '<div class="muted">原始：' + esc(r.raw_name) + '</div>' : '') : '<span class="muted">（無可靠來源，維持原名）</span>') +
        '</td><td><span class="st ' + (stc[r.name_status] || '') + '">' + esc(r.name_status) + '</span>' + (r.confidence ? ' <span class="muted">可信度' + CONF_LABEL[r.confidence] + '</span>' : '') +
        '</td><td title="' + esc(mates) + '">' + esc((L.launch_group || {}).value || '—') + '<div class="muted">同次發射 ' + (r.mates || []).length + ' 個物體（游標停留看清單）</div></td><td>' +
        esc((L.candidate_mission || {}).value || '—') + '</td><td>' + esc(IND_LABEL[(L.individual || {}).status] || '—') + '</td><td style="white-space:normal;max-width:280px;color:#e3b341">' +
        esc(warn.join('；')) + '</td></tr>';
    }).join('') || '<tr><td colspan="9" class="muted">沒有符合的項目</td></tr>';
    var all = $('sg-all'); if (all) all.onchange = function () { document.querySelectorAll('.sg-chk').forEach(function (x) { x.checked = all.checked; }); };
  }
  function loadSuggest(ids) {
    msg('sg-msg', '分析中…', true);
    var url = ids ? '/api/satadmin/suggest?norad=' + ids.join(',') : '/api/satadmin/suggest?scope=objects&limit=2000';
    api('GET', url).then(function (j) {
      SG = j.results;
      if (!j.gcat) msg('sg-msg', '資料庫內尚無 GCAT 資料（每日管線更新後才有逐顆與發射任務名稱）', false);
      else {
        var c = j.counts;
        msg('sg-msg', '共 ' + j.count + ' 顆：official ' + c.official + '、inferred ' + c.inferred + '、group_inferred ' + c.group_inferred + '、conflict ' + c.conflict + '、unresolved ' + c.unresolved + '（GCAT ' + (j.gcat.sat_updated || '') + '）', true);
      }
      renderSuggest();
      if (ids) $('sg').scrollIntoView({ behavior: 'smooth', block: 'center' });
    }).catch(function (e) { msg('sg-msg', e.message); });
  }
  function sgCheck(mode) {
    document.querySelectorAll('.sg-chk').forEach(function (x) {
      var r = SG.find(function (y) { return y.norad_id === +x.value; });
      x.checked = mode === 'safe' && r && (r.name_status === 'official' || r.name_status === 'inferred') && !(r.warnings || []).length;
    });
  }
  function applySuggest() {
    var ev = ($('sg-evidence').value.match(/https?:\/\/\S+/g) || []), review = $('sg-review').value, n = 0, errs = [];
    document.querySelectorAll('.sg-chk:checked').forEach(function (x) {
      var r = SG.find(function (y) { return y.norad_id === +x.value; }); if (!r) return;
      var name = x.closest('tr').querySelector('.sg-name').value.trim();
      var evid = (r.sources || []).map(function (s) { return s.url; }).concat(ev).filter(function (u, i, a) { return u && a.indexOf(u) === i; });
      var note = r.note + (name !== r.suggest ? '；人工修改建議名稱' : '') + (ev.length ? '；證據：' + ev.join(' ') : '');
      try { S.rename(r.norad_id, name, note, r.current_name, { status: name !== r.suggest ? 'manual' : r.name_status, confidence: r.confidence, evidence: evid, review: review, suggest_raw: r.raw_name }); n++; }
      catch (e) { errs.push(r.norad_id + '：' + e.message); }
    });
    msg('sg-msg', '已套用 ' + n + ' 筆（存在這個瀏覽器）' + (errs.length ? '；失敗：' + errs.slice(0, 3).join('；') : ''), !errs.length);
    renderSuggest();
  }

  // ── 群組 ──
  function kids(pid) { return S.data().groups.filter(function (g) { return (g.parent || null) === pid; }).sort(function (a, b) { return a.name.localeCompare(b.name, 'zh-Hant'); }); }
  function renderGroups() { renderTree(); renderDetail(); }
  function renderTree() {
    var out = [];
    (function walk(pid, depth) {
      kids(pid).forEach(function (g) {
        var es = S.effSensor(g.id);
        out.push('<div class="g' + (g.id === SEL ? ' on' : '') + '" data-id="' + g.id + '" style="padding-left:' + (8 + depth * 18) + 'px">' +
          '<span class="dot" style="background:' + esc(g.color || 'transparent') + '"></span>' + esc(g.name) +
          (es ? ' <span class="muted">' + esc(SENSOR_LABEL[es] || es) + '</span>' : '') + '<span class="cnt">' + S.allMembers(g.id).length + '</span></div>');
        walk(g.id, depth + 1);
      });
    })(null, 0);
    $('gtree').innerHTML = out.join('') || '<div class="muted">尚無群組。在上方輸入名稱新增，例如「PRC偵察衛星」。</div>';
    $('gtree').querySelectorAll('.g').forEach(function (el) { el.onclick = function () { SEL = el.dataset.id; renderGroups(); }; });
  }
  function newGroup(sub) {
    try {
      if (sub && !SEL) throw new Error('請先在下方選取上層群組');
      SEL = S.createGroup($('g-new').value, sub ? SEL : null); $('g-new').value = ''; msg('g-new-msg', '已新增', true); renderGroups();
    } catch (e) { msg('g-new-msg', e.message); }
  }
  function renderDetail() {
    var box = $('gdetail'), g = SEL && S.group(SEL);
    if (!g) { box.innerHTML = '<div class="muted">選取左側群組以檢視與編輯。</div>'; return; }
    var bad = {}; S.descendants(g.id).forEach(function (id) { bad[id] = 1; });
    var parents = '<option value="">（最上層）</option>' + S.data().groups.filter(function (x) { return !bad[x.id]; })
      .map(function (x) { return [S.path(x.id), x.id]; }).sort(function (a, b) { return a[0].localeCompare(b[0], 'zh-Hant'); })
      .map(function (x) { return '<option value="' + x[1] + '"' + (x[1] === g.parent ? ' selected' : '') + '>' + esc(x[0]) + '</option>'; }).join('');
    var sensors = Object.keys(SENSOR_LABEL).map(function (k) { return '<option value="' + k + '"' + (k === (g.sensor || '') ? ' selected' : '') + '>' + SENSOR_LABEL[k] + '</option>'; }).join('');
    var el = S.effLook(g.id), es = S.effSensor(g.id), total = S.allMembers(g.id).length;
    box.innerHTML = '<h2>' + esc(S.path(g.id)) + ' <span class="muted">（本層 ' + g.members.length + ' 顆，含子群組 ' + total + ' 顆）</span></h2>' +
      '<div class="grid2"><label>名稱</label><input id="gd-name" maxlength="64"/><label>上層群組</label><select id="gd-parent">' + parents + '</select>' +
      '<label>顏色</label><div><input type="color" id="gd-color" value="' + esc(g.color || '#F44336') + '"/> <label class="muted"><input type="checkbox" id="gd-nocolor"' + (g.color ? '' : ' checked') + '/> 不指定</label></div>' +
      '<label>感測器類型</label><div><select id="gd-sensor">' + sensors + '</select> <span class="muted">目前生效：' + esc(SENSOR_LABEL[es] || '未設定') + '</span></div>' +
      '<label>偵照角度（°）</label><div><input id="gd-look" type="number" min="1" max="70" step="0.5" style="width:90px" placeholder="沿用上層"/> <span class="muted">離天底角，過頂預報用；目前生效：' + (el != null ? el + '°' : '未設定') + '</span></div>' +
      '<label>備註</label><input id="gd-note"/></div>' +
      '<div class="row" style="margin-top:10px"><button class="pri" id="gd-save">儲存群組設定</button><button class="dan" id="gd-del">刪除群組</button>' +
      '<label class="muted"><input type="checkbox" id="gd-cascade"/> 連同子群組刪除</label><span id="gd-msg" class="msg"></span></div>' +
      '<h2 style="margin-top:12px">成員</h2><textarea id="gd-add" placeholder="輸入要加入的 NORAD ID，可多個（逗號、空白或換行分隔）"></textarea>' +
      '<div class="row" style="margin-top:6px"><button class="pri" id="gd-addbtn">加入</button><button class="dan" id="gd-rm">移除勾選的衛星</button><span id="gm-msg" class="msg"></span></div>' +
      '<div class="tw"><table><thead><tr><th><input type="checkbox" id="mchk-all"/></th><th>NORAD</th><th>名稱</th><th>Space-Track 原名</th><th>國際編號</th><th>TLE 曆元（UTC）</th><th>國別</th><th>狀態</th></tr></thead><tbody id="gd-mem"><tr><td colspan="8" class="muted">載入中…</td></tr></tbody></table></div>';
    $('gd-name').value = g.name; $('gd-note').value = g.note || ''; $('gd-look').value = g.look_deg != null ? g.look_deg : '';
    $('gd-save').onclick = function () {
      var look = $('gd-look').value.trim();
      try {
        S.updateGroup(g.id, { name: $('gd-name').value, parent: $('gd-parent').value || null, color: $('gd-nocolor').checked ? '' : $('gd-color').value,
          sensor: $('gd-sensor').value, look_deg: look === '' ? null : parseFloat(look), note: $('gd-note').value.trim() });
        renderGroups(); msg('gd-msg', '已儲存', true);
      } catch (e) { msg('gd-msg', e.message); }
    };
    $('gd-del').onclick = function () {
      var cas = $('gd-cascade').checked;
      if (!confirm('刪除群組「' + S.path(g.id) + '」' + (cas ? '及其所有子群組' : '') + '？（衛星本身與更名不受影響）')) return;
      try { var p = g.parent || null; S.deleteGroup(g.id, cas); SEL = p; renderGroups(); } catch (e) { msg('gd-msg', e.message); }
    };
    $('gd-addbtn').onclick = function () {
      var ids = ($('gd-add').value.match(/\d+/g) || []).map(Number);
      if (!ids.length) { msg('gm-msg', '請輸入 NORAD ID'); return; }
      var n = S.addMembers(g.id, ids); renderGroups(); msg('gm-msg', '加入 ' + n + ' 顆', true);
    };
    $('gd-rm').onclick = function () {
      var ids = Array.prototype.map.call(document.querySelectorAll('.mchk:checked'), function (c) { return +c.value; });
      if (!ids.length) { msg('gm-msg', '請先勾選要移除的衛星'); return; }
      if (!confirm('從群組移除 ' + ids.length + ' 顆衛星？')) return;
      var n = S.removeMembers(g.id, ids); renderGroups(); msg('gm-msg', '已移除 ' + n + ' 顆', true);
    };
    $('mchk-all').onchange = function () { var a = $('mchk-all').checked; document.querySelectorAll('.mchk').forEach(function (c) { c.checked = a; }); };
    fetchRows(g.members.slice()).then(function (rows) {
      var miss = {}; rows.forEach(function (r) { miss[r.norad_id] = r; });
      $('gd-mem').innerHTML = g.members.map(function (n) {
        var r = miss[n] || { norad_id: n, name: '', in_catalog: false }, c = S.data().names[n];
        return '<tr><td><input type="checkbox" class="mchk" value="' + n + '"/></td><td class="num">' + n + '</td><td>' + (c ? '<span class="cust">✎ ' + esc(c.name) + '</span>' : esc(r.name || '—')) +
          '</td><td>' + esc((c && c.orig) || r.name || '—') + '</td><td>' + esc(r.intl_code || '—') + '</td><td class="num">' + esc(fmtT(r.tle_epoch)) + '</td><td>' + esc(r.country || '—') +
          '</td><td>' + stBadge(r) + (r.in_catalog ? '' : ' <span class="muted">不在目前目錄</span>') + '</td></tr>';
      }).join('') || '<tr><td colspan="8" class="muted">本層尚無衛星' + (total ? '（成員在子群組）' : '') + '</td></tr>';
    });
  }
  function addToGroup(n) {
    var gs = S.data().groups;
    if (!gs.length) { alert('尚無群組，請先到「群組分類」新增。'); return; }
    var list = gs.map(function (g) { return [S.path(g.id), g.id]; }).sort(function (a, b) { return a[0].localeCompare(b[0], 'zh-Hant'); });
    var td = $('r-' + n).lastElementChild;
    td.innerHTML = '<select id="ag-' + n + '">' + list.map(function (x) { return '<option value="' + x[1] + '">' + esc(x[0]) + '</option>'; }).join('') +
      '</select> <button class="pri" id="agb-' + n + '">加入</button> <button id="agc-' + n + '">取消</button>';
    $('agc-' + n).onclick = relookup;
    $('agb-' + n).onclick = function () { S.addMembers($('ag-' + n).value, [n]); relookup(); };
  }

  // ── 衛星失能 ──
  var DECAY_ROWS = [];
  function decayTab() {
    var sel = $('dc-group'), cur = sel.value;
    sel.innerHTML = '<option value="">全部群組</option>' + S.data().groups.map(function (g) { return [S.path(g.id), g.id]; })
      .sort(function (a, b) { return a[0].localeCompare(b[0], 'zh-Hant'); })
      .map(function (x) { return '<option value="' + x[1] + '">' + esc(x[0]) + '（' + S.allMembers(x[1]).length + '）</option>'; }).join('');
    sel.value = cur || SEL || '';
    loadDecay();
  }
  function loadDecay() {
    var gid = $('dc-group').value, gs = gid ? S.descendants(gid) : S.data().groups.map(function (g) { return g.id; });
    var mem = {};
    gs.forEach(function (id) { var g = S.group(id); if (g) g.members.forEach(function (n) { (mem[n] = mem[n] || []).push(S.path(id)); }); });
    var ids = Object.keys(mem).map(Number);
    if (!ids.length) { $('dc-sum').textContent = ''; document.querySelector('#dc tbody').innerHTML = '<tr><td colspan="10" class="muted">群組內沒有衛星</td></tr>'; return; }
    msg('dc-msg', '載入中…（第一次需下載 SATCAT，約數秒）', true);
    api('POST', '/api/satadmin/status', { norads: ids, stale_days: parseFloat($('dc-stale').value) || 30 }).then(function (j) {
      var all = $('dc-all').checked, ord = { decayed: 0, stale: 1, no_satcat: 2, ok: 3 };
      var rows = j.rows.filter(function (r) { return all || r.status !== 'ok'; }).map(function (r) { r.groups = mem[r.norad_id]; return r; });
      rows.sort(function (a, b) { return ord[a.status] - ord[b.status] || String(b.decay_date || '').localeCompare(String(a.decay_date || '')); });
      DECAY_ROWS = rows;
      var c = { decayed: 0, stale: 0, no_satcat: 0, ok: 0 }; j.rows.forEach(function (r) { c[r.status]++; });
      $('dc-sum').innerHTML = '已再入 <b style="color:#ff8f87">' + c.decayed + '</b> 顆・TLE 停更 <b style="color:#e3b341">' + c.stale + '</b> 顆・SATCAT 查無 ' + c.no_satcat + ' 顆' +
        (all ? '・正常 ' + c.ok + ' 顆' : '') + '　|　SATCAT：' + (j.satcat ? esc(j.satcat.source) + ' ' + esc(fmtT(j.satcat.fetched_at)) + '（' + j.satcat.count.toLocaleString() + ' 筆）' :
        '<span style="color:#d29922">' + esc(j.satcat_error || '尚未取得，DECAY 狀態無法判定') + '</span>');
      document.querySelector('#dc tbody').innerHTML = rows.map(function (r) {
        var c2 = S.data().names[r.norad_id];
        return '<tr><td class="num">' + r.norad_id + '</td><td>' + esc(dispName(r)) + (c2 ? '<div class="muted">' + esc(r.name) + '</div>' : '') + '</td><td>' + stBadge(r, true) +
          '</td><td class="num">' + esc(r.decay_date || '—') + '</td><td class="num">' + esc(r.launch_date || '—') + '</td><td class="num">' + (r.days_in_orbit != null ? r.days_in_orbit.toLocaleString() : '—') +
          '</td><td class="num">' + esc(fmtT(r.tle_epoch)) + '</td><td class="num">' + (r.tle_age_days != null ? r.tle_age_days : '—') + '</td><td>' + esc(r.object_type || '—') + '／' + esc(r.owner || '—') +
          '</td><td>' + r.groups.map(function (g) { return '<span class="tag">' + esc(g) + '</span>'; }).join('') + '</td></tr>';
      }).join('') || '<tr><td colspan="10" class="muted">群組內沒有已再入或停更的衛星</td></tr>';
      msg('dc-msg', '', true);
    }).catch(function (e) { msg('dc-msg', e.message); });
  }
  function decayCsv() {
    var q = function (s) { s = String(s == null ? '' : s); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    var out = '\ufeffnorad_id,name,status,decay_date,launch_date,days_in_orbit,tle_epoch,tle_age_days,intl_code,object_type,owner,groups\n' + DECAY_ROWS.map(function (r) {
      return [r.norad_id, q(dispName(r)), ST_LABEL[r.status], r.decay_date || '', r.launch_date || '', r.days_in_orbit == null ? '' : r.days_in_orbit, r.tle_epoch || '',
        r.tle_age_days == null ? '' : r.tle_age_days, q(r.intl_code || ''), q(r.object_type || ''), q(r.owner || ''), q((r.groups || []).join(' | '))].join(',');
    }).join('\n') + '\n';
    download('decay_' + stamp() + '.csv', out);
  }
  function loadRecent() {
    msg('dr-msg', '載入中…', true);
    api('GET', '/api/satadmin/recent_decay?days=' + encodeURIComponent($('dc-days').value || 30)).then(function (j) {
      if (!j.satcat_loaded) { msg('dr-msg', j.satcat_error || '尚未取得 SATCAT'); return; }
      document.querySelector('#dr tbody').innerHTML = j.rows.map(function (r) {
        return '<tr><td class="num">' + r.norad_id + '</td><td>' + esc(dispName(r)) + '</td><td>' + esc(r.intl_code || '—') + '</td><td class="num">' + esc(r.decay_date) + '</td><td class="num">' + esc(r.launch_date || '—') +
          '</td><td class="num">' + (r.days_in_orbit != null ? r.days_in_orbit.toLocaleString() : '—') + '</td><td>' + esc(r.object_type || '—') + '／' + esc(r.owner || '—') + '</td></tr>';
      }).join('') || '<tr><td colspan="7" class="muted">期間內沒有再入物體</td></tr>';
      msg('dr-msg', '共 ' + j.rows.length + ' 筆', true);
    }).catch(function (e) { msg('dr-msg', e.message); });
  }

  // ── 星歷 ──
  var EP_TIMER = null;
  function ago(iso) { if (!iso) return ''; var h = (Date.now() - new Date(iso)) / 3600000; return h < 1 ? Math.max(1, Math.round(h * 60)) + ' 分鐘前' : h < 48 ? h.toFixed(1) + ' 小時前' : (h / 24).toFixed(1) + ' 天前'; }
  function loadEphem() {
    api('GET', '/api/ephemeris/status').then(function (s) {
      var ix = s.index, gp = s.gp, sc = s.satcat;
      $('ep-status').innerHTML = '<div class="grid2" style="max-width:900px">' +
        '<label>最後更新</label><div><b style="color:#e6edf3">' + esc(fmtT(s.last_updated)) + ' UTC</b> <span class="muted">（' + ago(s.last_updated) + '）</span></div>' +
        '<label>資料庫</label><div>' + esc(s.db.name || '—') + '，更新於 ' + esc(fmtT(s.db.updated_at)) + '（' + ago(s.db.updated_at) + '）</div>' +
        '<label>最近重抓</label><div>' + (gp ? 'CelesTrak ' + esc(fmtT(gp.fetched_at)) + '：' + gp.count.toLocaleString() + ' 筆，更新 ' + gp.newer.toLocaleString() + ' 顆、新增 ' + gp.added + ' 顆' : '尚未重抓（本次服務啟動以來）') + '</div>' +
        '<label>目前目錄</label><div>' + ix.count.toLocaleString() + ' 顆；最新曆元 ' + esc(fmtT(ix.epoch_latest)) + '；曆元中位數 ' + ix.median_age_days + ' 天前；1 天內 ' + ix.within_1d.toLocaleString() + ' 顆、3 天內 ' + ix.within_3d.toLocaleString() + ' 顆、超過 30 天 ' + ix.older_30d.toLocaleString() + ' 顆</div>' +
        '<label>SATCAT</label><div>' + (sc ? esc(sc.source) + ' ' + esc(fmtT(sc.fetched_at)) + '：' + sc.count.toLocaleString() + ' 筆（已再入 ' + sc.decayed.toLocaleString() + '）' : '<span class="muted">需要時自動下載</span>') + '</div></div>';
      var jb = s.job || {};
      if (jb.started_at) $('ep-job').textContent = (jb.running ? '⏳ 進行中\n' : (jb.ok ? '✅ ' : '❌ ') + '最近一次（' + fmtT(jb.finished_at) + '）\n') + (jb.log || []).join('\n');
      clearTimeout(EP_TIMER); if (jb.running) EP_TIMER = setTimeout(loadEphem, 1500);
    }).catch(function (e) { $('ep-status').textContent = e.message; });
  }
  function refresh() {
    var tok = $('ep-token').value.trim(), h = tok ? { 'X-Admin-Token': tok } : {};
    msg('ep-msg', '已送出…', true);
    api('POST', '/api/ephemeris/refresh', {}, h).then(function () { msg('ep-msg', '', true); loadEphem(); }).catch(function (e) { msg('ep-msg', e.message); });
  }

  // ── 備份 ──
  function readFile(id) { var f = $(id).files[0]; if (!f) return Promise.reject(new Error('請先選擇檔案')); return f.text(); }
  function importCsv() {
    readFile('csvfile').then(function (t) {
      var r = S.importCsv(t, {});
      msg('csv-msg', '完成：更名 ' + r.renamed + ' 筆、加入群組 ' + r.grouped + ' 筆、新建群組 ' + r.created_groups + ' 個' + (r.errors.length ? '；錯誤 ' + r.errors.length + ' 筆：' + r.errors.slice(0, 3).join('；') : ''), !r.errors.length);
    }).catch(function (e) { msg('csv-msg', e.message); });
  }
  function exportCsv() { download('sat_custom_' + stamp() + '.csv', S.exportCsv()); }
  function exportJson() { download('satadmin_backup_' + stamp() + '.json', S.exportJson(), 'application/json'); }
  function importJson() {
    readFile('jsonfile').then(function (t) {
      if (!confirm('以備份取代這個瀏覽器目前的更名與群組？')) return;
      S.importJson(t); msg('bk-msg', '已還原：' + S.data().groups.length + ' 個群組、' + Object.keys(S.data().names).length + ' 筆更名', true);
    }).catch(function (e) { msg('bk-msg', e.message); });
  }
  function clearAll() { if (confirm('清除這個瀏覽器中所有更名與群組？（建議先下載備份）')) { S.clearAll(); SEL = null; msg('bk-msg', '已清除', true); } }

  $('q').addEventListener('keydown', function (e) { if (e.key === 'Enter') lookup(); });
  $('sg-filter').addEventListener('change', renderSuggest);
  window.SA = { loadSuggest: loadSuggest, sgCheck: sgCheck, applySuggest: applySuggest, lookup: lookup, rename: rename, unrename: unrename, loadDup: loadDup, loadNamed: loadNamed, newGroup: newGroup, addToGroup: addToGroup,
    loadDecay: loadDecay, decayCsv: decayCsv, loadRecent: loadRecent, refresh: refresh, importCsv: importCsv, exportCsv: exportCsv,
    exportJson: exportJson, importJson: importJson, clearAll: clearAll };
  var t = new URLSearchParams(location.search).get('tab');
  if (t) { var b = document.querySelector('#tabs button[data-t="' + t + '"]'); if (b) b.click(); }
})();
