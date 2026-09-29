"""頁面路由：3D 地球儀主頁、台北 2D 覆蓋頁、本機 Cesium 靜態檔、Logo。

前端已抽離至 web/templates + web/static（Phase 1.1），
以 Jinja2 render_template() 傳入 context，不再以字串拼接產生 HTML。
"""
from __future__ import annotations

from flask import Blueprint, make_response, redirect, render_template, send_from_directory

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


# 舊頁面已併入 /starlink 分頁；保留網址轉址（StoryMap 與書籤連結不失效）
_STARLINK_TAB_REDIRECTS = {"/starlink-census": "census", "/starlink-v3": "v3", "/starlink-deorbit": "deorbit"}


def _starlink_tab_redirect(tab: str):
    return lambda: redirect(f"/starlink?tab={tab}", code=302)


for _path, _tab in _STARLINK_TAB_REDIRECTS.items():
    bp.add_url_rule(_path, endpoint=f"starlink_tab_{_tab}", view_func=_starlink_tab_redirect(_tab))


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
