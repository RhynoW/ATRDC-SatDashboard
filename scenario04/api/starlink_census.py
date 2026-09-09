"""Starlink 顆數普查（vs keeptrack.space）與即時離軌名單 API。"""
from __future__ import annotations

from flask import Blueprint, request

from . import json_response
from ..physics.starlink_census import (
    count_v3_candidates,
    estimate_reentry_detail,
    keeptrack_starlink_count,
    list_deorbiting_starlinks,
    our_starlink_counts,
)

bp = Blueprint("starlink_census_api", __name__)


@bp.get("/api/starlink/census")
def api_starlink_census():
    """比對 keeptrack.space 公開數字與本系統依不同口徑算出的 Starlink 顆數。"""
    kt = keeptrack_starlink_count()
    ours = our_starlink_counts()
    return json_response({"keeptrack": kt, "ours": ours})


@bp.get("/api/starlink/deorbiting")
def api_starlink_deorbiting():
    """即時查詢目前正在離軌的 Starlink 名單（近期仍有 TLE、半長軸快速下降）。"""
    try:
        limit = max(1, min(int(request.args.get("limit", 60)), 300))
    except (TypeError, ValueError):
        limit = 60
    return json_response(list_deorbiting_starlinks(limit=limit), max_age=300)


@bp.get("/api/starlink/v3_census")
def api_starlink_v3_census():
    """啟發式估計疑似 Starlink V3 部署數量（依公開已知部署時程＋初始入軌殼層推測）。"""
    era_start = request.args.get("era_start", "").strip()
    return json_response(count_v3_candidates(era_start) if era_start else count_v3_candidates(),
                         max_age=300)


@bp.get("/api/starlink/reentry_detail")
def api_starlink_reentry_detail():
    """單顆衛星之零階再入估算（SGP4 逐圈外推近地點），供離軌清單逐列展開使用。"""
    try:
        norad_id = int(request.args.get("norad", ""))
    except (TypeError, ValueError):
        return json_response({"error": "norad 必填且需為整數"}), 400
    try:
        days = max(1.0, min(float(request.args.get("days", 10.0)), 30.0))
    except (TypeError, ValueError):
        days = 10.0
    return json_response(estimate_reentry_detail(norad_id, days=days), max_age=300)
