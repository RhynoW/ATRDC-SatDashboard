"""圖層與底圖 API：Globe 貼圖、底圖清單、國界 GeoJSON、SSN 站點。"""
from __future__ import annotations

import json
import logging

from flask import Blueprint, jsonify, make_response

from ..config import settings
from ..config.stations import SSN_STATIONS

logger = logging.getLogger(__name__)

bp = Blueprint("layers", __name__)

# ── 底圖目錄 ──────────────────────────────────────────────────────────────────
# type="single"  → 單張圖檔，走 /api/globe_texture/<name>
# type="tms"     → 本地 TMS 目錄，CesiumJS TileMapServiceImageryProvider
BASEMAP_CATALOG: dict[str, dict] = {
    "default": {
        "file": "globe_texture.jpg", "label": "預設底圖",
        "credit": "NASA Blue Marble", "type": "single",
        "candidates": [settings.GLOBE_TEXTURE_LOCAL],
    },
    "2k": {
        "file": "land_shallow_topo_2048.jpg", "label": "藍色大理石 2K",
        "credit": "NASA Visible Earth", "type": "single",
        "candidates": [settings.TEXTURE_DIR / "land_shallow_topo_2048.jpg"],
    },
    "topo": {
        "file": "world_topo_bathy_5400.jpg", "label": "地形+海底地形 5.4K",
        "credit": "NASA Visible Earth", "type": "single",
        "candidates": [settings.TEXTURE_DIR / "world_topo_bathy_5400.jpg"],
    },
    "natural_earth": {
        "file": "", "label": "Natural Earth II",
        "credit": "Natural Earth / CesiumJS (已內建)", "type": "tms",
        "tms_url": "/cesium/Assets/Textures/NaturalEarthII/",
        "candidates": [settings.CESIUM_LOCAL_DIR / "Assets" / "Textures"
                       / "NaturalEarthII" / "tilemapresource.xml"],
    },
}

_texture_file_cache:  dict[str, bytes] = {}
_globe_texture_cache: bytes | None = None
_borders_cache:       bytes | None = None


def _get_globe_texture() -> bytes | None:
    global _globe_texture_cache
    if _globe_texture_cache is not None:
        return _globe_texture_cache
    if settings.GLOBE_TEXTURE_LOCAL.exists():
        try:
            _globe_texture_cache = settings.GLOBE_TEXTURE_LOCAL.read_bytes()
            logger.info("Globe 貼圖載入: %d bytes", len(_globe_texture_cache))
            return _globe_texture_cache
        except Exception as exc:
            logger.warning("Globe 貼圖讀取失敗: %s", exc)
    # 本地不存在時嘗試 CDN fallback
    try:
        import requests
        logger.info("嘗試下載 Globe 貼圖 CDN fallback…")
        resp = requests.get(settings.GLOBE_TEXTURE_CDN, timeout=10)
        if resp.status_code == 200:
            _globe_texture_cache = resp.content
            logger.info("Globe 貼圖 CDN 下載成功: %d bytes", len(_globe_texture_cache))
            return _globe_texture_cache
    except Exception as exc:
        logger.error("Globe 貼圖 CDN fallback 失敗: %s", exc)
    logger.error("Globe 貼圖無法取得（本地: %s）", settings.GLOBE_TEXTURE_LOCAL)
    return None


def _image_response(data: bytes):
    ct = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    resp = make_response(data)
    resp.headers["Content-Type"]  = ct
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


@bp.get("/api/globe_texture")
def api_globe_texture():
    data = _get_globe_texture()
    if data is None:
        return make_response("Globe 貼圖無法取得", 503)
    return _image_response(data)


@bp.get("/api/globe_texture/<name>")
def api_globe_texture_named(name: str):
    """動態底圖服務：依 name 回傳 BASEMAP_CATALOG 中對應的圖檔（single 類型）。"""
    entry = BASEMAP_CATALOG.get(name)
    if entry is None:
        return make_response(f"未知底圖: {name}", 404)
    if entry.get("type") == "tms":
        return make_response("TMS 底圖請直接使用 tms_url", 400)
    path = next((p for p in entry["candidates"] if p.exists()), None)
    if path is None:
        return make_response(f"底圖檔案尚未下載: {entry['file']}", 503)
    if name not in _texture_file_cache:
        try:
            _texture_file_cache[name] = path.read_bytes()
            logger.info("底圖 '%s' 載入: %d bytes (%s)",
                        name, len(_texture_file_cache[name]), path.name)
        except Exception as exc:
            logger.error("底圖讀取失敗 %s: %s", path, exc)
            return make_response("底圖讀取失敗", 500)
    return _image_response(_texture_file_cache[name])


@bp.get("/api/textures")
def api_textures():
    """回傳可用底圖清單，含 type/tms_url 與是否可用。"""
    result = []
    for key, entry in BASEMAP_CATALOG.items():
        available = any(p.exists() for p in entry["candidates"])
        item = {
            "key":       key,
            "label":     entry["label"],
            "credit":    entry["credit"],
            "type":      entry.get("type", "single"),
            "available": available,
        }
        if entry.get("type") == "tms":
            item["tms_url"] = entry.get("tms_url", "")
        result.append(item)
    return jsonify(result)


# ── 幾何工具：判斷粗糙台灣輪廓（Natural Earth 110m）以便用精細行政界替換 ─────
_TAIWAN_PROBE = (121.0, 23.7)   # 台灣本島內陸點
_MAX_STRIP_SPAN_DEG = 15.0      # 安全護欄：只剔除小範圍 feature，避免誤刪大陸級多邊形


def _point_in_ring(lon: float, lat: float, ring: list) -> bool:
    """Ray casting：點是否在多邊形外環內。"""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def _feature_contains(feat: dict, lon: float, lat: float) -> bool:
    geom = feat.get("geometry") or {}
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    rings: list = []
    if gtype == "Polygon" and coords:
        rings = [coords[0]]
    elif gtype == "MultiPolygon":
        rings = [poly[0] for poly in coords if poly]
    for ring in rings:
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        if (max(lons) - min(lons) > _MAX_STRIP_SPAN_DEG
                or max(lats) - min(lats) > _MAX_STRIP_SPAN_DEG):
            continue
        if _point_in_ring(lon, lat, ring):
            return True
    return False


def strip_taiwan_features(raw: dict) -> int:
    """自 FeatureCollection 移除包含台灣本島探測點的（小範圍）feature；回傳移除數。"""
    feats = raw.get("features")
    if not isinstance(feats, list):
        return 0
    kept = [f for f in feats if not _feature_contains(f, *_TAIWAN_PROBE)]
    removed = len(feats) - len(kept)
    raw["features"] = kept
    return removed


def _load_borders_dict() -> dict:
    if settings.BORDERS_LOCAL.exists():
        try:
            raw = json.loads(settings.BORDERS_LOCAL.read_bytes())
            logger.info("國界 GeoJSON 本地: %d features", len(raw.get("features", [])))
            return raw
        except Exception as exc:
            logger.warning("國界讀取失敗: %s", exc)
    try:
        import requests
        logger.info("下載 Natural Earth 國界: %s", settings.NE_BORDERS_URL)
        r = requests.get(settings.NE_BORDERS_URL, timeout=30,
                         headers={"User-Agent": "ATRDC-TLE-Tracker/1.0"})
        r.raise_for_status()
        raw = json.loads(r.content)
        for feat in raw.get("features", []):
            feat["properties"] = {}
        try:
            settings.BORDERS_LOCAL.parent.mkdir(parents=True, exist_ok=True)
            settings.BORDERS_LOCAL.write_bytes(
                json.dumps(raw, separators=(",", ":")).encode("utf-8"))
        except Exception:
            pass
        return raw
    except Exception as exc:
        logger.warning("國界下載失敗: %s", exc)
        return {"type": "FeatureCollection", "features": []}


def reset_borders_cache() -> None:
    """清除國界記憶體快取（admin reload 後重新套用台灣剔除判斷）。"""
    global _borders_cache
    _borders_cache = None


@bp.get("/api/layers/borders")
def api_layer_borders():
    global _borders_cache
    if _borders_cache is None:
        raw = _load_borders_dict()
        # 精細台灣行政界檔存在 → 剔除 Natural Earth 的粗糙台灣輪廓（由前端疊加精細版）
        if settings.TAIWAN_ADMIN_FILE.exists():
            removed = strip_taiwan_features(raw)
            if removed:
                logger.info("全球國界：剔除粗糙台灣輪廓 %d 個 feature（改用 %s）",
                            removed, settings.TAIWAN_ADMIN_FILE.name)
        _borders_cache = json.dumps(raw, separators=(",", ":")).encode("utf-8")
    resp = make_response(_borders_cache)
    resp.headers["Content-Type"]  = "application/json; charset=utf-8"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


# ── 使用者自訂 GeoJSON 圖層（scenario04/geojson/*.geojson）──────────────────

@bp.get("/api/layers/user_geojson")
def api_user_geojson_list():
    """列出使用者自訂 geojson 圖層檔（放檔案即生效）。"""
    d = settings.USER_GEOJSON_DIR
    result = []
    if d.is_dir():
        for f in sorted(d.glob("*.geojson")):
            item: dict = {"name": f.name, "size_kb": round(f.stat().st_size / 1024, 1)}
            try:
                raw = json.loads(f.read_text(encoding="utf-8-sig"))
                item["features"] = len(raw.get("features", []))
            except Exception as exc:
                item["error"] = f"解析失敗: {exc}"
                logger.warning("user geojson 解析失敗 %s: %s", f.name, exc)
            result.append(item)
    return jsonify(result)


@bp.get("/api/layers/user_geojson/<name>")
def api_user_geojson_file(name: str):
    """回傳指定使用者 geojson 檔內容。"""
    if "/" in name or "\\" in name or ".." in name or not name.endswith(".geojson"):
        return make_response("無效檔名", 400)
    path = settings.USER_GEOJSON_DIR / name
    if not path.is_file():
        return make_response(f"找不到圖層檔: {name}", 404)
    try:
        data = path.read_bytes()
    except Exception as exc:
        logger.error("user geojson 讀取失敗 %s: %s", name, exc)
        return make_response("讀取失敗", 500)
    resp = make_response(data)
    resp.headers["Content-Type"]  = "application/json; charset=utf-8"
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@bp.get("/api/layers/ssn_stations")
def api_layer_ssn_stations():
    resp = make_response(
        json.dumps(SSN_STATIONS, ensure_ascii=False).encode("utf-8"))
    resp.headers["Content-Type"]  = "application/json; charset=utf-8"
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp
