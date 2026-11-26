/* 觀測資料（線上版）：存在使用者瀏覽器 localStorage，不上傳伺服器；交叉驗證時才把資料送到 /api/obs/check 計算（伺服器不保存）。
   介面與 Rust 單機版 web/ext/obs_server.js 相同（OBS_BACKEND），由 obs.js 共用。 */
(function () {
  'use strict';
  var KEY = 'atrdc_obs_v1', M = window.OBSM;
  function empty() { return { next_id: 1, pois: [], observations: [], orbits: [], claims: [] }; }
  var D = empty();
  try { var s = JSON.parse(localStorage.getItem(KEY) || 'null'); if (s && typeof s === 'object') D = Object.assign(empty(), s); } catch (e) { /* 損毀或無法存取：從空白開始 */ }
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(D)); }
    catch (e) { throw new Error('瀏覽器儲存空間不足或被封鎖：' + e.message); }
  }
  function now() { return new Date().toISOString().replace(/\.\d+Z$/, 'Z'); }
  function find(kind, id) { var i = D[kind].findIndex(function (r) { return r.id === id; }); if (i < 0) throw new Error('找不到紀錄 ' + id); return i; }
  function usedBy(poi) { var n = String(poi.name).toUpperCase(); return D.observations.filter(function (o) { return String(o.station).toUpperCase() === n; }).length; }
  function reviewOf(r) { var v = r.review || 'unreviewed'; return Object.prototype.hasOwnProperty.call(M.REVIEW, v) ? v : 'unreviewed'; }
  function snapshot() { return JSON.parse(JSON.stringify(D)); }

  window.OBS_BACKEND = {
    where: '這個瀏覽器（localStorage）',
    caps: function () { return Promise.resolve({ canEdit: true, canReview: true, user: '' }); },
    load: function () { return Promise.resolve(snapshot()); },
    add: function (kind, recs) {
      var added = 0, errors = [];
      recs.forEach(function (r, i) {
        var v = M.validate(kind, r, D, null);
        if (!v.rec) { errors.push({ row: r._row || i + 1, msg: v.errors.join('；') }); return; }
        var o = Object.assign({ id: 'o' + D.next_id++, by: '', at: now(), review: reviewOf(r), review_note: String(r.review_note || '').slice(0, 500), reviewer: '', reviewed_at: '' }, v.rec);
        D[kind].push(o); added++;
      });
      if (added) save();
      return Promise.resolve({ added: added, errors: errors });
    },
    update: function (kind, id, rec) {
      var i = find(kind, id), old = D[kind][i];
      var v = M.validate(kind, rec, D, id);
      if (!v.rec) return Promise.reject(new Error(v.errors.join('；')));
      if (kind === 'pois' && String(old.name).toUpperCase() !== v.rec.name.toUpperCase() && usedBy(old)) return Promise.reject(new Error('有 ' + usedBy(old) + ' 筆觀測紀錄使用此觀測站，不能改名'));
      var changed = Object.keys(v.rec).some(function (k) { return old[k] !== v.rec[k]; });
      D[kind][i] = Object.assign({}, old, v.rec, { at: now() });
      // 內容有變動 → 需重新複核
      if (changed && kind !== 'pois' && (old.review || 'unreviewed') !== 'unreviewed')
        Object.assign(D[kind][i], { review: 'unreviewed', review_note: '內容已修改，需重新複核', reviewer: '', reviewed_at: '' });
      save();
      return Promise.resolve(D[kind][i]);
    },
    remove: function (kind, id) {
      var i = find(kind, id);
      if (kind === 'pois' && usedBy(D.pois[i])) return Promise.reject(new Error('有 ' + usedBy(D.pois[i]) + ' 筆觀測紀錄使用此觀測站，請先刪除那些紀錄'));
      D[kind].splice(i, 1); save();
      return Promise.resolve(true);
    },
    review: function (kind, id, rv) {
      var i = find(kind, id);
      if (!Object.prototype.hasOwnProperty.call(M.REVIEW, rv.review)) return Promise.reject(new Error('複核狀態錯誤'));
      Object.assign(D[kind][i], { review: rv.review, review_note: String(rv.review_note || '').slice(0, 500), reviewer: '', reviewed_at: now() });
      save();
      return Promise.resolve(D[kind][i]);
    },
    check: function () {
      return fetch('/api/obs/check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(D) })
        .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; }); });
    },
    exportJson: function () { return JSON.stringify(D, null, 1); },
    importJson: function (text) {
      var j = JSON.parse(text);
      if (!j || typeof j !== 'object' || !Array.isArray(j.pois)) throw new Error('不是觀測資料備份檔');
      D = Object.assign(empty(), j); save();
    },
    clear: function () { D = empty(); save(); }
  };
})();
