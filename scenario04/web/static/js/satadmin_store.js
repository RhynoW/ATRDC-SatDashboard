/* 衛星更名與群組：只存在使用者自己的瀏覽器（localStorage），不上傳伺服器。
   衛星管理頁（satadmin.js）與過頂預報頁（forecast.js）共用。CSV 格式與 Rust 單機版相同：norad_id,name,group,note */
(function () {
  'use strict';
  var KEY = 'atrdc_satadmin_v1', SITES_KEY = 'atrdc_forecast_sites_v1';
  function now() { return new Date().toISOString().replace(/\.\d+Z$/, 'Z'); }
  function blank() { return { next_id: 0, names: {}, groups: [] }; }
  function load() {
    try { var d = JSON.parse(localStorage.getItem(KEY) || 'null'); if (d && d.groups && d.names) return d; } catch (e) {}
    return blank();
  }
  var D = load();
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(D)); return true; }
    catch (e) { alert('瀏覽器無法儲存（可能是無痕模式或儲存空間已滿）：' + e.message); return false; }
  }
  function group(id) { return D.groups.find(function (g) { return g.id === id; }); }
  function children(id) { return D.groups.filter(function (g) { return (g.parent || null) === id; }); }
  function descendants(id) {
    var out = [id];
    for (var i = 0; i < out.length && out.length < 10000; i++) children(out[i]).forEach(function (g) { out.push(g.id); });
    return out;
  }
  function ancestors(id) {
    var out = [], g = group(id);
    while (g && out.length < 64 && out.indexOf(g) < 0) { out.push(g); g = g.parent ? group(g.parent) : null; }
    return out;
  }
  function path(id) { return ancestors(id).map(function (g) { return g.name; }).reverse().join(' / '); }
  function effSensor(id) { var g = ancestors(id).find(function (x) { return x.sensor; }); return g ? g.sensor : ''; }
  function effLook(id) { var g = ancestors(id).find(function (x) { return x.look_deg != null; }); return g ? g.look_deg : null; }
  function allMembers(id) {
    var seen = {}, out = [];
    descendants(id).forEach(function (gid) { var g = group(gid); if (g) g.members.forEach(function (n) { if (!seen[n]) { seen[n] = 1; out.push(n); } }); });
    return out;
  }
  function groupsOf(n) { return D.groups.filter(function (g) { return g.members.indexOf(n) >= 0; }).map(function (g) { return path(g.id); }); }
  function checkName(s, what) {
    if (!s || s.length > 64) throw new Error(what + '需 1–64 字元');
    if (/[\u0000-\u001f]/.test(s)) throw new Error(what + '不可含控制字元');
  }
  function newId() { D.next_id = (D.next_id || 0) + 1; return 'g' + D.next_id; }

  var S = {
    data: function () { return D; },
    group: group, children: children, descendants: descendants, path: path, effSensor: effSensor, effLook: effLook,
    allMembers: allMembers, groupsOf: groupsOf,
    /* meta（選填）：{ status: official|inferred|group_inferred|conflict|manual, confidence: high|medium|low,
       evidence: [網址…], review: unreviewed|accepted|disputed|superseded, suggest_raw: 原始建議名稱 } */
    rename: function (n, name, note, orig, meta) {
      name = (name || '').trim(); checkName(name, '名稱');
      var prev = D.names[n];
      var rec = { name: name, orig: (prev && prev.orig) || orig || '', note: (note || '').trim().slice(0, 500), at: now() };
      meta = meta || {};
      rec.status = meta.status || 'manual';
      if (meta.confidence) rec.confidence = meta.confidence;
      if (meta.evidence && meta.evidence.length) rec.evidence = meta.evidence.slice(0, 10);
      rec.review = meta.review || 'unreviewed';
      if (meta.suggest_raw) rec.suggest_raw = meta.suggest_raw;
      D.names[n] = rec;
      save();
    },
    unrename: function (n) { delete D.names[n]; save(); },
    createGroup: function (name, parent) {
      name = (name || '').trim(); checkName(name, '群組名稱');
      parent = parent || null;
      if (parent && !group(parent)) throw new Error('找不到上層群組');
      if (D.groups.some(function (g) { return (g.parent || null) === parent && g.name === name; })) throw new Error('同一層已有群組「' + name + '」');
      var id = newId();
      D.groups.push({ id: id, name: name, parent: parent, color: '', sensor: '', look_deg: null, note: '', members: [], at: now() });
      save(); return id;
    },
    updateGroup: function (id, f) {
      var g = group(id); if (!g) throw new Error('找不到群組');
      if (f.name != null) checkName(f.name.trim(), '群組名稱');
      if (f.parent !== undefined) {
        var p = f.parent || null;
        if (p && descendants(id).indexOf(p) >= 0) throw new Error('不能移到自己或自己的下層群組底下');
      }
      if (f.look_deg != null && !(f.look_deg > 0 && f.look_deg <= 70)) throw new Error('偵照角度需介於 0–70 度');
      var name = f.name != null ? f.name.trim() : g.name, parent = f.parent !== undefined ? (f.parent || null) : (g.parent || null);
      if (D.groups.some(function (x) { return x !== g && (x.parent || null) === parent && x.name === name; })) throw new Error('同一層已有群組「' + name + '」');
      g.name = name; g.parent = parent;
      ['color', 'sensor', 'note'].forEach(function (k) { if (f[k] != null) g[k] = f[k]; });
      if (f.look_deg !== undefined) g.look_deg = f.look_deg;
      g.at = now(); save();
    },
    deleteGroup: function (id, cascade) {
      var ids = descendants(id);
      if (ids.length > 1 && !cascade) throw new Error('此群組底下還有 ' + (ids.length - 1) + ' 個子群組；請先刪除子群組，或勾選「連同子群組刪除」');
      D.groups = D.groups.filter(function (g) { return ids.indexOf(g.id) < 0; }); save();
    },
    addMembers: function (id, ids) {
      var g = group(id); if (!g) throw new Error('找不到群組');
      var n0 = g.members.length;
      ids.forEach(function (n) { if (n > 0 && g.members.indexOf(n) < 0) g.members.push(n); });
      g.at = now(); save(); return g.members.length - n0;
    },
    removeMembers: function (id, ids) {
      var g = group(id); if (!g) throw new Error('找不到群組');
      var n0 = g.members.length;
      g.members = g.members.filter(function (n) { return ids.indexOf(n) < 0; });
      g.at = now(); save(); return n0 - g.members.length;
    },
    ensurePath: function (p) {
      var parts = p.split(/[\/>／＞]/).map(function (s) { return s.trim(); }).filter(Boolean), parent = null;
      if (!parts.length) throw new Error('群組路徑空白');
      parts.forEach(function (name) {
        checkName(name, '群組名稱');
        var g = D.groups.find(function (x) { return (x.parent || null) === parent && x.name === name; });
        if (!g) { var id = newId(); D.groups.push({ id: id, name: name, parent: parent, color: '', sensor: '', look_deg: null, note: '', members: [], at: now() }); parent = id; }
        else parent = g.id;
      });
      return parent;
    },
    /* CSV 匯入：norad_id,name,group,note（有標題列依欄名；group 以「/」分層） */
    importCsv: function (text, origNames) {
      var rows = parseCsv(text.replace(/^﻿/, ''));
      if (!rows.length) throw new Error('CSV 是空的');
      var ci = 0, cn = 1, cg = 2, cnote = 3, header = isNaN(parseInt(rows[0][0], 10));
      if (header) {
        var h = rows[0].map(function (x) { return x.trim().toLowerCase(); });
        var find = function (keys) { return h.findIndex(function (x) { return keys.indexOf(x) >= 0; }); };
        ci = find(['norad_id', 'norad', 'norad id', 'catnr', 'norad 編號', '編號']); cn = find(['name', 'new_name', '名稱', '新名稱', '自訂名稱']);
        cg = find(['group', '群組', '分類']); cnote = find(['note', 'notes', '備註']);
        if (ci < 0) throw new Error('標題列找不到 norad_id 欄');
        rows.shift();
      }
      var r = { renamed: 0, grouped: 0, created_groups: 0, errors: [] }, before = D.groups.length;
      rows.forEach(function (row, i) {
        var line = i + (header ? 2 : 1), g = function (c) { return c >= 0 && row[c] != null ? row[c].trim() : ''; };
        var n = parseInt(g(ci), 10);
        if (!(n > 0)) { if (row.some(function (c) { return c.trim(); })) r.errors.push('第 ' + line + ' 行：NORAD 編號無效'); return; }
        try {
          var name = g(cn), note = g(cnote);
          if (name) { checkName(name, '名稱'); var prev = D.names[n]; D.names[n] = { name: name, orig: (prev && prev.orig) || (origNames && origNames[n]) || '', note: note, at: now() }; r.renamed++; }
          if (g(cg)) { var gid = S.ensurePath(g(cg)), grp = group(gid); if (grp.members.indexOf(n) < 0) { grp.members.push(n); r.grouped++; } }
        } catch (e) { r.errors.push('第 ' + line + ' 行：' + e.message); }
      });
      r.created_groups = D.groups.length - before;
      save(); return r;
    },
    exportCsv: function () {
      var rows = [], grouped = {};
      D.groups.forEach(function (g) { g.members.forEach(function (n) { rows.push([n, path(g.id)]); grouped[n] = 1; }); });
      Object.keys(D.names).forEach(function (n) { if (!grouped[n]) rows.push([+n, '']); });
      rows.sort(function (a, b) { return a[0] - b[0] || a[1].localeCompare(b[1]); });
      var q = function (s) { s = String(s == null ? '' : s); return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
      return '﻿norad_id,name,orig_name,group,note\n' + rows.map(function (r) {
        var rec = D.names[r[0]] || {};
        return [r[0], q(rec.name || ''), q(rec.orig || ''), q(r[1]), q(rec.note || '')].join(',');
      }).join('\n') + '\n';
    },
    exportJson: function () { return JSON.stringify(D, null, 1); },
    importJson: function (text) {
      var d = JSON.parse(text);
      if (!d || !Array.isArray(d.groups) || typeof d.names !== 'object') throw new Error('不是有效的衛星管理備份檔');
      D = { next_id: d.next_id || 0, names: d.names, groups: d.groups }; save();
    },
    clearAll: function () { D = blank(); save(); },
    loadSites: function () { try { var s = JSON.parse(localStorage.getItem(SITES_KEY) || 'null'); return Array.isArray(s) ? s : null; } catch (e) { return null; } },
    saveSites: function (s) { try { localStorage.setItem(SITES_KEY, JSON.stringify(s)); return true; } catch (e) { return false; } },
  };

  function parseCsv(text) {
    var rows = [], row = [], f = '', q = false;
    for (var i = 0; i < text.length; i++) {
      var c = text[i];
      if (q) { if (c === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += c; }
      else if (c === '"') q = true;
      else if (c === ',') { row.push(f); f = ''; }
      else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; row.push(f); rows.push(row); row = []; f = ''; }
      else f += c;
    }
    if (f || row.length) { row.push(f); rows.push(row); }
    return rows.filter(function (r) { return r.some(function (x) { return x.trim(); }); });
  }
  S.parseCsv = parseCsv;
  window.SAStore = S;
})();
