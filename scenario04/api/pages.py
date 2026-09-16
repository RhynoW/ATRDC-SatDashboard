"""頁面路由：3D 地球儀主頁、台北 2D 覆蓋頁、本機 Cesium 靜態檔、Logo。

前端已抽離至 web/templates + web/static（Phase 1.1），
以 Jinja2 render_template() 傳入 context，不再以字串拼接產生 HTML。
"""
from __future__ import annotations

from flask import Blueprint, make_response, render_template, send_from_directory

from ..config import settings

bp = Blueprint("pages", __name__)


@bp.get("/")
def index():
    return render_template("globe.html")


@bp.get("/taipei")
def taipei_page():
    return render_template("taipei.html", cesium_token=settings.CESIUM_ION_TOKEN)


@bp.get("/cable")
def cable_page():
    """全球海纜態勢互動地圖（TeleGeography 圖資＋台灣斷纜事件；Cesium Ion 底圖）。"""
    return render_template("cable.html", cesium_token=settings.CESIUM_ION_TOKEN)


@bp.get("/starlink")
def starlink_page():
    return render_template("starlink.html")


@bp.get("/constellations")
def constellations_page():
    """星座排行頁 — 僅有效載荷（資料源 /api/stats/constellations）。"""
    return render_template("constellations.html")


@bp.get("/starlink-census")
def starlink_census_page():
    """Starlink 顆數普查：本系統 vs keeptrack.space 公開數字（資料源 /api/starlink/census）。"""
    return render_template("starlink_census.html")


@bp.get("/starlink-deorbit")
def starlink_deorbit_page():
    """即時離軌中的 Starlink 名單（資料源 /api/starlink/deorbiting、/api/starlink/reentry_detail）。"""
    return render_template("starlink_deorbit.html")


@bp.get("/starlink-v3")
def starlink_v3_page():
    """Starlink V3 佈署數量統計（啟發式；資料源 /api/starlink/v3_census）。"""
    return render_template("starlink_v3.html")


@bp.get("/orbit")
def orbit_page():
    """軌道要素歷史（Spiral Polar + SMA 圓形圖）；資料源 /api/orbit/history。"""
    return render_template("orbit.html")


@bp.get("/orbit-tuner")
def orbit_tuner_page():
    """軌道六參數調整器介紹頁（仿 AGI Orbit Tuner）；CTA 連回 /?open=orbit6 開啟互動面板。"""
    return render_template("orbit_tuner.html")


@bp.get("/cesium/<path:filename>")
def cesium_static(filename: str):
    safe = (settings.CESIUM_LOCAL_DIR / filename).resolve()
    if not str(safe).startswith(str(settings.CESIUM_LOCAL_DIR.resolve())):
        return make_response("Forbidden", 403)
    if not safe.is_file():
        return make_response(f"Cesium asset not found: {filename}", 404)
    return send_from_directory(str(settings.CESIUM_LOCAL_DIR), filename)


@bp.get("/api/logo")
def api_logo():
    if not settings.LOGO_FILE.exists():
        return "", 404
    resp = make_response(settings.LOGO_FILE.read_bytes())
    resp.headers["Content-Type"]  = "image/png"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp
