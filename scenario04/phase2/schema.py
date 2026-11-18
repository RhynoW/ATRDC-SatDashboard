"""場景驗證：結構（JSON Schema）＋語意檢查。回傳穩定的錯誤代碼，Python 與 Rust 以代碼集合比對。

語意錯誤代碼：
  E_SCHEMA                        結構不符（含缺欄位、型別、列舉、多餘欄位）
  E_ROLE_DUPLICATE                兩個物體的 role 相同
  E_COV_SIGMA_MISSING             demo 模式缺 sigma_km
  E_COV_SIGMA_NONPOSITIVE         sigma_km 有非正值
  E_COV_EPOCH_MISSING             propagated 模式缺 epoch
  E_COV_FRAME_MISSING             propagated 模式缺 frame
  E_COV_MATRIX_MISSING            propagated 模式缺 matrix_6x6
  E_COV_UNITS_MISSING             propagated 模式缺 units（pos、vel、cross 皆須給）
  E_COV_VELOCITY_CONVENTION_MISSING  frame=RTN 的 6×6 協方差缺 velocity_convention
  E_COV_NOT_SYMMETRIC             |P − Pᵀ| > 1e-12 · max|P|
  E_COV_NOT_POSITIVE_DEFINITE     Cholesky 失敗
  E_COV_DEPENDENCE_UNSUPPORTED    independent_of_other=false（Sprint 0 尚不支援交叉協方差）
  E_WINDOW_ORDER                  maneuver_search.window_rel_tca_s 須 a < b ≤ 0
  E_NOTICE_WINDOW_CONFLICT        analysis_time + min_notice_s > tca + window_rel_tca_s[1]
  E_DV_RANGE_ORDER                dv_ms 須 lo ≤ hi
  E_DRAG_PARAMS_MISSING           drag=true 但物體缺 area_to_mass_m2kg 或 cd
  E_TIME_PARSE                    時刻字串無法解析或無時區
  E_PROPAGATOR_SGP4_NEEDS_TLE     screening.propagator=sgp4 但有物體以狀態向量給定
  E_COV_EPOCH_MISMATCH            傳播模式協方差曆元須等於該物體星曆曆元（狀態向量 epoch；TLE 物體為 screening.start）
  E_MIN_STEP_GT_COARSE            screening.min_step_s 大於 coarse_step_s
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .timescale import UtcTime

SCHEMA_PATH = Path(__file__).resolve().parent / "scenario_v1.schema.json"
_SCHEMA = None


def _schema():
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _SCHEMA


def _seconds_between(a: str, b: str) -> float:
    ta, tb = UtcTime.parse(a), UtcTime.parse(b)
    return (tb.mjd - ta.mjd) * 86400.0 + (tb.sec - ta.sec)


def validate(sc: dict) -> dict:
    """回傳 {"ok": bool, "codes": [...排序後代碼], "messages": [...]}。"""
    import jsonschema

    codes: set[str] = set()
    msgs: list[str] = []
    v = jsonschema.Draft202012Validator(_schema())
    errs = sorted(v.iter_errors(sc), key=lambda e: list(e.path))
    if errs:
        codes.add("E_SCHEMA")
        msgs += [f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errs[:20]]
        return {"ok": False, "codes": sorted(codes), "messages": msgs}

    # 時刻
    times = [sc["screening"]["start"]]
    if "analysis_time" in sc:
        times.append(sc["analysis_time"])
    if "conjunction" in sc and "tca" in sc["conjunction"]:
        times.append(sc["conjunction"]["tca"])
    for o in sc["objects"]:
        if o["ephemeris"]["type"] == "state":
            times.append(o["ephemeris"]["epoch"])
        cov = o.get("covariance") or {}
        if "epoch" in cov:
            times.append(cov["epoch"])
    for t in times:
        try:
            UtcTime.parse(t)
        except Exception:  # noqa: BLE001
            codes.add("E_TIME_PARSE")
            msgs.append(f"無法解析時刻：{t}")
    if "E_TIME_PARSE" in codes:
        return {"ok": False, "codes": sorted(codes), "messages": msgs}

    roles = [o["role"] for o in sc["objects"]]
    if len(set(roles)) != len(roles):
        codes.add("E_ROLE_DUPLICATE")
    scr = sc["screening"]
    if scr.get("propagator") == "sgp4" and any(o["ephemeris"]["type"] != "TLE" for o in sc["objects"]):
        codes.add("E_PROPAGATOR_SGP4_NEEDS_TLE")
    if "min_step_s" in scr and scr["min_step_s"] > scr.get("coarse_step_s", 60.0):
        codes.add("E_MIN_STEP_GT_COARSE")
    for o in sc["objects"]:
        cov = o.get("covariance") or {}
        if cov.get("mode") == "propagated" and "epoch" in cov:
            ref = o["ephemeris"]["epoch"] if o["ephemeris"]["type"] == "state" else scr["start"]
            if abs(_seconds_between(ref, cov["epoch"])) > 1e-6:
                codes.add("E_COV_EPOCH_MISMATCH")

    for o in sc["objects"]:
        cov = o.get("covariance")
        if sc["force_model"]["drag"] and ("area_to_mass_m2kg" not in o or "cd" not in o):
            codes.add("E_DRAG_PARAMS_MISSING")
        if not cov:
            continue
        if not cov["independent_of_other"]:
            codes.add("E_COV_DEPENDENCE_UNSUPPORTED")
        if cov["mode"] == "demo":
            if "sigma_km" not in cov:
                codes.add("E_COV_SIGMA_MISSING")
            elif any(not s > 0 for s in cov["sigma_km"]):
                codes.add("E_COV_SIGMA_NONPOSITIVE")
            continue
        if "epoch" not in cov:
            codes.add("E_COV_EPOCH_MISSING")
        if "frame" not in cov:
            codes.add("E_COV_FRAME_MISSING")
        u = cov.get("units") or {}
        if not all(k in u for k in ("pos", "vel", "cross")):
            codes.add("E_COV_UNITS_MISSING")
        if "matrix_6x6" not in cov:
            codes.add("E_COV_MATRIX_MISSING")
            continue
        if cov.get("frame") == "RTN" and "velocity_convention" not in cov:
            codes.add("E_COV_VELOCITY_CONVENTION_MISSING")
        P = np.array(cov["matrix_6x6"], float)
        scale = float(np.max(np.abs(P))) or 1.0
        if float(np.max(np.abs(P - P.T))) > 1e-12 * scale:
            codes.add("E_COV_NOT_SYMMETRIC")
        else:
            try:
                np.linalg.cholesky(P)
            except np.linalg.LinAlgError:
                codes.add("E_COV_NOT_POSITIVE_DEFINITE")

    ms = sc.get("maneuver_search")
    if ms:
        a, b = ms["window_rel_tca_s"]
        if not (a < b <= 0):
            codes.add("E_WINDOW_ORDER")
        if "dv_ms" in ms and ms["dv_ms"][0] > ms["dv_ms"][1]:
            codes.add("E_DV_RANGE_ORDER")
        tca = (sc.get("conjunction") or {}).get("tca")
        at = sc.get("analysis_time")
        prim = next((o for o in sc["objects"] if o["role"] == "primary"), None)
        notice = ((prim or {}).get("propulsion") or {}).get("min_notice_s")
        if tca and at and notice is not None and "E_WINDOW_ORDER" not in codes:
            # 最早可執行時刻 = analysis_time + notice；最晚有用時刻 = tca + window[1]
            if _seconds_between(at, tca) + b < notice:
                codes.add("E_NOTICE_WINDOW_CONFLICT")
                msgs.append("最短通知時間與機動窗衝突：沒有合法的機動時刻（infeasible_constraints）")
    return {"ok": not codes, "codes": sorted(codes), "messages": msgs}
