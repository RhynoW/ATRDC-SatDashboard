"""使用者自訂資料 API：自訂 TLE / 衛星目錄 / NORAD 監測。

- GET /api/user/tles           使用者自訂 TLE 衛星（含即時位置）
- GET /api/user/catalogue      使用者自訂衛星目錄原始內容
- GET /api/tracking/list       NORAD 監測清單（併入索引中的衛星資訊）
- GET /api/tracking/positions  監測衛星即時位置（?ids=1,2,3 可覆寫清單）
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, jsonify, request

from ..ingestion.index import get_sat_index
from ..ingestion.user_defined import (
    load_tracking_list,
    load_user_catalogue,
    load_user_tles,
)
from ..physics.propagate import propagate_batch
from . import json_response

logger = logging.getLogger(__name__)

bp = Blueprint("user_data", __name__)

_MAX_TRACK_IDS = 50


def _sat_info(idx: dict, nid: int) -> dict[str, Any]:
    info = idx.get(nid) or {}
    return {
        "norad_id":      nid,
        "name":          info.get("name", f"NORAD-{nid}"),
        "name_zh":       info.get("name_zh", ""),
        "country":       info.get("country", "不明"),
        "purpose":       info.get("purpose", "—"),
        "era":           info.get("era", "不明"),
        "constellation": info.get("constellation") or "—",
        "operator":      info.get("operator", ""),
        "notes":         info.get("notes", ""),
        "user_defined":  bool(info.get("user_defined")),
        "in_index":      nid in idx,
    }


@bp.get("/api/user/tles")
def api_user_tles():
    """使用者自訂 TLE 衛星清單 + 即時位置。"""
    tles = load_user_tles()
    idx  = get_sat_index()
    nids = [n for n in tles if n in idx]
    positions = propagate_batch(nids, idx)
    pos_map = dict(zip(nids, positions))

    sats = []
    for nid, tle in sorted(tles.items()):
        item = _sat_info(idx, nid)
        item["source_file"] = tle["source_file"]
        item["tle_name"]    = tle["name"]
        pos = pos_map.get(nid)
        if pos:
            item["lat"], item["lon"], item["alt_km"] = (
                round(pos[0], 4), round(pos[1], 4), round(pos[2], 1))
        sats.append(item)

    return json_response({
        "count":      len(sats),
        "satellites": sats,
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    })


@bp.get("/api/user/catalogue")
def api_user_catalogue():
    """使用者自訂衛星目錄（CSV 原始欄位）。"""
    cat = load_user_catalogue()
    rows = [{"norad_id": nid, **row} for nid, row in sorted(cat.items())]
    return json_response({"count": len(rows), "rows": rows})


@bp.get("/api/tracking/list")
def api_tracking_list():
    """監測清單（CSV 定義 + 索引資訊），供前端面板初始化。"""
    idx = get_sat_index()
    items = []
    for entry in load_tracking_list():
        item = {**entry, **_sat_info(idx, entry["norad_id"])}
        # alias 未填時退回衛星名稱
        if not item["alias"]:
            item["alias"] = item["name"]
        items.append(item)
    return json_response({
        "count":     len(items),
        "enabled":   sum(1 for i in items if i["enabled"]),
        "items":     items,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@bp.get("/api/tracking/positions")
def api_tracking_positions():
    """監測衛星即時位置。

    ?ids=25544,42920 指定要追蹤的 NORAD ID（前端勾選狀態 + 手動加入）；
    未給 ids 時使用 CSV 中 enabled=Y 的清單。
    """
    tracking = {e["norad_id"]: e for e in load_tracking_list()}

    ids_param = request.args.get("ids", "").strip()
    if ids_param:
        nids: list[int] = []
        for tok in ids_param.split(","):
            tok = tok.strip()
            if tok.isdigit():
                nids.append(int(tok))
        nids = list(dict.fromkeys(nids))[:_MAX_TRACK_IDS]
    else:
        nids = [nid for nid, e in tracking.items() if e["enabled"]][:_MAX_TRACK_IDS]

    idx = get_sat_index()
    known = [n for n in nids if n in idx]
    positions = propagate_batch(known, idx)
    pos_map = dict(zip(known, positions))

    results = []
    for nid in nids:
        item = _sat_info(idx, nid)
        meta = tracking.get(nid)
        item["alias"]    = (meta["alias"] if meta and meta["alias"] else item["name"])
        item["color"]    = meta["color"] if meta else ""
        item["priority"] = meta["priority"] if meta else "medium"
        item["priority_label"] = meta["priority_label"] if meta else "中"
        if meta and meta["notes"]:
            item["notes"] = meta["notes"]
        pos = pos_map.get(nid)
        if pos:
            item["ok"] = True
            item["lat"], item["lon"], item["alt_km"] = (
                round(pos[0], 4), round(pos[1], 4), round(pos[2], 1))
        else:
            item["ok"] = False
            item["error"] = ("不在索引中" if nid not in idx else "SGP4 傳播失敗")
        results.append(item)

    return json_response({
        "count":      len(results),
        "ok_count":   sum(1 for r in results if r["ok"]),
        "satellites": results,
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    })
