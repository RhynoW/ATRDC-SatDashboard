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

    from .api import register_blueprints
    register_blueprints(app)

    @app.errorhandler(Exception)
    def handle_error(err):
        from werkzeug.exceptions import HTTPException
        if isinstance(err, HTTPException):
            return err
        logger.exception("未預期例外")
        return jsonify({"error": str(err)}), 500

    return app
