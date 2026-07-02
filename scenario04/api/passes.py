"""台北覆蓋 / 過頂預報 API（含時間軸版本）。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from ..config import settings
from ..physics.coverage import (
    compute_taipei_coverage,
    compute_taipei_coverage_at,
    predict_taipei_passes_at,
)
from ..services.passes_service import passes_service
from . import json_response, parse_float_arg

bp = Blueprint("passes", __name__)


def _parse_ts() -> datetime:
    """解析 ?ts=… 並夾在時間軸 ±TIMELINE_DAYS 範圍內。"""
    ts_str = request.args.get("ts", "").strip()
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        ts = datetime.now(timezone.utc)
    now = datetime.now(timezone.utc)
    ts  = max(ts, now - timedelta(days=settings.TIMELINE_DAYS))
    ts  = min(ts, now + timedelta(days=settings.TIMELINE_DAYS))
    return ts


@bp.get("/api/taipei_coverage")
def api_taipei_coverage():
    mask = parse_float_arg(request.args, "mask_deg", settings.MASK_DEG, 0.0, 85.0)
    t0 = time.monotonic()
    data = compute_taipei_coverage(mask_deg=mask)
    data["elapsed_sec"] = round(time.monotonic() - t0, 2)
    return json_response(data, max_age=60)


@bp.get("/api/taipei_passes")
def api_taipei_passes():
    hours = parse_float_arg(request.args, "hours",    24.0, 1.0, 72.0)
    step  = parse_float_arg(request.args, "step_sec", 60.0, 10.0, 300.0)
    mask  = parse_float_arg(request.args, "mask_deg", settings.MASK_DEG, 0.0, 85.0)

    status, payload = passes_service.get_or_submit(hours, step, mask)
    if status == "ready":
        return json_response(payload, max_age=300)
    if status == "error":
        return jsonify(payload), 500
    return jsonify({"status": "computing", "retry_after_sec": 5}), 202


@bp.get("/api/taipei_coverage_at")
def api_taipei_coverage_at():
    ts   = _parse_ts()
    mask = parse_float_arg(request.args, "mask_deg", settings.MASK_DEG, 0.0, 85.0)
    t0   = time.monotonic()
    data = compute_taipei_coverage_at(ts, mask_deg=mask)
    data["elapsed_sec"] = round(time.monotonic() - t0, 2)
    return json_response(data, max_age=60)


@bp.get("/api/taipei_passes_at")
def api_taipei_passes_at():
    ts    = _parse_ts()
    hours = parse_float_arg(request.args, "hours",    24.0, 1.0, 72.0)
    step  = parse_float_arg(request.args, "step_sec", 60.0, 10.0, 300.0)
    mask  = parse_float_arg(request.args, "mask_deg", settings.MASK_DEG, 0.0, 85.0)
    t0    = time.monotonic()
    data  = predict_taipei_passes_at(ts, hours=hours, step_sec=step, mask_deg=mask)
    data["elapsed_sec"] = round(time.monotonic() - t0, 2)
    return json_response(data, max_age=300)
