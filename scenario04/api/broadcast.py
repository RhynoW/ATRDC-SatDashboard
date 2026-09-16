"""群播（Multicast）API：單一全域直播——一位主播操縱頁面，其餘連線者即時跟隨。

機制：SSE（Server-Sent Events）推播 + 主播端節流輪詢回報，不依賴額外套件
（如 flask-socketio）。狀態存於行程記憶體（單一 worker 假設，開發伺服器適用）。

端點：
    POST /api/broadcast/claim      搶下主播身分，回傳 token
    POST /api/broadcast/release    釋出主播身分
    POST /api/broadcast/heartbeat  維持主播心跳（狀態未變時仍需定期呼叫，避免逾時被釋出）
    POST /api/broadcast/state      主播回報最新頁面狀態（同時視為心跳）
    GET  /api/broadcast/status     目前是否有直播中、序號、更新時間
    GET  /api/broadcast/stream     SSE：狀態變化即推播給所有觀眾
"""
from __future__ import annotations

import secrets
import threading
import time
from typing import Any

from flask import Blueprint, Response, jsonify, request

from . import json_response

bp = Blueprint("broadcast", __name__)

HOST_TIMEOUT_S = 20.0     # 主播逾此秒數無回報（心跳/狀態）即視為離線，自動釋出
POLL_INTERVAL_S = 0.3     # SSE 迴圈檢查狀態變化的間隔
KEEPALIVE_S = 15.0        # SSE 無變化時仍需送出的保活註解間隔

_lock = threading.Lock()
_state: dict[str, Any] = {
    "active": False,
    "host_token": None,
    "last_heartbeat": 0.0,
    "seq": 0,
    "updated_at": 0.0,
    "payload": {},
}


def _expire_if_stale_locked() -> None:
    """呼叫端須已持有 _lock。主播逾時未回報則自動釋出直播位。"""
    if _state["active"] and (time.time() - _state["last_heartbeat"]) > HOST_TIMEOUT_S:
        _state["active"] = False
        _state["host_token"] = None
        _state["seq"] += 1
        _state["updated_at"] = time.time()


def _public_state_locked() -> dict[str, Any]:
    return {
        "active": _state["active"],
        "seq": _state["seq"],
        "updated_at": _state["updated_at"],
        "payload": _state["payload"] if _state["active"] else {},
    }


@bp.post("/api/broadcast/claim")
def api_broadcast_claim():
    with _lock:
        _expire_if_stale_locked()
        if _state["active"]:
            return json_response({"error": "already_live"}), 409
        token = secrets.token_urlsafe(18)
        now = time.time()
        _state.update(active=True, host_token=token, last_heartbeat=now,
                       updated_at=now, payload={})
        _state["seq"] += 1
        return json_response({"token": token, "seq": _state["seq"]})


@bp.post("/api/broadcast/release")
def api_broadcast_release():
    body = request.get_json(silent=True) or {}
    token = body.get("token")
    with _lock:
        if _state["active"] and token == _state["host_token"]:
            _state["active"] = False
            _state["host_token"] = None
            _state["seq"] += 1
            _state["updated_at"] = time.time()
            return json_response({"status": "released"})
    return json_response({"error": "not_host"}), 403


@bp.post("/api/broadcast/heartbeat")
def api_broadcast_heartbeat():
    body = request.get_json(silent=True) or {}
    token = body.get("token")
    with _lock:
        _expire_if_stale_locked()
        if not (_state["active"] and token == _state["host_token"]):
            return json_response({"error": "not_host"}), 403
        _state["last_heartbeat"] = time.time()
        return json_response({"status": "ok", "seq": _state["seq"]})


@bp.post("/api/broadcast/state")
def api_broadcast_state():
    body = request.get_json(silent=True) or {}
    token = body.get("token")
    patch = body.get("payload")
    with _lock:
        _expire_if_stale_locked()
        if not (_state["active"] and token == _state["host_token"]):
            return json_response({"error": "not_host"}), 403
        if isinstance(patch, dict):
            _state["payload"] = patch
            _state["seq"] += 1
        now = time.time()
        _state["last_heartbeat"] = now
        _state["updated_at"] = now
        return json_response({"status": "ok", "seq": _state["seq"]})


@bp.get("/api/broadcast/status")
def api_broadcast_status():
    with _lock:
        _expire_if_stale_locked()
        return json_response(_public_state_locked())


@bp.get("/api/broadcast/stream")
def api_broadcast_stream():
    def gen():
        last_seq = -1
        last_sent = 0.0
        while True:
            with _lock:
                _expire_if_stale_locked()
                snap = _public_state_locked()
            now = time.time()
            if snap["seq"] != last_seq:
                last_seq = snap["seq"]
                last_sent = now
                yield "event: state\ndata: " + _sse_json(snap) + "\n\n"
            elif now - last_sent > KEEPALIVE_S:
                last_sent = now
                yield ": keepalive\n\n"
            time.sleep(POLL_INTERVAL_S)

    resp = Response(gen(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


def _sse_json(data: Any) -> str:
    import json
    return json.dumps(data, ensure_ascii=False)
