"""API 層（Phase 1.2）：每組路由一個 Blueprint 模組。"""
from __future__ import annotations

import json
from typing import Any

from flask import Flask, Response, make_response


def json_response(data: Any, max_age: int | None = None, default=None) -> Response:
    """UTF-8（ensure_ascii=False）JSON 回應，選配 Cache-Control。"""
    resp = make_response(
        json.dumps(data, ensure_ascii=False, default=default).encode("utf-8"))
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    if max_age is not None:
        resp.headers["Cache-Control"] = f"public, max-age={max_age}"
    return resp


def parse_float_arg(args, name: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(args.get(name, default))
        return max(lo, min(v, hi))
    except ValueError:
        return default


def register_blueprints(app: Flask) -> None:
    from . import admin, conjunctions, layers, pages, passes, positions, user_data
    app.register_blueprint(pages.bp)
    app.register_blueprint(positions.bp)
    app.register_blueprint(passes.bp)
    app.register_blueprint(conjunctions.bp)
    app.register_blueprint(layers.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(user_data.bp)
