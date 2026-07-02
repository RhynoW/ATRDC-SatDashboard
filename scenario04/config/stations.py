"""SSN 地面觀測站清單（Phase 2.4）：由 config/ssn_stations.geojson 載入。

新增/修改感測站只需編輯 geojson 檔並呼叫 POST /api/admin/reload_cats，
不需改程式碼、不需重新部署。載入時做 schema 驗證：格式錯誤記警告而非崩潰。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from . import settings

logger = logging.getLogger(__name__)

_REQUIRED_PROPS = ("name", "type", "location", "status", "notes")

_EMPTY: dict[str, Any] = {"type": "FeatureCollection", "features": []}


def _validate(geo: dict[str, Any]) -> dict[str, Any]:
    if geo.get("type") != "FeatureCollection" or not isinstance(geo.get("features"), list):
        logger.warning("ssn_stations.geojson 不是 FeatureCollection，忽略")
        return dict(_EMPTY)
    valid: list[dict] = []
    for i, feat in enumerate(geo["features"]):
        geom  = feat.get("geometry") or {}
        props = feat.get("properties") or {}
        coords = geom.get("coordinates")
        if geom.get("type") != "Point" or not (
            isinstance(coords, (list, tuple)) and len(coords) == 2
        ):
            logger.warning("ssn_stations.geojson feature[%d] geometry 無效，略過", i)
            continue
        missing = [k for k in _REQUIRED_PROPS if k not in props]
        if missing:
            logger.warning(
                "ssn_stations.geojson feature[%d] (%s) 缺少欄位 %s",
                i, props.get("name", "?"), missing,
            )
        valid.append(feat)
    return {"type": "FeatureCollection", "features": valid}


def load_ssn_stations() -> dict[str, Any]:
    """讀取並驗證 SSN 站點 geojson；失敗時回傳空 FeatureCollection。"""
    try:
        with settings.SSN_STATIONS_FILE.open(encoding="utf-8") as f:
            geo = json.load(f)
    except Exception as exc:
        logger.error("ssn_stations.geojson 讀取失敗: %s", exc)
        return dict(_EMPTY)
    result = _validate(geo)
    logger.info("SSN 站點載入: %d 站", len(result["features"]))
    return result


# 模組層狀態：以 reload_ssn_stations() 熱重載（in-place 更新，引用不失效）
SSN_STATIONS: dict[str, Any] = load_ssn_stations()


def reload_ssn_stations() -> int:
    new = load_ssn_stations()
    SSN_STATIONS.clear()
    SSN_STATIONS.update(new)
    return len(SSN_STATIONS.get("features", []))
