"""Conjunction & Maneuver Lab（第二階段 Sprint 1）：頁面與 API。

GET  /lab                 頁面
GET  /api/lab/demo        示範場景清單（含完整場景 JSON）
POST /api/lab/scenario    場景 JSON → 端到端報告
POST /api/lab/batch       主星＋次星清單（CSV 文字或陣列）→ 批次會合結果
POST /api/lab/batch.csv   {"events": [...]} → 結果 CSV（下載）
POST /api/lab/csv/parse   CSV 文字 → 解析結果與錯誤（前端預覽）
GET  /api/lab/maneuver/demo  避碰示範案例（maneuver_cases.json 套用 ops 後的完整場景）
POST /api/lab/maneuver    {"scenario": 場景, "screening_norad_ids": [...]} → 避碰決策報告（Sprint 2）
"""
from __future__ import annotations

import json
import logging

from flask import Blueprint, Response, render_template, request

from . import json_response
from ..ingestion.index import get_sat_index
from ..phase2 import e2e, maneuver
from ..phase2.batch import run_batch
from ..phase2.csvio import events_to_csv, parse_secondaries
from ..phase2.eop import TESTDATA, default_eop

bp = Blueprint("lab", __name__)
logger = logging.getLogger(__name__)

DEMOS = ("e2e_leo_synthetic.json", "e2e_leo_propagated.json", "e2e_tle_iss_css.json")
MAX_SECONDARIES = 200
MAX_SCREENING_OBJECTS = 200


def _lookup(norad: int):
    s = get_sat_index().get(int(norad))
    return (s["name"], s["line1"], s["line2"]) if s else None


@bp.get("/lab")
def lab_page():
    return render_template("lab.html")


@bp.get("/api/lab/demo")
def lab_demo():
    out = []
    for f in DEMOS:
        sc = json.loads((TESTDATA / f).read_text(encoding="utf-8"))
        out.append({"id": sc["id"], "description": sc.get("description", ""), "scenario": sc})
    return json_response({"scenarios": out}, max_age=3600)


def _float_default(o):
    return float(o)


@bp.post("/api/lab/scenario")
def lab_scenario():
    sc = request.get_json(silent=True)
    if not isinstance(sc, dict):
        return json_response({"status": "invalid_scenario",
                              "validation": {"ok": False, "codes": ["E_SCHEMA"], "messages": ["需要 JSON 物件"]}}), 400
    try:
        rep = e2e.run(sc, default_eop())
    except ValueError as ex:
        return json_response({"status": "error", "error": str(ex)}), 400
    return json_response(rep, default=_float_default)


@bp.post("/api/lab/csv/parse")
def lab_csv_parse():
    body = request.get_json(silent=True) or {}
    return json_response(parse_secondaries(str(body.get("text", ""))))


@bp.post("/api/lab/batch")
def lab_batch():
    body = request.get_json(silent=True) or {}
    parsed = None
    if body.get("secondaries_csv"):
        parsed = parse_secondaries(str(body["secondaries_csv"]))
        secs = parsed["rows"]
    else:
        secs = [s for s in (body.get("secondaries") or []) if isinstance(s, dict)]
    if not secs:
        return json_response({"status": "error", "error": "沒有可分析的次星", "csv": parsed}), 400
    if len(secs) > MAX_SECONDARIES:
        return json_response({"status": "error", "error": f"次星最多 {MAX_SECONDARIES} 顆"}), 400
    req = {k: body.get(k) for k in ("primary", "start", "duration_s", "threshold_km", "coarse_step_s", "defaults")}
    if not isinstance(req["primary"], dict) or not req["start"] or req["duration_s"] is None or req["threshold_km"] is None:
        return json_response({"status": "error", "error": "需要 primary、start、duration_s、threshold_km"}), 400
    req["secondaries"] = secs
    try:
        rep = run_batch(req, default_eop(), _lookup)
    except ValueError as ex:
        return json_response({"status": "error", "error": str(ex)}), 400
    if parsed is not None:
        rep["csv"] = {"errors": parsed["errors"], "n_rows": len(parsed["rows"])}
    return json_response(rep, default=_float_default), (200 if rep.get("status") == "ok" else 400)


@bp.post("/api/lab/batch.csv")
def lab_batch_csv():
    body = request.get_json(silent=True) or {}
    text = events_to_csv(body.get("events") or [])
    return Response("﻿" + text, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=conjunction_events.csv"})


def apply_ops(doc: dict, ops: list) -> dict:
    """schema_cases／maneuver_cases 的 ops：set 設值、del 刪除；path 為鍵或索引。"""
    out = json.loads(json.dumps(doc))
    for op in ops:
        tgt = out
        for k in op["path"][:-1]:
            tgt = tgt[k]
        if op["op"] == "set":
            tgt[op["path"][-1]] = op["value"]
        else:
            del tgt[op["path"][-1]]
    return out


@bp.get("/api/lab/maneuver/demo")
def lab_maneuver_demo():
    cases = json.loads((TESTDATA / "maneuver_cases.json").read_text(encoding="utf-8"))
    base = json.loads((TESTDATA / cases["base"]).read_text(encoding="utf-8"))
    out = [{"id": c["name"], "scenario": apply_ops(base, c["ops"])} for c in cases["cases"]]
    return json_response({"description": base.get("description", ""), "scenarios": out}, max_age=3600)


@bp.post("/api/lab/maneuver")
def lab_maneuver():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("scenario"), dict):
        return json_response({"status": "error", "error": "需要 {\"scenario\": 場景 JSON}"}), 400
    sc = json.loads(json.dumps(body["scenario"]))
    ids = body.get("screening_norad_ids") or []
    if not isinstance(ids, list):
        return json_response({"status": "error", "error": "screening_norad_ids 須為陣列"}), 400
    unresolved = []
    if ids:
        ms = sc.get("maneuver_search")
        if not isinstance(ms, dict):
            return json_response({"status": "error", "error": "場景缺 maneuver_search"}), 400
        ss = ms.setdefault("secondary_screening", {})
        if not isinstance(ss, dict) or not isinstance(ss.setdefault("objects", []), list):
            return json_response({"status": "error", "error": "secondary_screening 須為物件、objects 須為陣列"}), 400
        objs = ss["objects"]
        have = {o.get("norad_id") for o in objs if isinstance(o, dict)}
        for x in ids:
            try:
                nid = int(x)
            except (TypeError, ValueError):
                unresolved.append(x)
                continue
            if nid in have:
                continue
            hit = _lookup(nid)
            if hit is None:
                unresolved.append(nid)
                continue
            objs.append({"name": hit[0], "norad_id": nid,
                         "ephemeris": {"type": "TLE", "line1": hit[1], "line2": hit[2]}})
            have.add(nid)
        if len(objs) > MAX_SCREENING_OBJECTS:
            return json_response({"status": "error", "error": f"二次篩選物體最多 {MAX_SCREENING_OBJECTS} 顆"}), 400
    try:
        rep = maneuver.run(sc, default_eop())
    except ValueError as ex:
        return json_response({"status": "error", "error": str(ex)}), 400
    rep["screening_unresolved"] = unresolved
    return json_response(rep, default=_float_default)
