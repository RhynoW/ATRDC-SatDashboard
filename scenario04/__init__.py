"""scenario04 — 太空態勢儀表板（模組化重構版，Phase 1）。

原 scenario04-Cesium-advanced04.py（3880 行單一檔案）依
scenario04_redesign_phases_20260702.md Phase 1 拆分為：
  config/     設定與外部化資料（settings、SSN 站點、分類規則）
  ingestion/  DuckDB 存取、metadata、衛星索引、Space-Track
  physics/    純數學運算（SGP4、座標轉換、覆蓋、接近事件）
  services/   背景運算服務（過頂預報 executor）
  api/        Flask Blueprint（每組路由一個模組）
  web/        Jinja2 templates + static JS/CSS
"""
from __future__ import annotations

import logging
import os
import uuid

from flask import Flask, jsonify
from flask_cors import CORS

from .config import settings

logger = logging.getLogger(__name__)

_WEB_DIR = settings.PACKAGE_DIR / "web"


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(_WEB_DIR / "templates"),
        static_folder=str(_WEB_DIR / "static"),
    )
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    # POST 本體上限：最大合法請求（/api/positions/active 100 顆、Starlink 遮蔽格）遠小於此
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024

    # 安全標頭（2026-10-28 架構檢查）。frame-ancestors 放行 HF Space 外框，故不用 X-Frame-Options
    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "frame-ancestors 'self' https://huggingface.co https://*.huggingface.co https://*.hf.space")
        return resp

    # 靜態檔 cache-busting：url_for('static', …) 自動附 ?v=<mtime>，
    # 佈署更新後瀏覽器不會沿用快取的舊 JS/CSS（曾致前端修正未生效）。
    @app.url_defaults
    def _static_cache_bust(endpoint, values):
        if endpoint == "static" and "filename" in values and "v" not in values:
            fp = os.path.join(app.static_folder, values["filename"])
            try:
                values["v"] = int(os.stat(fp).st_mtime)
            except OSError:
                pass

    from .api import register_blueprints
    register_blueprints(app)

    @app.context_processor
    def _inject_cesium_cdn():
        from .config import settings
        return {"cesium_cdn": settings.CESIUM_CDN}

    @app.errorhandler(Exception)
    def handle_error(err):
        from werkzeug.exceptions import HTTPException
        if isinstance(err, HTTPException):
            return err
        # 對外只回固定訊息＋錯誤 ID；例外內容（含 DB 路徑等內部資訊）只寫 log
        err_id = uuid.uuid4().hex[:8]
        logger.exception("未預期例外 [%s]", err_id)
        return jsonify({"error": "伺服器內部錯誤", "error_id": err_id}), 500

    return app
