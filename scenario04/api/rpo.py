"""RPO 3D 場景路由：兩顆衛星相對接近 + Chan Pc 之 Cesium 3D 展示。

  GET /rpo                       → 3D 場景頁（預設神龍 58573 × 59884）
  GET /rpo/cases                 → RPO 案例總覽 landing page（各案例直接連結）
  GET /api/rpo/<prim>/<sec>      → 場景資料 JSON（orbit / series / meta / summary）
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request

from ..physics.rpo import _PRESETS, get_rpo_scene

logger = logging.getLogger(__name__)

bp = Blueprint("rpo", __name__)

# 預設展示案例：神龍太空梭在軌釋放與再接近（可經 ?primary=&secondary= 切換任一配對）
DEFAULT_PRIMARY = 58573
DEFAULT_SECONDARY = 59884


@bp.get("/api/rpo/presets")
def rpo_presets():
    """精選 RPO 案例配對（供選單置頂）；源自 physics.rpo._PRESETS。"""
    out = [{"primary": p, "secondary": s, "title": info.get("title", f"{p} × {s}")}
           for (p, s), info in _PRESETS.items()]
    return jsonify({"presets": out})


@bp.get("/rpo/cases")
def rpo_cases():
    """RPO 案例總覽：精選案例卡片 + 直接回放連結（特寫＝神龍4 × Object H）。"""
    cases = [{"primary": p, "secondary": s, **info} for (p, s), info in _PRESETS.items()]
    featured = next((c for c in cases
                     if (c["primary"], c["secondary"]) == (69673, 67689)), None)
    rest = [c for c in cases if c is not featured]
    return render_template("rpo_cases.html", featured=featured, cases=rest)


@bp.get("/rpo", strict_slashes=False)
def rpo_page():
    try:
        p = int(request.args.get("primary", DEFAULT_PRIMARY))
        s = int(request.args.get("secondary", DEFAULT_SECONDARY))
    except (TypeError, ValueError):
        p, s = DEFAULT_PRIMARY, DEFAULT_SECONDARY
    return render_template("rpo3d.html", default_primary=p, default_secondary=s)


@bp.get("/api/rpo/<int:primary>/<int:secondary>")
def rpo_data(primary: int, secondary: int):
    try:
        return jsonify(get_rpo_scene(primary, secondary))
    except (ValueError, RuntimeError) as e:
        logger.warning("RPO 場景計算失敗 %s×%s：%s", primary, secondary, e)
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # noqa: BLE001
        logger.exception("RPO 場景未預期錯誤 %s×%s", primary, secondary)
        return jsonify({"error": str(e)}), 500
