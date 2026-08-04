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


@bp.get("/starlink")
def starlink_page():
    return render_template("starlink.html")


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
