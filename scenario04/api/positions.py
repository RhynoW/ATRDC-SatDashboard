"""衛星位置/統計/搜尋 API：/api/stats、/api/positions、/api/search 等。"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
from flask import Blueprint, jsonify, request

from ..config import settings
from ..ingestion.db import get_db_info
from ..ingestion.index import get_sat_index, get_stats
from ..physics.coords import eci_to_llh_batch
from ..physics.propagator_cache import get_cache
from ..physics.propagate import (
    HAS_SATREC_ARRAY,
    propagate_arc,
    propagate_batch,
    propagate_now,
    sgp4_propagate_raw,
)
from . import json_response
from .colors import get_color

logger = logging.getLogger(__name__)

bp = Blueprint("positions", __name__)


@bp.get("/api/stats")
def api_stats():
    payload_only = request.args.get("payload_only", "0") == "1"
    return jsonify(get_stats(payload_only=payload_only))


@bp.get("/api/db_info")
def api_db_info():
    return json_response(get_db_info(), max_age=settings.DB_INFO_TTL, default=str)


@bp.get("/api/positions")
def api_positions():
    ftype = request.args.get("ftype", "").strip()
    fval  = request.args.get("fval",  "").strip()
    payload_only = request.args.get("payload_only", "0") == "1"
    VALID = {"country", "purpose", "era", "constellation"}
    if ftype not in VALID or not fval:
        return jsonify({"error": "ftype 必須為 country/purpose/era/constellation，且 fval 不可空白"}), 400

    EXCLUDE = {"碎片", "火箭體"} if payload_only else set()
    idx = get_sat_index()
    matched = [n for n, i in idx.items()
               if i.get(ftype) == fval and i.get("purpose") not in EXCLUDE]

    total = len(matched)
    t0    = time.monotonic()
    logger.info("向量化傳播 %d 顆（%s=%s）", total, ftype, fval)

    positions = propagate_batch(matched, idx)
    elapsed   = time.monotonic() - t0
    logger.info("傳播完成 %d 顆，耗時 %.2f s（%s）",
                total, elapsed, "SatrecArray" if HAS_SATREC_ARRAY else "sequential")

    color   = get_color(ftype, fval)
    results = []
    for nid, pos in zip(matched, positions):
        if pos is None:
            continue
        lat, lon, alt = pos
        info = idx[nid]
        results.append({
            "norad_id":      nid,
            "name":          info["name"],
            "country":       info["country"],
            "purpose":       info["purpose"],
            "era":           info["era"],
            "constellation": info["constellation"] or "—",
            "color":         color,
            "lat":           round(lat, 4),
            "lon":           round(lon, 4),
            "alt_km":        round(alt, 1),
        })

    return jsonify({
        "ftype":         ftype,
        "fval":          fval,
        "count":         len(results),
        "total_matched": total,
        "sampled":       False,
        "elapsed_sec":   round(elapsed, 3),
        "vectorized":    HAS_SATREC_ARRAY,
        "satellites":    results,
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    })


@bp.get("/api/positions/coords")
def api_positions_coords():
    """中頻更新端點：從背景快取讀取位置，不在請求路徑觸發 SGP4。
    快取過期（>120 s）或尚未就緒時自動退回即時傳播（fallback）。
    """
    ftype = request.args.get("ftype", "").strip()
    fval  = request.args.get("fval",  "").strip()
    payload_only = request.args.get("payload_only", "0") == "1"
    VALID = {"country", "purpose", "era", "constellation"}
    if ftype not in VALID or not fval:
        return jsonify({"error": "ftype 必須為 country/purpose/era/constellation，且 fval 不可空白"}), 400

    EXCLUDE = {"碎片", "火箭體"} if payload_only else set()
    idx     = get_sat_index()
    matched = [n for n, i in idx.items()
               if i.get(ftype) == fval and i.get("purpose") not in EXCLUDE]

    cache      = get_cache()
    from_cache = cache.ready and cache.age_seconds < 120
    if from_cache:
        snapshot = cache.get_snapshot(matched)
        sats = [{"norad_id": nid, **d} for nid, d in snapshot.items()]
    else:
        # 快取尚未就緒或過期：退回即時傳播
        positions = propagate_batch(matched, idx)
        sats = []
        for nid, pos in zip(matched, positions):
            if pos is None:
                continue
            lat, lon, alt = pos
            sats.append({"norad_id": nid, "lat": round(lat, 4),
                         "lon": round(lon, 4), "alt_km": round(alt, 1)})

    return jsonify({
        "count":      len(sats),
        "from_cache": from_cache,
        "cache_age":  round(cache.age_seconds, 1) if cache.ready else None,
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "satellites": sats,
    })


@bp.post("/api/positions/active")
def api_positions_active():
    """快速更新端點：對前端指定的少量可見衛星（≤100）即時計算 SGP4，供 1 Hz 輪詢。
    直接即時計算，不走快取（≤100 顆向量化 SGP4 < 5 ms，不影響伺服器負載）。
    """
    MAX_ACTIVE = 100
    data    = request.get_json(silent=True) or {}
    raw_ids = (data.get("norad_ids") or [])[:MAX_ACTIVE]
    norad_ids = [int(x) for x in raw_ids
                 if isinstance(x, int) or (isinstance(x, str) and x.isdigit())]
    if not norad_ids:
        return jsonify({"satellites": [], "count": 0}), 200

    idx   = get_sat_index()
    valid = [n for n in norad_ids if n in idx]
    if not valid:
        return jsonify({"satellites": [], "count": 0}), 200

    t0        = time.monotonic()
    positions = propagate_batch(valid, idx)
    elapsed   = round(time.monotonic() - t0, 4)

    sats = []
    for nid, pos in zip(valid, positions):
        if pos is None:
            continue
        lat, lon, alt = pos
        sats.append({"norad_id": nid, "lat": round(lat, 4),
                     "lon": round(lon, 4), "alt_km": round(alt, 1)})

    return jsonify({
        "count":       len(sats),
        "elapsed_sec": elapsed,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "satellites":  sats,
    })


def _v3_meme_position_fallback(norad_id: int) -> dict[str, Any] | None:
    """/api/position、/api/search 共用：Flight 14 V3 這批合成編號的 TLE 列可能
    被每日重建清空（見 v3_flight14_roster），這裡從目錄檔 + MEME 內插補一份
    最基本的顯示資料，讓搜尋、地球儀點選等既有功能不因此整個失效。"""
    if norad_id not in V3_FLIGHT14_NORAD_IDS:
        return None
    from ..ingestion.user_defined import load_user_catalogue
    from ..physics import v3_meme
    cat = load_user_catalogue().get(norad_id)
    if cat is None:
        return None
    result: dict[str, Any] = {
        "norad_id":      norad_id,
        "name":          cat.get("name_en") or f"NORAD {norad_id}",
        "country":       cat.get("country") or "—",
        "purpose":       cat.get("purpose") or "—",
        "era":           "< 1 年",
        "constellation": cat.get("constellation") or "—",
    }
    r_eci = v3_meme.meme_positions_eci([norad_id], datetime.now(timezone.utc)).get(norad_id)
    if r_eci is not None:
        lat, lon, alt = eci_to_llh_batch(r_eci[None, :], datetime.now(timezone.utc))[0]
        result["lat"]    = round(float(lat), 4)
        result["lon"]    = round(float(lon), 4)
        result["alt_km"] = round(float(alt), 1)
    return result


@bp.get("/api/position/<int:norad_id>")
def api_position_single(norad_id: int):
    idx = get_sat_index()
    info = idx.get(norad_id)
    if info is None:
        fallback = _v3_meme_position_fallback(norad_id)
        if fallback is not None:
            return jsonify(fallback)
        return jsonify({"error": f"NORAD {norad_id} 不在索引中"}), 404
    pos = propagate_now(info["line1"], info["line2"])
    result: dict[str, Any] = {
        "norad_id":      norad_id,
        "name":          info["name"],
        "country":       info["country"],
        "purpose":       info["purpose"],
        "era":           info["era"],
        "constellation": info["constellation"] or "—",
    }
    if pos:
        result["lat"]    = round(pos[0], 4)
        result["lon"]    = round(pos[1], 4)
        result["alt_km"] = round(pos[2], 1)
    return jsonify(result)


@bp.get("/api/sat_orbit")
def api_sat_orbit():
    """回傳單顆衛星 SGP4 外推軌道弧（預設 2h / 120 點）。"""
    try:
        norad_id = int(request.args.get("norad_id", 0))
    except ValueError:
        return jsonify({"error": "norad_id 必須為整數"}), 400
    hours = float(request.args.get("hours", "2"))
    pts   = min(int(request.args.get("pts", "120")), 720)
    idx   = get_sat_index()
    info  = idx.get(norad_id)
    if info is None:
        return jsonify({"error": f"NORAD {norad_id} 不在索引中"}), 404
    l1, l2 = info.get("line1", ""), info.get("line2", "")
    if not l1 or not l2:
        return jsonify({"error": f"NORAD {norad_id} 無 TLE 資料"}), 404
    positions = propagate_arc(l1, l2, hours=hours, pts=pts)
    return jsonify({
        "norad_id":  norad_id,
        "name":      info["name"],
        "hours":     hours,
        "pts":       len(positions),
        "positions": positions,
    })


@bp.get("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"results": [], "count": 0, "query": q})

    idx  = get_sat_index()
    q_up = q.upper()
    matches: list[dict[str, Any]] = []

    if q.isdigit():
        nid = int(q)
        if nid in idx:
            matches.append({"norad_id": nid, **idx[nid], "score": 0})

    for nid, info in idx.items():
        if q_up in info["name"].upper():
            if not any(m["norad_id"] == nid for m in matches):
                matches.append({"norad_id": nid, **info, "score": 1})
        if len(matches) >= 60:
            break

    matches.sort(key=lambda x: (x["score"], x["name"]))
    top = matches[:20]

    nids      = [m["norad_id"] for m in top]
    positions = propagate_batch(nids, idx)

    results = []
    for m, pos in zip(top, positions):
        r: dict[str, Any] = {
            "norad_id":      m["norad_id"],
            "name":          m["name"],
            "country":       m["country"],
            "purpose":       m["purpose"],
            "era":           m["era"],
            "constellation": m["constellation"] or "—",
        }
        if pos:
            r["lat"]    = round(pos[0], 4)
            r["lon"]    = round(pos[1], 4)
            r["alt_km"] = round(pos[2], 1)
        results.append(r)

    # Flight 14 V3：TLE 列可能被每日重建清空，不在上面的 idx 比對範圍內，
    # 另外用目錄檔比對名稱/編號補上（見 _v3_meme_position_fallback）。
    matched_ids = {r["norad_id"] for r in results}
    if q.isdigit():
        cand = _v3_meme_position_fallback(int(q))
        if cand and cand["norad_id"] not in matched_ids:
            results.append(cand)
    else:
        from ..ingestion.user_defined import load_user_catalogue
        for nid, cat in load_user_catalogue().items():
            if nid in V3_FLIGHT14_NORAD_IDS and nid not in matched_ids \
                    and q_up in (cat.get("name_en") or "").upper():
                cand = _v3_meme_position_fallback(nid)
                if cand:
                    results.append(cand)

    return jsonify({"results": results, "count": len(results), "query": q})


# Starship Flight 14 (2026-09-28, intl designator 2026-225A..AB) — synthetic NORAD
# block reserved by this project for TLEs fitted from SpaceX MEME precise ephemeris
# (starlink_ephemeris/meme_to_tle.py). Not official Space-Track numbers; kept out of
# starlink_census.py's official V3 count on purpose so that count stays accurate.
V3_FLIGHT14_NORAD_IDS = list(range(339974, 340000))
V3_FLIGHT14_INTDES = "2026-225"

_V3_CT_ELEMENTS_TTL_S = 4 * 3600     # CelesTrak 補充檔約每日 3 次更新
_v3_ct_elements_cache: tuple[float, dict[str, dict[str, Any]]] | None = None


def _fetch_celestrak_v3_elements() -> dict[str, dict[str, Any]]:
    """即時向 CelesTrak 補充檔（SpaceX 自行提供之完整軌道根數）查詢 Flight 14 V3
    這批衛星，依衛星名稱建索引，供與本系統自製 MEME 反算 TLE 做位置比對。
    快取 4 小時（CelesTrak 補充檔更新頻率約為每日 3 次）；失敗時回傳空字典（優雅降級）。
    """
    global _v3_ct_elements_cache
    now = time.monotonic()
    if _v3_ct_elements_cache and (now - _v3_ct_elements_cache[0]) < _V3_CT_ELEMENTS_TTL_S:
        return _v3_ct_elements_cache[1]
    import requests
    by_name: dict[str, dict[str, Any]] = {}
    try:
        r = requests.get(
            "https://celestrak.org/NORAD/elements/supplemental/sup-gp.php",
            params={"INTDES": V3_FLIGHT14_INTDES, "FORMAT": "json"},
            timeout=15, headers={"User-Agent": "SatDashboard/1.0"},
        )
        r.raise_for_status()
        by_name = {it["OBJECT_NAME"]: it for it in r.json() if it.get("OBJECT_NAME")}
    except Exception:
        logger.warning("CelesTrak V3 supplemental 擬合資料取得失敗", exc_info=True)
    _v3_ct_elements_cache = (now, by_name)
    return by_name


def _celestrak_elem_eci(elem: dict[str, Any], t: datetime) -> np.ndarray | None:
    """把 CelesTrak 補充檔一筆平均軌道根數用 SGP4 傳播到時刻 t，回傳 ECI/TEME 座標（km）。"""
    from sgp4.api import WGS72, Satrec
    from sgp4.api import jday as _jday
    try:
        ep = datetime.fromisoformat(elem["EPOCH"])
        if ep.tzinfo is None:
            ep = ep.replace(tzinfo=timezone.utc)
        jd_ep, fr_ep = _jday(ep.year, ep.month, ep.day, ep.hour, ep.minute,
                              ep.second + ep.microsecond / 1e6)
        xpdotp = 1440.0 / (2 * math.pi)
        sat = Satrec()
        sat.sgp4init(
            WGS72, "i", 90000,
            (jd_ep + fr_ep) - 2433281.5,
            float(elem["BSTAR"]), 0.0, 0.0,
            float(elem["ECCENTRICITY"]),
            math.radians(float(elem["ARG_OF_PERICENTER"])),
            math.radians(float(elem["INCLINATION"])),
            math.radians(float(elem["MEAN_ANOMALY"])),
            float(elem["MEAN_MOTION"]) / xpdotp,
            math.radians(float(elem["RA_OF_ASC_NODE"])),
        )
        jd, fr = _jday(t.year, t.month, t.day, t.hour, t.minute,
                        t.second + t.microsecond / 1e6)
        err, r_vec, _v = sat.sgp4(jd, fr)
        return None if err != 0 else np.array(r_vec)
    except Exception:
        logger.debug("CelesTrak 補充檔元素傳播失敗", exc_info=True)
        return None


@bp.get("/api/v3_flight14_roster")
def api_v3_flight14_roster():
    from ..ingestion.user_defined import load_user_catalogue
    from ..physics import v3_meme

    idx = get_sat_index()
    catalogue = load_user_catalogue()
    t = datetime.now(timezone.utc)

    # 名稱/星系等顯示資訊直接讀目錄檔，不依賴 TLE 索引：這批衛星的 TLE 列可能
    # 被每日重建清空（catalogue-only、無 TLE 的項目不會進 get_sat_index()），
    # 但目錄檔本身與 MEME 樣本都不受影響，兩者合起來就能完整顯示 26 顆。
    valid = [nid for nid in V3_FLIGHT14_NORAD_IDS if nid in catalogue]

    # 優先用 SpaceX 官方 MEME 72 小時預估樣本內插（見 v3_meme.py 模組說明：
    # 這批衛星已開始變軌，單次擬合的 SGP4 TLE 外推已不可靠）；涵蓋不到的
    # （資料過期、或該顆缺樣本）才退回 TLE 估測路徑（若 idx 裡剛好還有）。
    meme_eci = v3_meme.meme_positions_eci(valid, t)

    tle_valid = [nid for nid in valid if nid in idx]
    line1s = [idx[n]["line1"] for n in tle_valid]
    line2s = [idx[n]["line2"] for n in tle_valid]
    err_arr, r_arr = sgp4_propagate_raw(tle_valid, line1s, line2s, t) if tle_valid else (
        np.zeros(0, dtype=int), np.zeros((0, 3)))
    tle_eci = {nid: r_arr[i] for i, nid in enumerate(tle_valid) if err_arr[i] == 0}

    ct_elements = _fetch_celestrak_v3_elements()

    results = []
    for nid in valid:
        cat = catalogue[nid]
        r: dict[str, Any] = {
            "norad_id":      nid,
            "name":          cat.get("name_en") or f"NORAD {nid}",
            "constellation": cat.get("constellation") or "—",
        }

        r_eci: np.ndarray | None = None
        if nid in meme_eci:
            r_eci = meme_eci[nid]
            r["position_source"] = "meme"
        elif nid in tle_eci:
            r_eci = tle_eci[nid]
            r["position_source"] = "tle_estimate"

        if r_eci is not None:
            lat, lon, alt = eci_to_llh_batch(r_eci[None, :], t)[0]
            r["lat"]    = round(float(lat), 4)
            r["lon"]    = round(float(lon), 4)
            r["alt_km"] = round(float(alt), 1)

            ct_elem = ct_elements.get(r["name"])
            if ct_elem:
                r_ct = _celestrak_elem_eci(ct_elem, t)
                if r_ct is not None:
                    r["celestrak_epoch"]  = ct_elem.get("EPOCH")
                    r["compare_delta_km"] = round(float(np.linalg.norm(r_eci - r_ct)), 1)
        results.append(r)

    deltas = [r["compare_delta_km"] for r in results if "compare_delta_km" in r]
    meme_cov = v3_meme.coverage_info()

    return jsonify({
        "results": results,
        "count": len(results),
        "expected_count": len(V3_FLIGHT14_NORAD_IDS),
        "compared_count": len(deltas),
        "compare_delta_km_mean": round(sum(deltas) / len(deltas), 1) if deltas else None,
        "compare_delta_km_max":  round(max(deltas), 1) if deltas else None,
        "meme_coverage": meme_cov,
        "source": (
            "position_source=\"meme\"：Starlink 官方 MEME 72 小時預估（SpaceX 自行發布，"
            "直接內插，非本系統的軌道模型）。position_source=\"tle_estimate\"：MEME 樣本已"
            "過期時的退回路徑，SGP4 fitted from SpaceX MEME precise ephemeris — ESTIMATED，"
            "非官方 Space-Track/CelesTrak TLE。NORAD IDs 339974-339999 皆為本專案暫用的合成"
            "編號，尚未正式編目。"
        ),
        "compare_source": (
            "compare_delta_km：與 CelesTrak 補充檔（sup-gp.php，SpaceX 自行提供之獨立 SGP4 "
            "擬合，DATA_SOURCE=SpaceX-E）在同一時刻的 3D 位置差距（km），供交叉驗證；兩者皆"
            "非 Space-Track 官方編目 TLE，Space-Track 目前仍為 0 筆（尚未正式編目）。"
        ),
    })
