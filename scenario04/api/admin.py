"""管理 API：熱重載外部設定（不需重啟服務）。

Phase 2.3「YAML 熱重載」模式套用到所有外部化資料：
- overpass_cats.yaml         過頂分析衛星類別
- classification_rules.yaml  國別/星座分類規則
- ssn_stations.geojson       SSN 地面觀測站清單
"""
from __future__ import annotations

import hmac
import os

from flask import Blueprint, jsonify, request

from ..config.stations import reload_ssn_stations
from ..ingestion.index import invalidate_index
from ..ingestion.metadata import reload_classification_rules
from ..ingestion.user_defined import load_tracking_list, load_user_catalogue, load_user_tles
from ..physics.coverage import reload_overpass_cats

bp = Blueprint("admin", __name__)


def _admin_allowed() -> bool:
    """有設 ADMIN_TOKEN → 須帶相符的 X-Admin-Token 標頭；未設 → 僅限本機呼叫。
    公開部署（HF Space）原本任何人可反覆 POST 觸發 3.2 萬筆索引重建（2026-10-28 架構檢查）。"""
    token = os.getenv("ADMIN_TOKEN", "")
    if token:
        return hmac.compare_digest(request.headers.get("X-Admin-Token", ""), token)
    return request.remote_addr in ("127.0.0.1", "::1")


@bp.post("/api/admin/reload_cats")
def api_reload_cats():
    """POST /api/admin/reload_cats — 熱重載所有外部化設定與使用者自訂資料。"""
    if not _admin_allowed():
        return jsonify({"error": "forbidden"}), 403
    from .layers import reset_borders_cache

    cats  = reload_overpass_cats()
    rules = reload_classification_rules()
    n_stations = reload_ssn_stations()
    # 使用者自訂 TLE / 目錄併在索引中 → 清除索引快取，下次請求立即重建
    invalidate_index()
    # 國界快取重置（重新套用「精細台灣行政界存在 → 剔除粗糙輪廓」判斷）
    reset_borders_cache()
    return jsonify({
        "status":               "ok",
        "categories":           cats,
        "classification_rules": rules,
        "ssn_stations":         n_stations,
        "sat_index":            "invalidated",
        "user_tles":            len(load_user_tles()),
        "user_catalogue":       len(load_user_catalogue()),
        "tracking_list":        len(load_tracking_list()),
    })
