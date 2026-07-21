"""Starlink 服務能力分析 API：可見性、RTT 估算、遮蔽模擬。"""
from __future__ import annotations

from flask import Blueprint, request

from . import json_response, parse_float_arg
from ..config import settings
from ..physics.starlink_analysis import (
    compute_obstruction_analysis,
    compute_starlink_visibility,
)

bp = Blueprint("starlink_api", __name__)


@bp.get("/api/starlink/visibility")
def starlink_visibility():
    """
    計算 Starlink 星座對指定觀測點的幾何可用性（含 RTT 傳播延遲下限）。

    Query params:
        lat  (float)  觀測點緯度，預設台北 25.033
        lon  (float)  觀測點經度，預設台北 121.565
        mask (float)  仰角遮蔽（度），預設 25（Starlink 終端建議值）
        hours (int)   預報時數 1–72，預設 24
        step  (int)   時間步長（分）1–60，預設 15

    Returns:
        200 + JSON  — 計算完成
        202 + {"status":"computing"} — 背景計算中，客戶端請 1–2 秒後重試
    """
    lat      = parse_float_arg(request.args, "lat",  settings.TAIPEI_LAT, -90.0,   90.0)
    lon      = parse_float_arg(request.args, "lon",  settings.TAIPEI_LON, -180.0, 180.0)
    mask_deg = parse_float_arg(request.args, "mask", 25.0,                  0.0,   85.0)
    try:
        hours    = max(1, min(int(request.args.get("hours", 24)), 72))
        step_min = max(1, min(int(request.args.get("step",  15)), 60))
    except (TypeError, ValueError):
        hours, step_min = 24, 15

    result, ready = compute_starlink_visibility(lat, lon, mask_deg, hours, step_min)
    if not ready:
        return json_response({"status": "computing"}), 202
    return json_response(result)


@bp.post("/api/starlink/obstruction")
def starlink_obstruction():
    """
    套用用戶定義的水平線遮蔽（地形 / 建物遮蔽），回傳修正後的可見性時間序列。

    Request body (JSON):
        lat, lon, mask, hours, step  — 同 visibility（需已有快取結果）
        blocked_cells: [[az_center, el_center], ...]
            方位角 0–360°、仰角 mask_deg–90°，以 10°×5° 格為單位

    Returns:
        200  — 計算完成
        409  — 基礎資料尚未就緒（請先呼叫 /visibility）
        400  — 參數錯誤
    """
    body = request.get_json(silent=True) or {}
    try:
        lat      = float(body.get("lat",  settings.TAIPEI_LAT))
        lon      = float(body.get("lon",  settings.TAIPEI_LON))
        mask_deg = float(body.get("mask", 25.0))
        hours    = max(1, min(int(body.get("hours", 24)), 72))
        step_min = max(1, min(int(body.get("step",  15)), 60))
        raw_cells = body.get("blocked_cells", [])
        if not isinstance(raw_cells, list):
            raw_cells = []
        blocked_cells = [
            [float(c[0]), float(c[1])]
            for c in raw_cells
            if isinstance(c, (list, tuple)) and len(c) >= 2
        ]
    except (TypeError, ValueError, KeyError) as exc:
        return json_response({"error": f"參數錯誤：{exc}"}), 400

    result, ready = compute_obstruction_analysis(
        lat, lon, mask_deg, hours, step_min, blocked_cells,
    )
    if not ready:
        return json_response({"error": "基礎資料尚未就緒，請先呼叫 /api/starlink/visibility"}), 409
    return json_response(result)
