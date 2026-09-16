/* broadcast.js — 通用「單一全域直播」群播用戶端元件。
 * 後端見 /api/broadcast/*（claim/release/heartbeat/state/status/stream，SSE 推播）。
 * 頁面只需呼叫 window.SatBroadcast.init({ getState, applyState, getLabels }) 一次；
 * 徽章 UI 與樣式由本檔自行注入，不需頁面另外準備 DOM/CSS。
 */
(function () {
  "use strict";

  var ROLE_IDLE = "idle", ROLE_HOST = "host", ROLE_FOLLOW = "following", ROLE_DETACHED = "detached";

  var role = ROLE_IDLE;
  var token = null;
  var lastSeq = -1;
  var lastPayload = null;
  var lastSentJson = null;
  var lastSentAt = 0;
  var opts = null;
  var es = null;
  var reportTimer = null;
  var els = {};

  function labels() {
    var d = (opts && opts.getLabels && opts.getLabels()) || {};
    return {
      become_host: d.become_host || "🔴 Start broadcast",
      live_self: d.live_self || "🔴 Broadcasting",
      end_host: d.end_host || "⏹ Stop broadcast",
      following: d.following || "👁 Following host",
      detach: d.detach || "Detach",
      detached: d.detached || "🔓 Detached (manual)",
      reattach: d.reattach || "Resume following",
      live_none: d.live_none || "No live host",
      claim_failed: d.claim_failed || "Someone else is already hosting",
      host_lost: d.host_lost || "Host session lost (timed out or taken over)",
    };
  }

  function injectStyle() {
    if (document.getElementById("sbcast-style")) return;
    var css = ""
      + "#sbcast{position:fixed;left:14px;bottom:14px;z-index:30;display:flex;align-items:center;gap:8px;"
      + "background:rgba(16,20,29,.92);border:1px solid rgba(255,255,255,.10);border-radius:10px;"
      + "padding:8px 10px;backdrop-filter:blur(6px);box-shadow:0 8px 24px rgba(0,0,0,.4);"
      + "font-family:ui-monospace,'JetBrains Mono','SF Mono',Menlo,Consolas,monospace;font-size:11.5px;color:#E8ECF4}"
      + "#sbcast .dot{width:8px;height:8px;border-radius:50%;background:#8894AC;flex:none}"
      + "#sbcast.live .dot{background:#FF5C4E;box-shadow:0 0 6px #FF5C4E}"
      + "#sbcast.follow .dot{background:#4FD08A;box-shadow:0 0 6px #4FD08A}"
      + "#sbcast .lbl{color:#c9d1e0;white-space:nowrap}"
      + "#sbcast button{background:#35C6F4;color:#08131b;border:0;border-radius:6px;padding:5px 9px;"
      + "font-family:inherit;font-size:11px;font-weight:600;cursor:pointer;white-space:nowrap}"
      + "#sbcast button:hover{filter:brightness(1.1)}"
      + "#sbcast button.danger{background:#FF5C4E}"
      + "#sbcast button.ghost{background:transparent;border:1px solid rgba(255,255,255,.25);color:#c9d1e0}";
    var s = document.createElement("style");
    s.id = "sbcast-style";
    s.textContent = css;
    document.head.appendChild(s);
  }

  function buildBadge() {
    if (els.root) return;
    var root = document.createElement("div");
    root.id = "sbcast";
    root.innerHTML = '<span class="dot"></span><span class="lbl" id="sbcastLbl"></span>'
      + '<button id="sbcastBtn1"></button><button id="sbcastBtn2" class="ghost" style="display:none"></button>';
    document.body.appendChild(root);
    els.root = root;
    els.lbl = document.getElementById("sbcastLbl");
    els.btn1 = document.getElementById("sbcastBtn1");
    els.btn2 = document.getElementById("sbcastBtn2");
    els.btn1.addEventListener("click", onBtn1Click);
    els.btn2.addEventListener("click", onBtn2Click);
  }

  function onBtn1Click() {
    if (role === ROLE_IDLE) becomeHost();
    else if (role === ROLE_HOST) stopHost();
    else if (role === ROLE_FOLLOW) detach();
    else if (role === ROLE_DETACHED) reattach();
  }
  function onBtn2Click() {
    if (role === ROLE_HOST) stopHost();
  }

  function render() {
    if (!els.root) return;
    var L = labels();
    els.root.className = (role === ROLE_HOST) ? "live" : (role === ROLE_FOLLOW ? "follow" : "");
    els.btn2.style.display = "none";
    if (role === ROLE_IDLE) {
      els.lbl.textContent = L.live_none;
      els.btn1.textContent = L.become_host;
      els.btn1.className = "";
    } else if (role === ROLE_HOST) {
      els.lbl.textContent = L.live_self;
      els.btn1.textContent = L.end_host;
      els.btn1.className = "danger";
    } else if (role === ROLE_FOLLOW) {
      els.lbl.textContent = L.following;
      els.btn1.textContent = L.detach;
      els.btn1.className = "ghost";
    } else if (role === ROLE_DETACHED) {
      els.lbl.textContent = L.detached;
      els.btn1.textContent = L.reattach;
      els.btn1.className = "";
    }
  }

  function setRole(r) { role = r; render(); }

  // ── 主播端：搶主播身分 + 節流回報 ────────────────────────────────
  function becomeHost() {
    fetch("/api/broadcast/claim", { method: "POST" })
      .then(function (r) { if (!r.ok) throw new Error("claim_failed"); return r.json(); })
      .then(function (d) {
        token = d.token;
        lastSentJson = null; lastSentAt = 0;
        setRole(ROLE_HOST);
        startReportLoop();
      })
      .catch(function () { alert(labels().claim_failed); });
  }

  function stopHost() {
    stopReportLoop();
    var t = token; token = null;
    setRole(ROLE_IDLE);
    if (t) fetch("/api/broadcast/release", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: t }),
    }).catch(function () {});
  }

  function startReportLoop() {
    stopReportLoop();
    var interval = (opts && opts.throttleMs) || 400;
    reportTimer = setInterval(function () {
      if (role !== ROLE_HOST || !opts || !opts.getState) return;
      var state;
      try { state = opts.getState(); } catch (e) { return; }
      var json = JSON.stringify(state);
      var now = Date.now();
      // 內容有變 → 立即送；內容不變 → 每 8s 送一次當心跳，避免逾時被伺服器釋出
      if (json === lastSentJson && now - lastSentAt < 8000) return;
      lastSentJson = json; lastSentAt = now;
      fetch("/api/broadcast/state", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token, payload: state }),
      }).then(function (r) {
        if (!r.ok) throw new Error("lost");
      }).catch(function () {
        stopReportLoop(); token = null; setRole(ROLE_IDLE);
        alert(labels().host_lost);
      });
    }, interval);
  }
  function stopReportLoop() {
    if (reportTimer) { clearInterval(reportTimer); reportTimer = null; }
  }

  // ── 觀眾端：跟隨 / 解除跟隨 ──────────────────────────────────────
  function detach() { setRole(ROLE_DETACHED); }
  function reattach() {
    setRole(ROLE_FOLLOW);
    if (lastPayload && opts && opts.applyState) {
      try { opts.applyState(lastPayload); } catch (e) {}
    }
  }

  function applyIncoming(active, seq, payload) {
    if (role === ROLE_HOST) return;   // 自己是主播：不套用（避免回饋迴圈）
    if (!active) {
      if (role === ROLE_FOLLOW || role === ROLE_DETACHED) setRole(ROLE_IDLE);
      return;
    }
    lastPayload = payload;
    if (seq === lastSeq) return;
    lastSeq = seq;
    if (role === ROLE_IDLE) setRole(ROLE_FOLLOW);
    if (role === ROLE_FOLLOW && opts && opts.applyState) {
      try { opts.applyState(payload); } catch (e) {}
    }
    // role === ROLE_DETACHED：僅記錄 lastPayload，不套用，等待使用者按「重新跟隨」
  }

  function connectStream() {
    try { es = new EventSource("/api/broadcast/stream"); } catch (e) { return; }
    es.addEventListener("state", function (ev) {
      var d;
      try { d = JSON.parse(ev.data); } catch (e) { return; }
      applyIncoming(!!d.active, d.seq, d.payload || {});
    });
    es.onerror = function () { /* EventSource 會自動重連 */ };
  }

  window.SatBroadcast = {
    init: function (o) {
      opts = o || {};
      injectStyle();
      buildBadge();
      render();
      connectStream();
    },
    refreshLabels: render,
    isHost: function () { return role === ROLE_HOST; },
    isFollowing: function () { return role === ROLE_FOLLOW; },
  };
})();
