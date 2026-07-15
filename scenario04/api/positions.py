"""衛星位置/統計/搜尋 API：/api/stats、/api/positions、/api/search 等。"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, jsonify, request

from ..config import settings
from ..ingestion.db import get_db_info
from ..ingestion.index import get_sat_index, get_stats
from ..physics.propagate import (
    HAS_SATREC_ARRAY,
    propagate_arc,
    propagate_batch,
    propagate_now,
)
from . import json_response
from .colors import get_color

logger = logging.getLogger(__name__)

bp = Blueprint("positions", __name__)


@bp.get("/api/stats")
def api_stats():
    payload_only = request.args.get("payload_only", "0") == "1"
    return jsonify(get_stats(payload_only=payload_only))


@bp.get("/api/db_info")
def api_db_info():
    return json_response(get_db_info(), max_age=settings.DB_INFO_TTL, default=str)


@bp.get("/api/positions")
def api_positions():
    ftype = request.args.get("ftype", "").strip()
    fval  = request.args.get("fval",  "").strip()
    payload_only = request.args.get("payload_only", "0") == "1"
    VALID = {"country", "purpose", "era", "constellation"}
    if ftype not in VALID or not fval:
        return jsonify({"error": "ftype 必須為 country/purpose/era/constellation，且 fval 不可空白"}), 400

    EXCLUDE = {"碎片", "火箭體"} if payload_only else set()
    idx = get_sat_index()
    matched = [n for n, i in idx.items()
               if i.get(ftype) == fval and i.get("purpose") not in EXCLUDE]

    total = len(matched)
    t0    = time.monotonic()
    logger.info("向量化傳播 %d 顆（%s=%s）", total, ftype, fval)

    positions = propagate_batch(matched, idx)
    elapsed   = time.monotonic() - t0
    logger.info("傳播完成 %d 顆，耗時 %.2f s（%s）",
                total, elapsed, "SatrecArray" if HAS_SATREC_ARRAY else "sequential")

    color   = get_color(ftype, fval)
    results = []
    for nid, pos in zip(matched, positions):
        if pos is None:
            continue
        lat, lon, alt = pos
        info = idx[nid]
        results.append({
            "norad_id":      nid,
            "name":          info["name"],
            "country":       info["country"],
            "purpose":       info["purpose"],
            "era":           info["era"],
            "constellation": info["constellation"] or "—",
            "color":         color,
            "lat":           round(lat, 4),
            "lon":           round(lon, 4),
            "alt_km":        round(alt, 1),
        })

    return jsonify({
        "ftype":         ftype,
        "fval":          fval,
        "count":         len(results),
        "total_matched": total,
        "sampled":       False,
        "elapsed_sec":   round(elapsed, 3),
        "vectorized":    HAS_SATREC_ARRAY,
        "satellites":    results,
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    })


@bp.get("/api/position/<int:norad_id>")
def api_position_single(norad_id: int):
    idx = get_sat_index()
    info = idx.get(norad_id)
    if info is None:
        return jsonify({"error": f"NORAD {norad_id} 不在索引中"}), 404
    pos = propagate_now(info["line1"], info["line2"])
    result: dict[str, Any] = {
        "norad_id":      norad_id,
        "name":          info["name"],
        "country":       info["country"],
        "purpose":       info["purpose"],
        "era":           info["era"],
        "constellation": info["constellation"] or "—",
    }
    if pos:
        result["lat"]    = round(pos[0], 4)
        result["lon"]    = round(pos[1], 4)
        result["alt_km"] = round(pos[2], 1)
    return jsonify(result)


@bp.get("/api/sat_orbit")
def api_sat_orbit():
    """回傳單顆衛星 SGP4 外推軌道弧（預設 2h / 120 點）。"""
    try:
        norad_id = int(request.args.get("norad_id", 0))
    except ValueError:
        return jsonify({"error": "norad_id 必須為整數"}), 400
    hours = float(request.args.get("hours", "2"))
    pts   = min(int(request.args.get("pts", "120")), 720)
    idx   = get_sat_index()
    info  = idx.get(norad_id)
    if info is None:
        return jsonify({"error": f"NORAD {norad_id} 不在索引中"}), 404
    l1, l2 = info.get("line1", ""), info.get("line2", "")
    if not l1 or not l2:
        return jsonify({"error": f"NORAD {norad_id} 無 TLE 資料"}), 404
    positions = propagate_arc(l1, l2, hours=hours, pts=pts)
    return jsonify({
        "norad_id":  norad_id,
        "name":      info["name"],
        "hours":     hours,
        "pts":       len(positions),
        "positions": positions,
    })


@bp.get("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"results": [], "count": 0, "query": q})

    idx  = get_sat_index()
    q_up = q.upper()
    matches: list[dict[str, Any]] = []

    if q.isdigit():
        nid = int(q)
        if nid in idx:
            matches.append({"norad_id": nid, **idx[nid], "score": 0})

    for nid, info in idx.items():
        if q_up in info["name"].upper():
            if not any(m["norad_id"] == nid for m in matches):
                matches.append({"norad_id": nid, **info, "score": 1})
        if len(matches) >= 60:
            break

    matches.sort(key=lambda x: (x["score"], x["name"]))
    top = matches[:20]

    nids      = [m["norad_id"] for m in top]
    positions = propagate_batch(nids, idx)

    results = []
    for m, pos in zip(top, positions):
        r: dict[str, Any] = {
            "norad_id":      m["norad_id"],
            "name":          m["name"],
            "country":       m["country"],
            "purpose":       m["purpose"],
            "era":           m["era"],
            "constellation": m["constellation"] or "—",
        }
        if pos:
            r["lat"]    = round(pos[0], 4)
            r["lon"]    = round(pos[1], 4)
            r["alt_km"] = round(pos[2], 1)
        results.append(r)

    return jsonify({"results": results, "count": len(results), "query": q})
