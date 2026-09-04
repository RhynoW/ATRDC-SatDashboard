/* rpo3d.js — 泛用型：任一「近距離配對」之相對接近 3D 場景（Cesium）+ Chan Pc（proxy）。
 * 配對清單來自 /api/conjunctions；場景資料來自 /api/rpo/<primary>/<secondary>。
 * 由 rpo3d.html 於 Cesium.js 載入後呼叫 window.startRPO()。
 */
(function () {
  "use strict";

  var C = null, viewer = null;
  var PRIM = "#F2A73B", SEC = "#35C6F4", CRIT = "#FF5C4E";
  var _tick = null;            // 目前 clock.onTick handler（切換時移除）
  var _loadSeq = 0;            // 防止快速切換之過期回應覆寫
  var _curP = null, _curS = null;
  var _eP = null, _eS = null;  // 兩顆衛星 entity（視角追蹤用）
  var _meta = null, _vp = "p"; // 目前視角：'p'=primary / 's'=secondary
  var _pcSpheres = [], _pcSphereOn = true;   // 碰撞機率球 entities（兩顆衛星各一）+ 開關
  // COMSPOC 模式：全期地固座標系（ECEF）長軌跡（GEO 定點保持迴圈／東西換位）+ 外側定鏡頭 + Ranges 讀數
  var _comspocOn = false, _trailEnts = [], _trailData = null, _rangeLabel = null;

  function warn(m) {
    var w = document.getElementById("warn");
    if (w) { w.textContent = m; w.style.display = "block"; }
    var l = document.getElementById("loading");
    if (l) l.textContent = m;
  }
  function stat(m, err) {
    var s = document.getElementById("pstat");
    if (s) { s.textContent = m || ""; s.className = "stat" + (err ? " err" : ""); }
  }
  function fmtRange(km) {
    if (km == null || isNaN(km)) return "—";
    return km < 10 ? km.toFixed(2) : (km < 1000 ? km.toFixed(1) : Math.round(km).toLocaleString());
  }
  function fmtPc(pc) { return (!pc || pc <= 0) ? "&lt;1e-8" : pc.toExponential(1); }
  // Pc → 風險色（沿用 scenario04 conjunction 門檻：RED>1e-4, AMBER>1e-6, 否則 GREEN）
  function riskColor(pc) {
    if (pc > 1e-4) return C.Color.fromCssColorString(CRIT);
    if (pc > 1e-6) return C.Color.fromCssColorString("#F2A73B");
    return C.Color.fromCssColorString("#4FD08A");
  }

  function initCesium() {
    C = window.Cesium;
    C.Ion.defaultAccessToken = "";
    try { C.IonResource.fromAssetId = function () { return Promise.reject(new Error("Ion disabled")); }; } catch (e) {}
    viewer = new C.Viewer("cesiumContainer", {
      animation: true, timeline: true, baseLayerPicker: false,
      geocoder: false, homeButton: false, navigationHelpButton: false,
      sceneModePicker: false, fullscreenButton: false, infoBox: false, selectionIndicator: false,
      imageryProvider: new C.SingleTileImageryProvider({
        url: "/api/globe_texture/default",
        rectangle: C.Rectangle.fromDegrees(-180, -90, 180, 90),
        credit: "NASA Blue Marble",
      }),
      terrainProvider: new C.EllipsoidTerrainProvider(),
    });
    viewer.cesiumWidget.creditContainer.style.display = "none";
    viewer.scene.globe.enableLighting = true;
    viewer.scene.globe.depthTestAgainstTerrain = false;
  }

  function sampledPos(orbit, key) {
    var p = new C.SampledPositionProperty();
    for (var i = 0; i < orbit.length; i++) {
      var a = orbit[i][key];
      p.addSample(C.JulianDate.fromIso8601(orbit[i].t), C.Cartesian3.fromDegrees(a[0], a[1], a[2]));
    }
    try { p.setInterpolationOptions({ interpolationDegree: 5, interpolationAlgorithm: C.LagrangePolynomialApproximation }); } catch (e) {}
    return p;
  }

  function addSat(name, pos, hex, t0, t1, trailS) {
    return viewer.entities.add({
      name: name,
      availability: new C.TimeIntervalCollection([new C.TimeInterval({ start: t0, stop: t1 })]),
      position: pos,
      point: { pixelSize: 11, color: C.Color.fromCssColorString(hex), outlineColor: C.Color.BLACK, outlineWidth: 1 },
      label: { text: name, font: "12px monospace", fillColor: C.Color.fromCssColorString(hex),
        style: C.LabelStyle.FILL, pixelOffset: new C.Cartesian2(0, -18),
        showBackground: true, backgroundColor: C.Color.fromCssColorString("#0A0D13").withAlpha(0.6) },
      // 位置為地固座標系（ECEF）取樣：GEO 物體長拖尾即呈現定點保持迴圈（COMSPOC 式）
      path: { leadTime: 0, trailTime: trailS || 5400, width: 2, resolution: 60,
        material: C.Color.fromCssColorString(hex).withAlpha(0.85) },
    });
  }

  function buildHUD(meta) {
    document.getElementById("title").textContent = meta.title;
    document.getElementById("subtitle").innerHTML = meta.subtitle;
    document.getElementById("lg_p").textContent = meta.primId + " · " + meta.primName;
    document.getElementById("lg_s").textContent = meta.secId + " · " + meta.secName;
    document.getElementById("k_pname").textContent = meta.primName + " 高度";
    document.getElementById("k_sname").textContent = meta.secName + " 高度";
    var tl = meta.timeline || [];
    document.getElementById("timeline").innerHTML = tl.map(function (r) {
      return '<div class="tlrow"><div class="d">' + r.d + '</div><div class="t">' + r.t + "</div></div>";
    }).join("");
    document.getElementById("note").innerHTML =
      "<b>方法</b>：兩物體以最近 elset SGP4 傳播至共同時鐘；Pc = Chan (2008) 各向同性首階近似"
      + "（σr=" + meta.sigma_r + "、σt=" + meta.sigma_t + " km），為<b>排序代理（proxy）</b>、非作業級碰撞機率。<br>"
      + "TLE/SGP4 位置含約 1–5 km 不確定度，次公里距離落於雜訊底——幾何重建、非精密交會判定。";
  }

  function clearScene() {
    if (_tick) { try { viewer.clock.onTick.removeEventListener(_tick); } catch (e) {} _tick = null; }
    if (viewer) { try { viewer.trackedEntity = undefined; } catch (e) {} viewer.entities.removeAll(); }
    _eP = _eS = null; _pcSpheres = []; _trailEnts = []; _trailData = null; _rangeLabel = null;
    destroyRelWindow();
  }

  // ── COMSPOC 模式 ──
  // 位置以 fromDegrees 建立即為地固座標系：GEO 物體之傾角／偏心在此系呈每日迴圈、漂移呈螺旋，
  // 長時間累積即 COMSPOC「on-orbit behavior」影片中的兩色迴圈。軌跡隨時鐘漸進繪出（≤ 當前時刻）。
  function buildTrails(trail, meta) {
    if (!trail || !trail.t || !trail.t.length) return;
    var ms = trail.t.map(function (t) { return Date.parse(t); });
    var P = trail.p.map(function (a) { return C.Cartesian3.fromDegrees(a[0], a[1], a[2]); });
    var S = trail.s.map(function (a) { return C.Cartesian3.fromDegrees(a[0], a[1], a[2]); });
    _trailData = { ms: ms, P: P, S: S, d: trail.d };
    function upto(arr) {
      return new C.CallbackProperty(function (time) {
        var now = C.JulianDate.toDate(time).getTime();
        var n = ms.length;                      // 二分搜尋：≤ now 之樣本數
        var lo = 0, hi = n;
        while (lo < hi) { var mid = (lo + hi) >> 1; if (ms[mid] <= now) lo = mid + 1; else hi = mid; }
        return arr.slice(0, Math.max(2, lo));
      }, false);
    }
    _trailEnts = [
      viewer.entities.add({ show: _comspocOn, polyline: { positions: upto(P), width: 1.6,
        material: C.Color.fromCssColorString(PRIM).withAlpha(0.85), arcType: C.ArcType.NONE } }),
      viewer.entities.add({ show: _comspocOn, polyline: { positions: upto(S), width: 1.6,
        material: C.Color.fromCssColorString(SEC).withAlpha(0.85), arcType: C.ArcType.NONE } }),
    ];
    var info = document.getElementById("comspocInfo");
    if (info) info.textContent = "全期 " + trail.t[0].slice(0, 10) + " ~ " + trail.t[trail.t.length - 1].slice(0, 10)
      + " · " + ms.length + " 點 @ " + trail.step_min + " min · 地固座標系（ECEF）";
  }

  // Ranges 讀數：仿 COMSPOC「Ranges ⊗  A (SSN) … B (SSN) = xx km」，掛在連線中點
  function buildRangeLabel(pPos, sPos, meta, sampleAt) {
    _rangeLabel = viewer.entities.add({
      show: _comspocOn,
      position: new C.CallbackProperty(function (time) {
        var a = pPos.getValue(time), b = sPos.getValue(time);
        return (a && b) ? C.Cartesian3.midpoint(a, b, new C.Cartesian3()) : undefined;
      }, false),
      label: {
        text: new C.CallbackProperty(function (time) {
          var s = sampleAt(C.JulianDate.toDate(time).getTime());
          return "Ranges ⊗  " + meta.primName + " (SSN " + meta.primId + ") … "
               + meta.secName + " (SSN " + meta.secId + ") = " + fmtRange(s.d) + " km";
        }, false),
        font: "12px monospace", fillColor: C.Color.WHITE, style: C.LabelStyle.FILL,
        pixelOffset: new C.Cartesian2(0, -26), showBackground: true,
        backgroundColor: C.Color.fromCssColorString("#0A0D13").withAlpha(0.7),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
    });
  }

  // COMSPOC 鏡頭（兩段）：
  //  wide  ─ 經度偏西 12°、距地心 60,000 km，視線為地心／物體方向平分線：地球在左、兩物體在右（仿 COMSPOC 構圖）。
  //  close ─ 於兩物體中點沿軌（−T）1,400 km 外、以軌道面法向 N 為上方望向 +T：
  //          地固座標系日迴圈（振幅≈傾角×42,164 km，TJS-10/3 僅 ~250 km）在此距離清楚可見。
  var _camMode = "close";
  function rtnBasis(time) {
    var a = _eP.position.getValue(time), b = _eS.position.getValue(time);
    if (!a || !b) return null;
    var mid = C.Cartesian3.midpoint(a, b, new C.Cartesian3());
    var a2 = _eP.position.getValue(C.JulianDate.addSeconds(time, 60, new C.JulianDate())) || a;
    var v = C.Cartesian3.subtract(a2, a, new C.Cartesian3());
    var R = C.Cartesian3.normalize(mid, new C.Cartesian3());
    var N = C.Cartesian3.cross(mid, v, new C.Cartesian3());
    if (C.Cartesian3.magnitude(N) < 1e-6) N = C.Cartesian3.clone(C.Cartesian3.UNIT_Z);
    N = C.Cartesian3.normalize(N, N);
    var T = C.Cartesian3.normalize(C.Cartesian3.cross(N, R, new C.Cartesian3()), new C.Cartesian3());
    return { mid: mid, R: R, T: T, N: N };
  }
  function comspocCamera(mode) {
    if (!_eP || !_eS || !viewer) return;
    if (mode) _camMode = mode;
    try { viewer.trackedEntity = undefined; } catch (e) {}
    var time = viewer.clock.currentTime;
    var basis = rtnBasis(time);
    if (!basis) return;
    var dest, dir, up;
    if (_camMode === "wide") {
      // 眼點：與兩物體同經度偏西 12°、距地心 60,000 km（GEO）；視線取「地心方向」與「物體方向」
      // 之平分線 → 地球（角徑約 12°）在左、兩物體在右，各離軸約 ±12°，仿 COMSPOC 構圖。
      var c = C.Cartographic.fromCartesian(basis.mid);
      var lon = C.Math.toDegrees(c.longitude), rMid = C.Cartesian3.magnitude(basis.mid);
      var distC = Math.max(2.4e7, rMid * 1.42);   // LEO 遠景下限 24,000 km（地球約佔 31° 視角）
      dest = C.Cartesian3.fromDegrees(lon - 12, 6, distC - 6378137);
      var toC = C.Cartesian3.normalize(C.Cartesian3.negate(dest, new C.Cartesian3()), new C.Cartesian3());
      var toS = C.Cartesian3.normalize(C.Cartesian3.subtract(basis.mid, dest, new C.Cartesian3()), new C.Cartesian3());
      dir = C.Cartesian3.normalize(C.Cartesian3.add(toC, toS, new C.Cartesian3()), new C.Cartesian3());
      up = C.Cartesian3.UNIT_Z;
    } else {
      var back = C.Cartesian3.multiplyByScalar(basis.T, -1.4e6, new C.Cartesian3());
      var out = C.Cartesian3.multiplyByScalar(basis.R, 2.5e5, new C.Cartesian3());
      dest = C.Cartesian3.add(C.Cartesian3.add(basis.mid, back, new C.Cartesian3()), out, new C.Cartesian3());
      dir = C.Cartesian3.normalize(C.Cartesian3.subtract(basis.mid, dest, new C.Cartesian3()), new C.Cartesian3());
      up = basis.N;
    }
    viewer.camera.flyTo({ destination: dest, orientation: { direction: dir, up: up }, duration: 1.0 });
    var b = document.getElementById("camToggle");
    if (b) b.textContent = "📷 鏡頭：" + (_camMode === "wide" ? "全景（地球＋軌跡）" : "近景（迴圈）") + " ⇄";
  }
  window.__rpoCam = comspocCamera;   // 測試／除錯用

  function setComspoc(on) {
    _comspocOn = on;
    _trailEnts.forEach(function (e) { e.show = on; });
    if (_rangeLabel) _rangeLabel.show = on;
    // 模式下碰撞球與短拖尾會遮住迴圈 → 隱藏；關閉時還原
    _pcSpheres.forEach(function (e) { e.show = on ? false : _pcSphereOn; });
    [_eP, _eS].forEach(function (e) { if (e && e.path) e.path.show = !on; });
    var cb = document.getElementById("camToggle");
    if (cb) cb.style.display = on ? "" : "none";
    if (on) comspocCamera(); else setViewpoint(_vp);
  }

  // 切換至指定衛星視角（相機跟隨該顆），並更新按鈕/標籤
  // 視角三態：t=第三方遠景（預設，綜觀整個事件）→ p=primary → s=secondary → t …
  function setViewpoint(which) {
    if (!_eP || !_eS || !_meta) return;
    _vp = which;
    var names = { t: "第三方遠景", p: _meta.primName, s: _meta.secName };
    var next  = { t: "p", p: "s", s: "t" }[which];
    if (which === "t") {
      try { viewer.trackedEntity = undefined; } catch (e) {}
      comspocCamera("wide");                 // 遠景構圖：地球在側、兩物體與軌道全入鏡
    } else {
      try { viewer.trackedEntity = (which === "p") ? _eP : _eS; } catch (e) {}
    }
    var lab = document.getElementById("vpLabel");
    if (lab) lab.textContent = "目前視角：" + names[which];
    var btn = document.getElementById("vpToggle");
    if (btn) btn.textContent = "🎥 切換到 " + names[next] + (next === "t" ? "" : " 視角");
  }

  // Cesium 時間軸醒目色帶：meta.phases = [{from,to,label,color}]（部署/訪G/返回/回收）
  function paintPhases(phases) {
    if (!viewer.timeline || !phases || !phases.length) return;
    try { viewer.timeline._highlightRanges = []; } catch (e) {}   // 換場景時清舊色帶
    phases.forEach(function (ph, i) {
      try {
        var r = viewer.timeline.addHighlightRange(ph.color || "#F2A73B", 4, 1 + i * 5);
        r.setRange(C.JulianDate.fromIso8601(ph.from), C.JulianDate.fromIso8601(ph.to));
      } catch (e) {}
    });
    try { viewer.timeline.resize(); } catch (e) {}
  }

  // ── 浮動視窗：相對軌跡投影（LVLH T–R 平面，與 Cesium 時鐘同步） ──
  // 衛星繞地球「一直繞圈圈」時，慣性/地圖視角看不出軌道升降；在對方 LVLH 座標中，
  // 徑向 R（縱軸，正=軌道較高）與沿軌 T（橫軸）的投影軌跡才能直接呈現接近/抬升/降軌。
  // 資料：orbit[].rtn = secondary−primary 於 primary LVLH；取負向即 primary 相對 secondary
  //（以 secondary 為中心；兩 LVLH 軸向於 km 級間距下近乎平行，投影用途足夠）。
  var _relwin = null, _relOn = true, _relCenterSec = true;   // 預設以 secondary（如 67689 太空飛機）為中心
  function destroyRelWindow() {
    if (_relwin && _relwin.el) { try { _relwin.el.remove(); } catch (e) {} }
    _relwin = null;
  }
  function buildRelWindow(orbit, meta, stepS0) {
    destroyRelWindow();
    if (!orbit.length || !orbit[0].rtn) return;
    var el = document.createElement("div");
    el.id = "relwin";
    el.innerHTML = '<div class="hd">\ud83e\udded <span id="relTitle"></span>'
      + '<span class="x" id="relSwap" title="切換中心物體">\u21c4 中心</span>'
      + '<span class="x" id="relClose" title="關閉">\u2715</span></div>'
      + '<canvas id="relCanvas"></canvas>'
      + '<div class="ft" id="relFoot"></div>';
    document.body.appendChild(el);
    el.style.display = _relOn ? "" : "none";
    var hd = el.querySelector(".hd"), drag = null;
    hd.addEventListener("mousedown", function (e) {
      if (e.target.classList.contains("x")) return;
      drag = { x: e.clientX, y: e.clientY, l: el.offsetLeft, t: el.offsetTop };
      e.preventDefault();
    });
    window.addEventListener("mousemove", function (e) {
      if (!drag) return;
      el.style.left = (drag.l + e.clientX - drag.x) + "px";
      el.style.top = (drag.t + e.clientY - drag.y) + "px";
      el.style.right = "auto";
    });
    window.addEventListener("mouseup", function () { drag = null; });
    el.querySelector("#relClose").addEventListener("click", function () {
      _relOn = false; el.style.display = "none";
      var c = document.getElementById("relwinChk"); if (c) c.checked = false;
    });
    el.querySelector("#relSwap").addEventListener("click", function () {
      _relCenterSec = !_relCenterSec;
    });
    var stepS = stepS0 || 600;   // 由 summary.fine_step_s 覆寫（見下）
    var pts = orbit.map(function (o) {
      // 縱軸用「Δ高度」（測地高度差）而非直線 LVLH 的 R：大沿軌間距時 R 被軌道
      // 曲率項（~T²/2r，1,000 km 時約 71 km）淹沒，Δ高度才如實呈現軌道升降。
      return { ms: Date.parse(o.t), T: o.rtn[1], dh: (o.p[2] - o.s[2]) / 1000.0 };
    });
    // 單圈（~96 min）移動平均：把相對偏心造成的每圈擺動平掉，露出升降趨勢
    var per = Math.max(3, Math.round(96 * 60 / stepS)), half = per >> 1;
    var sm = pts.map(function (p, i) {
      var a = Math.max(0, i - half), b = Math.min(pts.length, i + half + 1), T = 0, dh = 0;
      for (var j = a; j < b; j++) { T += pts[j].T; dh += pts[j].dh; }
      return { ms: p.ms, T: T / (b - a), dh: dh / (b - a) };
    });
    _relwin = { el: el, cv: el.querySelector("#relCanvas"), pts: pts, sm: sm, meta: meta };
  }

  function drawRelWindow(nowMs) {
    if (!_relwin || !_relOn) return;
    var w = _relwin, cv = w.cv, meta = w.meta, dpr = window.devicePixelRatio || 1;
    var CW = cv.clientWidth || 400, CH = 300;
    if (cv.width !== CW * dpr || cv.height !== CH * dpr) { cv.width = CW * dpr; cv.height = CH * dpr; }
    var g = cv.getContext("2d");
    var sgn = _relCenterSec ? -1 : 1;   // 沿軌符號
    var hsgn = _relCenterSec ? 1 : -1;  // Δ高度：dh = primary−secondary，中心=secondary 時同號   // 中心=secondary → 畫 primary 相對位置（取負）
    var cName = _relCenterSec ? meta.secName : meta.primName;
    var mName = _relCenterSec ? meta.primName : meta.secName;
    document.getElementById("relTitle").textContent = "相對軌跡投影 — 中心：" + cName;
    var Ts = w.pts.map(function (p) { return sgn * p.T; });
    var Rs = w.pts.map(function (p) { return hsgn * p.dh; });
    var tmin = Math.min.apply(null, Ts), tmax = Math.max.apply(null, Ts);
    var rmin = Math.min.apply(null, Rs), rmax = Math.max.apply(null, Rs);
    function pad(lo, hi, minSpan) {
      var c = (lo + hi) / 2, sp = Math.max(hi - lo, minSpan) * 1.16 / 2;
      return [c - sp, c + sp];
    }
    var tr = pad(tmin, tmax, 10), rr = pad(rmin, rmax, 4);
    var padL = 46, padR = 12, padT = 10, padB = 30;
    g.save(); g.scale(dpr, dpr);
    g.clearRect(0, 0, CW, CH);
    function X(t) { return padL + (t - tr[0]) / (tr[1] - tr[0]) * (CW - padL - padR); }
    function Y(r) { return CH - padB - (r - rr[0]) / (rr[1] - rr[0]) * (CH - padT - padB); }
    g.strokeStyle = "rgba(255,255,255,.07)"; g.lineWidth = 1; g.beginPath();
    [0.25, 0.5, 0.75].forEach(function (f) {
      var x = padL + f * (CW - padL - padR), y = padT + f * (CH - padT - padB);
      g.moveTo(x, padT); g.lineTo(x, CH - padB); g.moveTo(padL, y); g.lineTo(CW - padR, y);
    }); g.stroke();
    g.strokeStyle = "rgba(255,255,255,.25)"; g.beginPath();
    g.moveTo(X(0), padT); g.lineTo(X(0), CH - padB);
    g.moveTo(padL, Y(0)); g.lineTo(CW - padR, Y(0)); g.stroke();
    g.fillStyle = _relCenterSec ? SEC : PRIM;
    g.beginPath(); g.arc(X(0), Y(0), 4.5, 0, 6.283); g.fill();
    g.fillStyle = "#dfe6f0"; g.font = "10px monospace";
    g.fillText(cName, Math.min(X(0) + 7, CW - 96), Y(0) - 6);
    g.fillStyle = "#8b96a8";
    g.fillText("沿軌 T (km) \u2192", CW - 104, CH - 8);
    g.save(); g.translate(11, 190); g.rotate(-Math.PI / 2);
    g.fillText("\u0394高度 (km) \u2191=軌道較高", 0, 0); g.restore();
    g.fillText(tr[0].toFixed(0), padL, CH - 18);
    g.fillText(tr[1].toFixed(0), CW - padR - 34, CH - 18);
    g.fillText(rr[1].toFixed(1), 22, padT + 10);
    g.fillText(rr[0].toFixed(1), 22, CH - padB - 2);
    var n = w.pts.length, k = 0;
    while (k < n && w.pts[k].ms <= nowMs) k++;
    function path(arr, from, to, style, width) {
      g.strokeStyle = style; g.lineWidth = width; g.beginPath();
      for (var i = from; i < to; i++) {
        var x = X(sgn * arr[i].T), y = Y(hsgn * arr[i].dh);
        if (i === from) g.moveTo(x, y); else g.lineTo(x, y);
      }
      g.stroke();
    }
    var bright = _relCenterSec ? PRIM : SEC;
    if (n > 1) path(w.pts, 0, n, "rgba(139,150,168,.22)", 1);          // 全程逐點（淡細）
    if (k > 1) path(w.pts, 0, k, "rgba(242,167,59,.45)", 1);           // 已播放逐點（半亮細）
    if (k > 1) path(w.sm, 0, k, bright, 2.4);                           // 已播放單圈平均（亮粗＝趨勢）
    if (k > 0) {
      var cur = w.pts[Math.min(k - 1, n - 1)];
      var cx = X(sgn * cur.T), cy = Y(hsgn * cur.dh);
      g.fillStyle = CRIT; g.beginPath(); g.arc(cx, cy, 5, 0, 6.283); g.fill();
      g.strokeStyle = "rgba(255,92,78,.5)"; g.lineWidth = 1;
      g.beginPath(); g.moveTo(X(0), Y(0)); g.lineTo(cx, cy); g.stroke();
      var ft = document.getElementById("relFoot");
      if (ft) ft.textContent = mName + "\uff1a沿軌 T " + (sgn * cur.T).toFixed(1) + " km\u3001\u0394高度 "
        + (hsgn * cur.dh).toFixed(2) + " km\uff08>0\uff1d軌道較高\uff09"
        + " \u00b7 粗線\uff1d單圈平均趨勢\u3001細線\uff1d逐點 \u00b7 拖曳標題移動\u3001\u21c4 換中心";
    }
    g.restore();
  }

  // ── 相對運動圖：距離–時間、RTN 分量–時間（純 canvas），游標隨 Cesium 時鐘同步 ──
  var _charts = null;
  function drawLine(ctx, W, H, xs, ys, ymin, ymax, color, pad) {
    ctx.strokeStyle = color; ctx.lineWidth = 1.4; ctx.beginPath();
    for (var i = 0; i < xs.length; i++) {
      var x = pad.l + (W - pad.l - pad.r) * xs[i], y = pad.t + (H - pad.t - pad.b) * (1 - (ys[i] - ymin) / (ymax - ymin || 1));
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    }
    ctx.stroke();
  }
  function setupCharts(orbit, meta) {
    var cd = document.getElementById("ch_dist"), cr = document.getElementById("ch_rtn");
    if (!cd || !cr || !orbit.length) return;
    var hasRtn = !!orbit[0].rtn;
    var t0 = Date.parse(orbit[0].t), t1 = Date.parse(orbit[orbit.length - 1].t) || t0 + 1;
    var xs = orbit.map(function (o) { return (Date.parse(o.t) - t0) / (t1 - t0); });
    var pad = { l: 44, r: 6, t: 4, b: 14 };
    function frame(cv) {
      var dpr = window.devicePixelRatio || 1, W = cv.clientWidth || 300, H = +cv.getAttribute("height");
      cv.width = W * dpr; cv.height = H * dpr; var ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, W, H); ctx.font = "9px monospace"; ctx.fillStyle = "#6e7681"; ctx.strokeStyle = "#21262d";
      return { ctx: ctx, W: W, H: H };
    }
    function axes(f, ymin, ymax) {
      var c = f.ctx; c.beginPath(); c.moveTo(pad.l, pad.t); c.lineTo(pad.l, f.H - pad.b); c.lineTo(f.W - pad.r, f.H - pad.b); c.stroke();
      c.fillText(ymax.toFixed(Math.abs(ymax) < 10 ? 2 : 0), 2, pad.t + 8);
      c.fillText(ymin.toFixed(Math.abs(ymin) < 10 ? 2 : 0), 2, f.H - pad.b);
      if (ymin < 0 && ymax > 0) {
        var y0 = pad.t + (f.H - pad.t - pad.b) * (1 - (0 - ymin) / (ymax - ymin));
        c.strokeStyle = "#30363d"; c.beginPath(); c.moveTo(pad.l, y0); c.lineTo(f.W - pad.r, y0); c.stroke();
      }
      c.fillText(orbit[0].t.slice(5, 16).replace("T", " "), pad.l, f.H - 3);
      var e = orbit[orbit.length - 1].t.slice(5, 16).replace("T", " ");
      c.fillText(e, f.W - pad.r - c.measureText(e).width, f.H - 3);
    }
    var ds = orbit.map(function (o) { return o.d; }), dmax = Math.max.apply(null, ds);
    var dpos = ds.filter(function (v) { return v > 0; });
    var logD = dpos.length && dmax / Math.min.apply(null, dpos) > 200;
    var dy = logD ? ds.map(function (v) { return Math.log10(Math.max(v, 1e-3)); }) : ds;
    var dyMin = logD ? Math.min.apply(null, dy) : 0, dyMax = Math.max.apply(null, dy);
    var rtnArr = hasRtn ? [0, 1, 2].map(function (k) { return orbit.map(function (o) { return o.rtn[k]; }); }) : null;
    var rMax = hasRtn ? Math.max.apply(null, rtnArr.map(function (a) { return Math.max.apply(null, a.map(Math.abs)); })) || 1 : 1;
    function draw(curMs) {
      var f = frame(cd);
      axes(f, logD ? Math.pow(10, dyMin) : 0, logD ? Math.pow(10, dyMax) : dmax);
      if (logD) f.ctx.fillText("log", pad.l + 3, pad.t + 8);
      drawLine(f.ctx, f.W, f.H, xs, dy, dyMin, dyMax, "#f85149", pad);
      var g = frame(cr);
      if (hasRtn) {
        axes(g, -rMax, rMax);
        ["#f0883e", "#58a6ff", "#bc8cff"].forEach(function (col, k) { drawLine(g.ctx, g.W, g.H, xs, rtnArr[k], -rMax, rMax, col, pad); });
      } else {
        g.ctx.fillText("此場景快取無 RTN 分量（重新計算後可得）", pad.l + 4, g.H / 2);
      }
      if (curMs != null) {
        var x = (curMs - t0) / (t1 - t0);
        if (x >= 0 && x <= 1) {
          [f, g].forEach(function (q) {
            var px = pad.l + (q.W - pad.l - pad.r) * x;
            q.ctx.strokeStyle = "#e6edf3"; q.ctx.lineWidth = 1; q.ctx.beginPath(); q.ctx.moveTo(px, pad.t); q.ctx.lineTo(px, q.H - pad.b); q.ctx.stroke();
          });
        }
      }
    }
    draw(null);
    _charts = { draw: draw, hasRtn: hasRtn };
    var fn = document.getElementById("note");
    if (fn && meta.frame_note && fn.textContent.indexOf("RTN") < 0) fn.textContent += (fn.textContent ? " " : "") + meta.frame_note;
  }

  function buildScene(data) {
    clearScene();
    var meta = data.meta, orbit = data.orbit, summary = data.summary;
    _meta = meta;
    buildHUD(meta);
    setupCharts(orbit, meta);

    var t0 = C.JulianDate.fromIso8601(orbit[0].t);
    var t1 = C.JulianDate.fromIso8601(orbit[orbit.length - 1].t);

    var t0ms = C.JulianDate.toDate(t0).getTime();
    var stepMs = (summary.fine_step_s || 60) * 1000;
    function sampleAt(ms) {
      var idx = Math.round((ms - t0ms) / stepMs);
      return orbit[Math.min(orbit.length - 1, Math.max(0, idx))];
    }
    window.__rpoSampleAt = sampleAt;

    var pPos = sampledPos(orbit, "p"), sPos = sampledPos(orbit, "s");
    var trailS = +meta.trail_s || 5400;
    var eP = addSat(meta.primName, pPos, PRIM, t0, t1, trailS);
    var eS = addSat(meta.secName, sPos, SEC, t0, t1, trailS);
    _eP = eP; _eS = eS;

    // ── 碰撞機率球：兩顆衛星各畫一個 3σ 不確定「橢球」，三軸獨立（徑向 R / 沿軌 T / 越軌 N），
    //    各自配向至該衛星之 RTN(LVLH) 座標系；半徑 = 3σ_R / 3σ_T / 3σ_N；顏色隨當下 Pc。
    var sr = +meta.sigma_r || 0.1, st = +meta.sigma_t || 0.5, sn = +meta.sigma_n;
    if (!(sn > 0)) sn = sr;                       // 舊快取缺 σ_n → 退回 σ_r
    var rR = 3 * sr * 1000, rT = 3 * st * 1000, rN = 3 * sn * 1000;   // 半軸（公尺）

    // 由某位置屬性 + 有限差分速度求 RTN 基底，回傳「局部→世界(ECEF)」旋轉四元數之函式。
    // 橢球局部軸 x→R、y→T、z→N（對應 radii 之三軸）。
    function makeRtnQuat(posProp) {
      return function (time) {
        var p = posProp.getValue(time);
        if (!p) return C.Quaternion.IDENTITY;
        var p2 = posProp.getValue(C.JulianDate.addSeconds(time, 1, new C.JulianDate()))
              || posProp.getValue(C.JulianDate.addSeconds(time, -1, new C.JulianDate()));
        if (!p2) return C.Quaternion.IDENTITY;
        var R = C.Cartesian3.normalize(p, new C.Cartesian3());
        var v = C.Cartesian3.subtract(p2, p, new C.Cartesian3());
        if (C.Cartesian3.magnitude(v) < 1e-6) return C.Quaternion.IDENTITY;
        var vh = C.Cartesian3.normalize(v, v);
        var N = C.Cartesian3.cross(R, vh, new C.Cartesian3());
        if (C.Cartesian3.magnitude(N) < 1e-9) return C.Quaternion.IDENTITY;
        N = C.Cartesian3.normalize(N, N);
        var T = C.Cartesian3.normalize(C.Cartesian3.cross(N, R, new C.Cartesian3()), new C.Cartesian3());
        var m = new C.Matrix3(R.x, T.x, N.x, R.y, T.y, N.y, R.z, T.z, N.z);  // 欄=R,T,N
        return C.Quaternion.fromRotationMatrix(m, new C.Quaternion());
      };
    }

    function makePcEllipsoid(posProp) {
      return viewer.entities.add({
        position: posProp,
        orientation: new C.CallbackProperty(makeRtnQuat(posProp), false),
        show: _pcSphereOn,
        ellipsoid: {
          radii: new C.Cartesian3(rR, rT, rN),   // x=R, y=T, z=N（三軸獨立）
          fill: true,
          material: new C.ColorMaterialProperty(new C.CallbackProperty(function (time) {
            var s = sampleAt(C.JulianDate.toDate(time).getTime());
            return riskColor(s.pc).withAlpha(0.22);
          }, false)),
          outline: true,
          outlineWidth: 1.0,
          outlineColor: new C.CallbackProperty(function (time) {
            var s = sampleAt(C.JulianDate.toDate(time).getTime());
            return riskColor(s.pc).withAlpha(0.6);
          }, false),
        },
      });
    }

    _pcSpheres = [makePcEllipsoid(pPos), makePcEllipsoid(sPos)];   // 兩顆衛星各一
    buildTrails(data.trail, meta);
    buildRelWindow(orbit, meta, summary.fine_step_s || 600);
    buildRangeLabel(pPos, sPos, meta, sampleAt);
    var ccInfo = document.getElementById("comspocInfo");
    if (!data.trail && ccInfo) ccInfo.textContent = "此案例未產生全期軌跡（preset 需設 trail_step_min）；模式僅套用鏡頭與 Ranges。";
    var info = document.getElementById("pcSphereInfo");
    if (info) info.textContent = "3σ 橢球（三軸）· R " + rR.toFixed(0) + " / T " + rT.toFixed(0)
      + " / N " + rN.toFixed(0) + " m　（σ R " + sr + " / T " + st + " / N " + sn
      + " km）· 顏色隨 Pc：綠<1e-6 / 琥珀 / 紅>1e-4";

    var dmin = summary.d_min, dmax = orbit.reduce(function (m, o) { return Math.max(m, o.d); }, 0);
    viewer.entities.add({
      polyline: {
        positions: new C.CallbackProperty(function (time) {
          var a = pPos.getValue(time), b = sPos.getValue(time);
          return (a && b) ? [a, b] : [];
        }, false),
        width: 1.6,
        material: new C.ColorMaterialProperty(new C.CallbackProperty(function (time) {
          var s = sampleAt(C.JulianDate.toDate(time).getTime());
          var frac = dmax > dmin ? (s.d - dmin) / (dmax - dmin) : 0;
          return C.Color.fromCssColorString(CRIT).withAlpha(0.9 - 0.55 * Math.min(1, Math.max(0, frac)));
        }, false)),
      },
    });

    var iMin = 0; for (var i = 1; i < orbit.length; i++) if (orbit[i].d < orbit[iMin].d) iMin = i;
    var om = orbit[iMin];
    viewer.entities.add({
      position: C.Cartesian3.fromDegrees(om.s[0], om.s[1], om.s[2]),
      point: { pixelSize: 9, color: C.Color.fromCssColorString(CRIT), outlineColor: C.Color.WHITE, outlineWidth: 1.5 },
      label: { text: "TCA " + fmtRange(om.d) + " km", font: "11px monospace",
        fillColor: C.Color.fromCssColorString(CRIT), pixelOffset: new C.Cartesian2(0, 16),
        showBackground: true, backgroundColor: C.Color.fromCssColorString("#0A0D13").withAlpha(0.6) },
    });

    var spanSec = C.JulianDate.secondsDifference(t1, t0);
    viewer.clock.startTime = t0.clone();
    viewer.clock.stopTime = t1.clone();
    viewer.clock.currentTime = C.JulianDate.addSeconds(t0, spanSec / 2, new C.JulianDate());
    viewer.clock.clockRange = C.ClockRange.LOOP_STOP;
    // 播放速度：全窗約 3 分鐘播完（原 90 s 減半），讓機動段看得清楚
    viewer.clock.multiplier = Math.max(15, spanSec / 180);
    viewer.clock.shouldAnimate = true;
    paintPhases(meta.phases);                       // 需在 zoomTo 前掛上，色帶才會隨刻度繪出
    if (viewer.timeline) viewer.timeline.zoomTo(t0, t1);

    _tick = function (clock) {
      var nowMs = C.JulianDate.toDate(clock.currentTime).getTime();
      drawRelWindow(nowMs);
      var s = sampleAt(nowMs);
      document.getElementById("k_range").textContent = fmtRange(s.d);
      document.getElementById("k_pc").innerHTML = fmtPc(s.pc);
      document.getElementById("k_palt").textContent = (s.p[2] / 1000).toFixed(1);
      document.getElementById("k_salt").textContent = (s.s[2] / 1000).toFixed(1);
      if (_charts) {
        _charts.draw(C.JulianDate.toDate(clock.currentTime).getTime());
        var cs = document.getElementById("ch_stat");
        if (cs) cs.textContent = s.t.slice(0, 16).replace("T", " ") + "Z  |\u03c1| " + fmtRange(s.d) + " km" +
          (s.rtn ? "  R " + s.rtn[0].toFixed(2) + "  T " + s.rtn[1].toFixed(2) + "  N " + s.rtn[2].toFixed(2) + " km" : "");
      }
    };
    viewer.clock.onTick.addEventListener(_tick);

    document.getElementById("loading").style.display = "none";
    // 預設第三方遠景綜觀事件；操作者可循 遠景→primary→secondary 切換
    setViewpoint("t");
    if (_comspocOn) setComspoc(true);

    stat("TCA " + fmtRange(summary.d_min) + " km · Pc(proxy) " + fmtPc(summary.pc_max).replace("&lt;", "<")
      + " · " + summary.n_orbit + " 點");
  }

  function loadScene(primary, secondary) {
    var seq = ++_loadSeq;
    _curP = primary; _curS = secondary;
    stat("計算場景中 … (" + primary + " × " + secondary + ")");
    try { history.replaceState(null, "", "/rpo?primary=" + primary + "&secondary=" + secondary); } catch (e) {}
    fetch("/api/rpo/" + primary + "/" + secondary).then(function (r) { return r.json(); }).then(function (data) {
      if (seq !== _loadSeq) return;                       // 已被更新的請求取代
      if (data.error) { stat("無法載入：" + data.error, true); return; }
      if (!data.orbit || !data.orbit.length) { stat("此配對無有效軌道取樣（TLE 歷史不足或無重疊）", true); return; }
      buildScene(data);
      var sel = document.getElementById("pairSel");
      if (sel) sel.value = primary + "," + secondary;
    }).catch(function (e) {
      if (seq === _loadSeq) stat("讀取失敗：" + e.message, true);
    });
  }

  function optionLabel(p) {
    var mp = fmtRange(p.miss_km);
    return (p.primary_name || p.primary_norad) + " × " + (p.secondary_name || p.secondary_norad)
      + "  ·  " + mp + " km  ·  Pc " + (p.Pc_str || "—");
  }

  function populatePairs(threshold) {
    var sel = document.getElementById("pairSel");
    stat("掃描近距離配對 …");
    // 先取精選案例（歷史 RPO，如神龍系列），再併入即時近距離配對
    fetch("/api/rpo/presets").then(function (r) { return r.json(); }).catch(function () { return { presets: [] }; })
      .then(function (pd) {
        var presets = (pd && pd.presets) || [];
        fetch("/api/conjunctions?threshold_km=" + (threshold || 10)).then(function (r) { return r.json(); }).then(function (d) {
          var pairs = (d && d.pairs) || [];
          var html = "";
          var cur = _curP != null ? (_curP + "," + _curS) : null;
          // 保留目前（預設/深連結）配對於清單頂端
          if (cur) html += '<option value="' + cur + '">★ 目前：' + _curP + " × " + _curS + "</option>";
          // 精選案例（置於即時配對之前）
          for (var j = 0; j < presets.length; j++) {
            var pr = presets[j];
            var pv = pr.primary + "," + pr.secondary;
            if (pv === cur) continue;
            html += '<option value="' + pv + '">◎ 案例：' + pr.title + "</option>";
          }
          for (var i = 0; i < pairs.length && i < 150; i++) {
            var p = pairs[i];
            var v = p.primary_norad + "," + p.secondary_norad;
            if (v === cur) continue;   // 避免重複
            html += '<option value="' + v + '">' + optionLabel(p) + "</option>";
          }
          sel.innerHTML = html || '<option value="">（無近距離配對）</option>';
          if (cur) sel.value = cur;
          stat(presets.length + " 個精選案例 + " + pairs.length + " 個近距離配對（閾值 " + (threshold || 10) + " km）");
        }).catch(function (e) { stat("配對清單載入失敗：" + e.message, true); });
      });
  }

  window.startRPO = function () {
    try { initCesium(); } catch (e) { warn("Cesium 初始化失敗：" + e.message); return; }
    var initP = window.RPO_PRIMARY || 58573, initS = window.RPO_SECONDARY || 59884;
    _curP = initP; _curS = initS;

    var sel = document.getElementById("pairSel");
    if (sel) sel.addEventListener("change", function () {
      var v = this.value; if (!v) return;
      var a = v.split(","); loadScene(parseInt(a[0], 10), parseInt(a[1], 10));
    });
    var btn = document.getElementById("rescan");
    if (btn) btn.addEventListener("click", function () {
      var thr = parseFloat(document.getElementById("thr").value) || 10;
      populatePairs(thr);
    });
    var vpBtn = document.getElementById("vpToggle");
    if (vpBtn) vpBtn.addEventListener("click", function () {
      setViewpoint({ t: "p", p: "s", s: "t" }[_vp] || "t");   // 遠景→primary→secondary 循環
    });
    var pcChk = document.getElementById("pcSphereChk");
    _pcSphereOn = pcChk ? pcChk.checked : true;   // 預設開啟
    if (pcChk) pcChk.addEventListener("change", function () {
      _pcSphereOn = this.checked;
      _pcSpheres.forEach(function (e) { e.show = _pcSphereOn; });
    });

    var rwChk = document.getElementById("relwinChk");
    if (rwChk) {
      _relOn = rwChk.checked;
      rwChk.addEventListener("change", function () {
        _relOn = this.checked;
        if (_relwin && _relwin.el) _relwin.el.style.display = _relOn ? "" : "none";
      });
    }

    var ccChk = document.getElementById("comspocChk");
    if (ccChk) {
      _comspocOn = ccChk.checked;
      ccChk.addEventListener("change", function () { setComspoc(this.checked); });
    }
    var camBtn = document.getElementById("camToggle");
    if (camBtn) camBtn.addEventListener("click", function () {
      comspocCamera(_camMode === "wide" ? "close" : "wide");
    });

    populatePairs(parseFloat((document.getElementById("thr") || {}).value) || 10);
    loadScene(initP, initS);
  };
})();
