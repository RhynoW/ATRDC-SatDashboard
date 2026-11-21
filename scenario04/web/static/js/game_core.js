/* 線上版太空小遊戲 /game 的外框：載入 Cesium、建立 window.SDG（與 Rust 單機版 games.js 相同的介面：viewer、status、fmt、tw、icrf、tab），
 * 再依序載入共用的 games_debris_i18n.js、games_debris.js、games_debris_game.js（tools/sync_debris.py 自單機版複製）。
 * 語言：中／EN／日，存在 localStorage（game_lang）；靜態面板以字典替換文字節點，動態內容由共用程式的 relang 重畫。 */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var TW = 8 * 3600e3;
  var LANG = "zh";
  try { LANG = localStorage.getItem("game_lang") || "zh"; } catch (e) { /* 私密模式 */ }
  if (["zh", "en", "ja"].indexOf(LANG) < 0) LANG = "zh";
  window.SDG_LANG = LANG;

  // 頁面外框的字串（面板與遊戲本身的字串在 games_debris_i18n.js）
  var PAGE = {
    en: {
      "碎片連鎖 — 太空小遊戲": "Debris Cascade — Space Mini-Game",
      "💥 碎片連鎖 — 太空小遊戲": "💥 Debris Cascade — Space Mini-Game",
      "← 返回主頁": "← Home",
      "🎯 一顆大衛星解體，會引發碎片連鎖碰撞嗎？": "🎯 If a large satellite breaks up, will it trigger a debris collision cascade?",
      "選任務或情境（含歷史案例），或自選衛星、碎片數與解體方式": "Pick a mission or scenario (including historical cases), or choose a satellite, number of fragments and breakup type",
      "可選緩解決策卡，按「引爆」（預設 ×1 寫實；想看連鎖可改 ×1,000 誇張模式）": "Optionally pick mitigation cards and press “Detonate” (default ×1 realistic; try ×1,000 exaggerated to see a cascade)",
      "看結果、風險儀表板與成就；點「看全程」播放": "Read the results, risk dashboard and achievements; click “Whole run” to play it back",
      "▶ 示範": "▶ Demo",
      "自動帶入一組參數並執行": "fills in a set of parameters and runs it",
      "時間（UTC+8）": "Time (UTC+8)", "播放速度": "Playback speed", "自訂": "Custom",
      "1 分鐘／秒": "1 min/s", "1 小時／秒": "1 h/s", "1 天／秒": "1 day/s", "30 天／秒": "30 days/s",
      "計算核心與 ATRDC SatDashboard Rust 單機版相同（NASA 標準解體模型、NRLMSISE-00 大氣、Cosmos 1408 驗證）；線上版同時只算一個模擬，忙碌時會自動排隊重試。":
        "Same engine as the ATRDC SatDashboard Rust desktop edition (NASA Standard Breakup Model, NRLMSISE-00 atmosphere, Cosmos 1408 validation). The online version runs one simulation at a time and queues automatically when busy.",
      "載入 3D 地球中…": "Loading 3D globe…", "3D 地球載入失敗，請重新整理頁面": "Failed to load the 3D globe; please reload the page",
      "示範：已用 ×1,000 誇張模式引爆 Envisat，正在播放前 3 小時。": "Demo: Envisat detonated in ×1,000 exaggerated mode; playing the first 3 hours.",
      "示範中斷：": "Demo interrupted: ",
    },
    ja: {
      "碎片連鎖 — 太空小遊戲": "デブリ連鎖 — 宇宙ミニゲーム",
      "💥 碎片連鎖 — 太空小遊戲": "💥 デブリ連鎖 — 宇宙ミニゲーム",
      "← 返回主頁": "← ホーム",
      "🎯 一顆大衛星解體，會引發碎片連鎖碰撞嗎？": "🎯 大型衛星が破砕すると、デブリの連鎖衝突は起きるのか？",
      "選任務或情境（含歷史案例），或自選衛星、碎片數與解體方式": "ミッションまたはシナリオ（歴史的事例を含む）を選ぶか、衛星・破片数・破砕方式を自分で選ぶ",
      "可選緩解決策卡，按「引爆」（預設 ×1 寫實；想看連鎖可改 ×1,000 誇張模式）": "必要なら緩和策カードを選び、「起爆」を押す（既定は ×1 現実的。連鎖を見たいなら ×1,000 誇張モード）",
      "看結果、風險儀表板與成就；點「看全程」播放": "結果・リスクダッシュボード・実績を見る。「全期間」で再生",
      "▶ 示範": "▶ デモ",
      "自動帶入一組參數並執行": "パラメータを自動設定して実行",
      "時間（UTC+8）": "時刻（UTC+8）", "播放速度": "再生速度", "自訂": "カスタム",
      "1 分鐘／秒": "1 分／秒", "1 小時／秒": "1 時間／秒", "1 天／秒": "1 日／秒", "30 天／秒": "30 日／秒",
      "計算核心與 ATRDC SatDashboard Rust 單機版相同（NASA 標準解體模型、NRLMSISE-00 大氣、Cosmos 1408 驗證）；線上版同時只算一個模擬，忙碌時會自動排隊重試。":
        "計算エンジンは ATRDC SatDashboard Rust スタンドアロン版と同じです（NASA 標準破砕モデル、NRLMSISE-00 大気、Cosmos 1408 検証）。オンライン版は同時に 1 件だけ計算し、混雑時は自動で順番待ちして再試行します。",
      "載入 3D 地球中…": "3D 地球を読み込み中…", "3D 地球載入失敗，請重新整理頁面": "3D 地球の読み込みに失敗しました。ページを再読み込みしてください",
      "示範：已用 ×1,000 誇張模式引爆 Envisat，正在播放前 3 小時。": "デモ：Envisat を ×1,000 誇張モードで起爆し、最初の 3 時間を再生中です。",
      "示範中斷：": "デモ中断：",
    },
  };
  function P(s) { return (LANG !== "zh" && PAGE[LANG] && PAGE[LANG][s]) || s; }

  // 靜態文字：記住中文原文，依字典替換（結果區由共用程式自己重畫，不在這裡處理）
  var DYN = { "d-res": 1, "d-ev": 1, "d-ach": 1, "d-cards": 1, "d-compare": 1, "d-mission-res": 1, "d-mission-card": 1,
    "d-legend": 1, "d-kind-note": 1, "d-tpl-note": 1, "status": 1, "t-now-label": 1 };
  function tr(s) {
    if (LANG === "zh") return s;
    var d = window.SDG_I18N && window.SDG_I18N[LANG];
    return (d && d[s]) || (PAGE[LANG] && PAGE[LANG][s]) || s;
  }
  function translateDom(root) {
    (function walk(el) {
      if (el.nodeType === 1) {
        if (el.id && DYN[el.id]) return;
        if (el.tagName === "SCRIPT" || el.tagName === "STYLE") return;
        ["title", "placeholder"].forEach(function (a) {
          if (!el.hasAttribute(a)) return;
          var k = "zh" + a;
          if (el.dataset[k] == null) el.dataset[k] = el.getAttribute(a);
          el.setAttribute(a, tr(el.dataset[k]));
        });
        if (el.tagName === "OPTION" && el.dataset.dyn) return;   // 搜尋結果等動態選項由程式產生
        for (var c = el.firstChild; c; c = c.nextSibling) walk(c);
      } else if (el.nodeType === 3) {
        if (el._zh == null) { if (!el.nodeValue.trim()) return; el._zh = el.nodeValue; }
        var raw = el._zh, core = raw.trim();
        el.nodeValue = raw.replace(core, tr(core));
      }
    })(root);
  }
  function setLang(l) {
    LANG = window.SDG_LANG = l;
    try { localStorage.setItem("game_lang", l); } catch (e) { /* 略 */ }
    document.documentElement.lang = { zh: "zh-Hant", en: "en", ja: "ja" }[l];
    document.title = P("碎片連鎖 — 太空小遊戲");
    document.querySelectorAll(".lang-btn").forEach(function (b) { b.classList.toggle("active", b.dataset.lang === l); });
    ["top", "side", "loading"].forEach(function (id) { if ($(id)) translateDom($(id)); });
    var H = window.SDG_DEBRIS;
    if (H && H.relangGame) H.relangGame();
    if (H && H.relang) H.relang();
  }
  document.querySelectorAll(".lang-btn").forEach(function (b) { b.onclick = function () { setLang(b.dataset.lang); }; });
  setLang(LANG);

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function tw(d, sec) {
    var t = new Date(d.getTime() + TW);
    return t.getUTCFullYear() + "-" + pad(t.getUTCMonth() + 1) + "-" + pad(t.getUTCDate()) + " " +
      pad(t.getUTCHours()) + ":" + pad(t.getUTCMinutes()) + (sec ? ":" + pad(t.getUTCSeconds()) : "");
  }
  function fmt(x, n) { return Number(x).toLocaleString("en-US", { maximumFractionDigits: n || 0, minimumFractionDigits: n || 0 }); }
  function status(s) { $("status").textContent = s || ""; }

  function loadScript(src) {
    return new Promise(function (ok, no) {
      var s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = function () { no(new Error(src)); };
      document.body.appendChild(s);
    });
  }

  function start() {
    $("loading").remove();
    var viewer = new Cesium.Viewer("cesium", {
      // Cesium 內建的 Natural Earth II（隨 Cesium 從 CDN 或本站載入），不依賴本站底圖 API
      baseLayer: Cesium.ImageryLayer.fromProviderAsync(Cesium.TileMapServiceImageryProvider.fromUrl(
        Cesium.buildModuleUrl("Assets/Textures/NaturalEarthII"))),
      terrainProvider: new Cesium.EllipsoidTerrainProvider(),
      baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false,
      navigationHelpButton: false, animation: false, timeline: true, infoBox: false, selectionIndicator: false,
      fullscreenButton: false,
    });
    viewer.scene.globe.enableLighting = true;
    viewer.camera.frustum.far = 1e11;
    var clk = viewer.clock;
    clk.multiplier = 3600; clk.shouldAnimate = true;
    $("t-speed").value = "3600";
    // 時間軸標籤用台灣時間
    viewer.timeline.makeLabel = function (jd) { return tw(Cesium.JulianDate.toDate(jd)); };
    $("t-speed").onchange = function () {
      if (!this.value) return;
      clk.multiplier = +this.value; clk.clockRange = Cesium.ClockRange.UNBOUNDED; clk.shouldAnimate = true; $("t-play").textContent = "⏸";
    };
    $("t-play").onclick = function () { clk.shouldAnimate = !clk.shouldAnimate; this.textContent = clk.shouldAnimate ? "⏸" : "▶"; };
    setInterval(function () { $("t-now-label").textContent = tw(Cesium.JulianDate.toDate(clk.currentTime), true); }, 250);
    function icrf(jd, res) { return Cesium.Transforms.computeIcrfToFixedMatrix(jd, res) || Cesium.Transforms.computeTemeToPseudoFixedMatrix(jd, res); }
    window.SDG = { viewer: viewer, status: status, tw: tw, fmt: fmt, icrf: icrf, tab: function () { return "debris"; } };
    var v = "?v=" + (window.GAME_BUILD || "1");
    loadScript("/static/js/games_debris_i18n.js" + v)
      .then(function () { return loadScript("/static/js/games_debris.js" + v); })
      .then(function () { return loadScript("/static/js/games_debris_game.js" + v); })
      .then(function () {
        setLang(LANG);
        document.dispatchEvent(new CustomEvent("sdg-tab", { detail: "debris" }));
      })
      .catch(function (e) { status("載入失敗：" + e.message); });
  }

  // 示範：Envisat、×1,000 誇張模式引爆，播放前 3 小時
  $("pg-demo").onclick = function () {
    var btn = this, run = $("d-run");
    if (!run) return;
    btn.disabled = true;
    $("d-tpl").value = "envisat"; $("d-tpl").onchange();
    $("d-acc").value = "1000";
    run.click();
    var t0 = Date.now();
    (function wait() {
      if (!run.disabled || Date.now() - t0 > 240000) {
        btn.disabled = false;
        if (!run.disabled) status(P("示範：已用 ×1,000 誇張模式引爆 Envisat，正在播放前 3 小時。"));
        return;
      }
      setTimeout(wait, 400);
    })();
  };

  window.loadCesium(start, function () { $("loading").textContent = P("3D 地球載入失敗，請重新整理頁面"); });
})();
