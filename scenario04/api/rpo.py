"""RPO 3D 場景路由：兩顆衛星相對接近 + Chan Pc 之 Cesium 3D 展示。

  GET /rpo                       → 3D 場景頁（預設神龍 58573 × 59884）
  GET /rpo/cases                 → RPO 案例總覽 landing page（各案例直接連結）
  GET /api/rpo/<prim>/<sec>      → 場景資料 JSON（orbit / series / meta / summary；?variant= 選同配對之其他事件段）
  GET /api/rpo/<prim>/<sec>/report.png → 三面板靜態影像報告（?lang=zh|en&theme=light|dark&lookback=&forecast=&variant=）
"""
from __future__ import annotations

import io
import logging

from flask import Blueprint, jsonify, render_template, request, send_file

from ..physics.rpo import _PRESET_VARIANTS, _PRESETS, get_rpo_scene, valid_variant

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
    out += [{"primary": p, "secondary": s, "variant": v, "title": info.get("title", f"{p} × {s} ({v})")}
            for (p, s), vs in _PRESET_VARIANTS.items() for v, info in vs.items()]
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
    v = valid_variant(p, s, request.args.get("variant"))
    return render_template("rpo3d.html", default_primary=p, default_secondary=s, default_variant=v or "")


@bp.get("/api/rpo/<int:primary>/<int:secondary>")
def rpo_data(primary: int, secondary: int):
    try:
        return jsonify(get_rpo_scene(primary, secondary, variant=request.args.get("variant")))
    except (ValueError, RuntimeError) as e:
        logger.warning("RPO 場景計算失敗 %s×%s：%s", primary, secondary, e)
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # noqa: BLE001
        logger.exception("RPO 場景未預期錯誤 %s×%s", primary, secondary)
        return jsonify({"error": str(e)}), 500


@bp.get("/api/rpo/<int:primary>/<int:secondary>/report.png")
def rpo_report_png(primary: int, secondary: int):
    """三面板 PNG 報告：星座分布／高度歷史／距離（觀測 vs 彈道預測）。每次即時以最新 TLE 計算。"""
    from ..physics.rpo_report import build_rpo_report   # 延遲匯入：matplotlib 僅此路由需要
    try:
        lookback = min(max(float(request.args.get("lookback", 45)), 1.0), 365.0)
        forecast = min(max(float(request.args.get("forecast", 10)), 0.0), 30.0)
        theme = "dark" if request.args.get("theme") == "dark" else "light"
        png = build_rpo_report(primary, secondary, lang=request.args.get("lang", "zh"), theme=theme,
                               lookback_days=lookback, forecast_days=forecast,
                               variant=request.args.get("variant"))
    except (ValueError, RuntimeError) as e:
        logger.warning("RPO 報告產生失敗 %s×%s：%s", primary, secondary, e)
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # noqa: BLE001
        logger.exception("RPO 報告未預期錯誤 %s×%s", primary, secondary)
        return jsonify({"error": str(e)}), 500
    as_dl = request.args.get("download") in ("1", "true")
    return send_file(io.BytesIO(png), mimetype="image/png", as_attachment=as_dl,
                     download_name=f"rpo_report_{primary}_{secondary}_{theme}.png")
