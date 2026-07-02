"""接近事件 + Space-Track CDM / Decay / SATCAT API。"""
from __future__ import annotations

import time
from typing import Any

from flask import Blueprint, jsonify, request

from ..config import settings
from ..ingestion import spacetrack
from ..ingestion.index import get_sat_index
from ..physics.conjunction import get_conjunctions
from . import json_response, parse_float_arg

bp = Blueprint("conjunctions", __name__)

# CDM 查詢快取（process-level）
_cdm_cache:          dict[int, list[dict]] = {}
_cdm_cache_at:       dict[int, float] = {}
_high_risk_cache:    dict[str, Any] | None = None
_high_risk_cache_at: float = 0.0


@bp.get("/api/conjunctions")
def api_conjunctions():
    threshold = parse_float_arg(
        request.args, "threshold_km", settings.CONJ_THRESHOLD_KM, 1.0, 500.0)
    try:
        max_pairs = int(request.args.get("max_pairs", 200))
        max_pairs = max(10, min(max_pairs, 2000))
    except ValueError:
        max_pairs = 200

    data = get_conjunctions(threshold_km=threshold, max_pairs=max_pairs)
    return json_response(data, max_age=settings.CONJ_TTL)


@bp.get("/api/cdm/high_risk")
def api_cdm_high_risk():
    global _high_risk_cache, _high_risk_cache_at
    if not spacetrack.ST_ENABLED:
        return jsonify({"error": "Space-Track 未配置", "count": 0, "events": []})
    now = time.monotonic()
    if _high_risk_cache is not None and (now - _high_risk_cache_at) < settings.CDM_CACHE_TTL:
        return jsonify(_high_risk_cache)
    idx = get_sat_index()
    limit = min(settings.CDM_HIGH_RISK_LIMIT, len(idx))
    subset = dict(list(idx.items())[:limit])
    nid_to_name = {nid: info["name"] for nid, info in subset.items()}
    t0 = time.monotonic()
    all_cdms = spacetrack.fetch_cdm_batch(nid_to_name)
    elapsed = time.monotonic() - t0
    high_risk: list[dict] = []
    for nid, events in all_cdms.items():
        for ev in events:
            if ev.get("pc", 0.0) > 1e-4:
                high_risk.append({
                    "norad_id":  nid,
                    "satellite": nid_to_name.get(nid, ""),
                    **ev,
                })
    high_risk.sort(key=lambda x: x.get("pc", 0.0), reverse=True)
    result: dict[str, Any] = {
        "count":       len(high_risk),
        "events":      high_risk,
        "scanned":     len(nid_to_name),
        "elapsed_sec": round(elapsed, 2),
    }
    _high_risk_cache    = result
    _high_risk_cache_at = now
    return jsonify(result)


@bp.get("/api/cdm/<int:norad_id>")
def api_cdm(norad_id: int):
    if not spacetrack.ST_ENABLED:
        return jsonify({"norad_id": norad_id, "error": "Space-Track 未配置",
                        "count": 0, "events": []})
    now = time.monotonic()
    if (norad_id in _cdm_cache
            and (now - _cdm_cache_at.get(norad_id, 0.0)) < settings.CDM_CACHE_TTL):
        events = _cdm_cache[norad_id]
    else:
        idx = get_sat_index()
        info = idx.get(norad_id)
        sat_name = info["name"] if info else f"NORAD-{norad_id}"
        events = spacetrack.fetch_cdm_for_satellite(sat_name)
        _cdm_cache[norad_id]    = events
        _cdm_cache_at[norad_id] = now
    idx = get_sat_index()
    satellite = (idx.get(norad_id) or {}).get("name", f"NORAD-{norad_id}")
    return jsonify({
        "norad_id":  norad_id,
        "satellite": satellite,
        "count":     len(events),
        "events":    events,
    })


@bp.get("/api/decay/<int:norad_id>")
def api_decay(norad_id: int):
    if not spacetrack.ST_ENABLED:
        return jsonify({"norad_id": norad_id, "error": "Space-Track 未配置"})
    idx = get_sat_index()
    info = idx.get(norad_id)
    sat_name = info["name"] if info else f"NORAD-{norad_id}"
    result = spacetrack.fetch_decay_prediction(sat_name)
    if result is None:
        return jsonify({"norad_id": norad_id, "message": "no decay prediction found"})
    return jsonify({"norad_id": norad_id, "satellite": sat_name, **result})


@bp.get("/api/satcat/<int:norad_id>")
def api_satcat_info(norad_id: int):
    if not spacetrack.ST_ENABLED:
        return jsonify({"norad_id": norad_id, "error": "Space-Track 未配置"})
    idx = get_sat_index()
    info = idx.get(norad_id)
    sat_name = info["name"] if info else f"NORAD-{norad_id}"
    result = spacetrack.fetch_satcat_info(sat_name)
    if result is None:
        return jsonify({"norad_id": norad_id, "satellite": sat_name,
                        "message": "no SATCAT found"})
    return jsonify({"norad_id": norad_id, "satellite": sat_name, **result})
