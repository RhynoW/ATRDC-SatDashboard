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

  function addSat(name, pos, hex, t0, t1) {
    return viewer.entities.add({
      name: name,
      availability: new C.TimeIntervalCollection([new C.TimeInterval({ start: t0, stop: t1 })]),
      position: pos,
      point: { pixelSize: 11, color: C.Color.fromCssColorString(hex), outlineColor: C.Color.BLACK, outlineWidth: 1 },
      label: { text: name, font: "12px monospace", fillColor: C.Color.fromCssColorString(hex),
        style: C.LabelStyle.FILL, pixelOffset: new C.Cartesian2(0, -18),
        showBackground: true, backgroundColor: C.Color.fromCssColorString("#0A0D13").withAlpha(0.6) },
      path: { leadTime: 0, trailTime: 5400, width: 2, resolution: 60,
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
    _eP = _eS = null; _pcSpheres = [];
  }

  // 切換至指定衛星視角（相機跟隨該顆），並更新按鈕/標籤
  function setViewpoint(which) {
    if (!_eP || !_eS || !_meta) return;
    _vp = which;
    try { viewer.trackedEntity = (which === "p") ? _eP : _eS; } catch (e) {}
    var curName = (which === "p") ? _meta.primName : _meta.secName;
    var otherName = (which === "p") ? _meta.secName : _meta.primName;
    var lab = document.getElementById("vpLabel");
    if (lab) lab.textContent = "目前視角：" + curName;
    var btn = document.getElementById("vpToggle");
    if (btn) btn.textContent = "🎥 切換到 " + otherName + " 視角";
  }

  function buildScene(data) {
    clearScene();
    var meta = data.meta, orbit = data.orbit, summary = data.summary;
    _meta = meta;
    buildHUD(meta);

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
    var eP = addSat(meta.primName, pPos, PRIM, t0, t1);
    var eS = addSat(meta.secName, sPos, SEC, t0, t1);
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
    viewer.clock.multiplier = Math.max(30, spanSec / 90);
    viewer.clock.shouldAnimate = true;
    if (viewer.timeline) viewer.timeline.zoomTo(t0, t1);

    _tick = function (clock) {
      var s = sampleAt(C.JulianDate.toDate(clock.currentTime).getTime());
      document.getElementById("k_range").textContent = fmtRange(s.d);
      document.getElementById("k_pc").innerHTML = fmtPc(s.pc);
      document.getElementById("k_palt").textContent = (s.p[2] / 1000).toFixed(1);
      document.getElementById("k_salt").textContent = (s.s[2] / 1000).toFixed(1);
    };
    viewer.clock.onTick.addEventListener(_tick);

    document.getElementById("loading").style.display = "none";
    // 自動切換為其中一顆衛星視角（預設 primary）；相機跟隨該顆，可用按鈕來回切換
    setViewpoint("p");

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
      setViewpoint(_vp === "p" ? "s" : "p");   // 來回切換兩顆衛星視角
    });
    var pcChk = document.getElementById("pcSphereChk");
    _pcSphereOn = pcChk ? pcChk.checked : true;   // 預設開啟
    if (pcChk) pcChk.addEventListener("change", function () {
      _pcSphereOn = this.checked;
      _pcSpheres.forEach(function (e) { e.show = _pcSphereOn; });
    });

    populatePairs(parseFloat((document.getElementById("thr") || {}).value) || 10);
    loadScene(initP, initS);
  };
})();
