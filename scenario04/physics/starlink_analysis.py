"""Starlink 服務能力分析：可見性時間序列、RTT 估算、天空密度、覆蓋空窗、遮蔽模擬。

計算邏輯使用與 coverage.py 相同的向量化 SGP4 引擎，並針對任意觀測點
（lat/lon）計算 Starlink 星座對該觀測點的幾何可用性。

快取架構：
  _main_cache[key] = (result_dict, computed_at)   — 主要 JSON 結果
  _occ_cache[key]  = (occupancy, n_az, n_el, n_steps, el_min)
                     occupancy shape: (n_az_bins, n_el_bins, n_steps) int16
                     供遮蔽模擬即時計算用，無需重新跑 SGP4
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sgp4.api import Satrec, jday

from ..config import settings
from ..ingestion.index import get_sat_index
from .coords import observer_ecef
from .propagate import HAS_SATREC_ARRAY

if HAS_SATREC_ARRAY:
    from sgp4.api import SatrecArray as _SatrecArray

logger = logging.getLogger(__name__)

_CACHE_TTL_SEC = 600

_main_cache: dict[tuple, tuple[dict, datetime]] = {}
_occ_cache:  dict[tuple, tuple]                 = {}
_cache_lock  = threading.Lock()

_computing:      set[tuple]        = set()
_computing_lock  = threading.Lock()

C_KM_MS = 299792.458 / 1000.0   # km / ms（光速換算）


def _cache_key(lat: float, lon: float, mask_deg: float, hours: int, step_min: int) -> tuple:
    return (round(lat, 3), round(lon, 3), round(mask_deg, 1), hours, step_min)


# ── 公開 API ──────────────────────────────────────────────────────────────────

def compute_starlink_visibility(
    lat:      float = settings.TAIPEI_LAT,
    lon:      float = settings.TAIPEI_LON,
    mask_deg: float = 25.0,
    hours:    int   = 24,
    step_min: int   = 15,
) -> tuple[dict[str, Any] | None, bool]:
    """
    非同步計算並快取結果。
    Returns (result, ready)；ready=False 代表背景計算中，呼叫方回 202 即可。
    """
    key = _cache_key(lat, lon, mask_deg, hours, step_min)

    with _cache_lock:
        if key in _main_cache:
            result, ts = _main_cache[key]
            if (datetime.now(timezone.utc) - ts).total_seconds() < _CACHE_TTL_SEC:
                return result, True

    with _computing_lock:
        if key in _computing:
            return None, False
        _computing.add(key)

    def _run() -> None:
        try:
            result, occ_data = _do_compute(lat, lon, mask_deg, hours, step_min)
            now = datetime.now(timezone.utc)
            with _cache_lock:
                _main_cache[key] = (result, now)
                _occ_cache[key]  = occ_data
        except Exception as exc:
            logger.error("Starlink 可見性計算失敗: %s", exc, exc_info=True)
            err: dict[str, Any] = {"error": str(exc), "timeline": [], "stats": {}}
            with _cache_lock:
                _main_cache[key] = (err, datetime.now(timezone.utc))
        finally:
            with _computing_lock:
                _computing.discard(key)

    threading.Thread(target=_run, daemon=True).start()
    return None, False


def compute_obstruction_analysis(
    lat:           float,
    lon:           float,
    mask_deg:      float,
    hours:         int,
    step_min:      int,
    blocked_cells: list[list[float]],
) -> tuple[dict[str, Any] | None, bool]:
    """
    套用使用者定義的水平線遮蔽（horizon mask）後，回傳修正後的可見性時間序列。
    blocked_cells: [[az_center, el_center], ...] 每格 10°×5°
    不需重跑 SGP4，直接由 _occ_cache 計算差值（O(T×B)，毫秒級）。
    """
    key = _cache_key(lat, lon, mask_deg, hours, step_min)

    with _cache_lock:
        if key not in _main_cache or key not in _occ_cache:
            return None, False
        main_result, ts = _main_cache[key]
        if (datetime.now(timezone.utc) - ts).total_seconds() >= _CACHE_TTL_SEC:
            return None, False
        occ, n_az, n_el, n_steps, el_min_f = _occ_cache[key]

    # 每個時刻被遮蔽的顆數
    obstructed = np.zeros(n_steps, dtype=np.int32)
    for az_c, el_c in blocked_cells:
        az_i = int(float(az_c) / 10.0) % n_az
        el_i = int((float(el_c) - el_min_f) / 5.0)
        if 0 <= el_i < n_el:
            obstructed += occ[az_i, el_i, :].astype(np.int32)

    orig_visible = np.array([e["visible"] for e in main_result["timeline"]], dtype=np.int32)
    new_visible  = np.maximum(orig_visible - obstructed, 0)

    gap_threshold = 1
    orig_ts = [e["ts"] for e in main_result["timeline"]]
    orig_rtt = [e.get("rtt_floor_ms") for e in main_result["timeline"]]

    new_timeline = [
        {
            "ts":           orig_ts[j],
            "visible":      int(new_visible[j]),
            "available":    bool(int(new_visible[j]) >= gap_threshold),
            "rtt_floor_ms": orig_rtt[j],
        }
        for j in range(n_steps)
    ]

    # 空窗偵測
    new_gaps: list[dict] = []
    gap_start: int | None = None
    for j, entry in enumerate(new_timeline):
        if not entry["available"] and gap_start is None:
            gap_start = j
        elif entry["available"] and gap_start is not None:
            dur = (j - gap_start) * step_min
            new_gaps.append({"start": orig_ts[gap_start], "end": entry["ts"], "duration_min": dur})
            gap_start = None
    if gap_start is not None:
        dur = (n_steps - 1 - gap_start) * step_min
        new_gaps.append({"start": orig_ts[gap_start], "end": orig_ts[-1], "duration_min": dur})

    nv = new_visible.astype(float)
    avail = int((nv >= gap_threshold).sum())
    new_pct = round(100.0 * avail / n_steps, 2)

    return {
        "timeline":      new_timeline,
        "gaps":          new_gaps,
        "blocked_count": len(blocked_cells),
        "stats": {
            "mean_visible":     round(float(nv.mean()), 1),
            "min_visible":      int(nv.min()),
            "max_visible":      int(nv.max()),
            "gap_count":        len(new_gaps),
            "gap_total_min":    sum(g["duration_min"] for g in new_gaps),
            "availability_pct": new_pct,
            "delta_pct":        round(new_pct - main_result["stats"]["availability_pct"], 2),
        },
        "original_stats": main_result["stats"],
        "mask_deg":  mask_deg,
        "step_min":  step_min,
    }, True


# ── 核心計算 ──────────────────────────────────────────────────────────────────

def _do_compute(
    lat:      float,
    lon:      float,
    mask_deg: float,
    hours:    int,
    step_min: int,
) -> tuple[dict[str, Any], tuple]:
    """
    Returns (result_dict, occ_tuple).
    occ_tuple = (occupancy, n_az_bins, n_el_bins, n_steps, el_min_f)
    """
    idx = get_sat_index()
    starlink_nids = [
        nid for nid, info in idx.items()
        if "STARLINK" in info["name"].upper()
    ]

    empty_occ = (np.zeros((36, 13, 1), dtype=np.int16), 36, 13, 1, float(mask_deg))
    if not starlink_nids:
        return {"error": "資料庫中無 STARLINK 衛星", "timeline": [], "stats": {}}, empty_occ
    if not HAS_SATREC_ARRAY:
        return {"error": "SatrecArray 不可用", "timeline": [], "stats": {}}, empty_occ

    logger.info(
        "Starlink 計算開始：(%.3f,%.3f) mask=%.1f° %dh@%dmin N=%d",
        lat, lon, mask_deg, hours, step_min, len(starlink_nids),
    )

    t0 = datetime.now(timezone.utc)
    obs = observer_ecef(lat, lon, 0.01)
    n_steps = int(hours * 60 / step_min) + 1
    times = [t0 + timedelta(minutes=i * step_min) for i in range(n_steps)]

    jd_fr = np.array([
        jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond * 1e-6)
        for t in times
    ])
    jds = np.ascontiguousarray(jd_fr[:, 0])
    frs = np.ascontiguousarray(jd_fr[:, 1])

    T_cent  = ((jds - 2451545.0) + frs) / 36525.0
    gmst_all = np.deg2rad(
        (280.46061837 + 360.98564736629 * (jds - 2451545.0 + frs)
         + 0.000387933 * T_cent ** 2) % 360.0)
    cg_all = np.cos(gmst_all)
    sg_all = np.sin(gmst_all)

    line1s = [idx[n]["line1"] for n in starlink_nids]
    line2s = [idx[n]["line2"] for n in starlink_nids]

    try:
        sa = _SatrecArray([Satrec.twoline2rv(l1, l2) for l1, l2 in zip(line1s, line2s)])
        e_raw, r_raw, _ = sa.sgp4(jds, frs)
    except Exception as exc:
        logger.error("SGP4 傳播失敗: %s", exc)
        return {"error": f"SGP4 失敗: {exc}", "timeline": [], "stats": {}}, empty_occ

    # ── 向量化 ECI → ENU（az/el/range）─────────────────────────────────────
    sl = obs["sl"]; cl = obs["cl"]; so = obs["so"]; co = obs["co"]
    x0 = obs["x0"]; y0 = obs["y0"]; z0 = obs["z0"]

    cg = cg_all[np.newaxis, :]
    sg = sg_all[np.newaxis, :]

    rx = r_raw[:, :, 0]; ry = r_raw[:, :, 1]; rz = r_raw[:, :, 2]
    del r_raw
    xe = cg * rx + sg * ry
    ye = -sg * rx + cg * ry
    ze = rz
    del rx, ry, rz
    dx = xe - x0; dy = ye - y0; dz = ze - z0
    del xe, ye, ze

    U   =  cl * co * dx + cl * so * dy + sl * dz
    E_  = -so * dx + co * dy
    N_  = -sl * co * dx - sl * so * dy + cl * dz
    del dx, dy, dz

    rng  = np.sqrt(E_ ** 2 + N_ ** 2 + U ** 2)
    safe = np.where(rng > 0.001, rng, 0.001)

    ok = (e_raw == 0)
    el = np.where(ok, np.rad2deg(np.arcsin(np.clip(U / safe, -1.0, 1.0))), -90.0)
    az = np.where(ok, np.rad2deg(np.arctan2(E_, N_)) % 360.0, 0.0)
    del U, E_, N_, safe

    visible        = (el >= mask_deg) & ok
    visible_counts = visible.sum(axis=0)

    # ── RTT 傳播延遲估算 ─────────────────────────────────────────────────────
    rng_inf   = np.where(visible, rng, np.inf)
    min_rng_t = rng_inf.min(axis=0)
    del rng_inf
    min_rng_t = np.where(np.isinf(min_rng_t), np.nan, min_rng_t)
    rtt_floor = np.where(np.isnan(min_rng_t), np.nan, 2.0 * min_rng_t / C_KM_MS)
    del rng

    # ── 遮蔽模擬：可視性佔用矩陣 ─────────────────────────────────────────────
    n_az  = 36
    el_min_f = float(mask_deg)
    n_el  = max(1, int((90.0 - el_min_f) / 5.0))

    az_bin_mat = (az / 10.0).astype(np.int32).clip(0, n_az - 1)
    el_bin_mat = ((el - el_min_f) / 5.0).astype(np.int32).clip(0, n_el - 1)

    occupancy = np.zeros((n_az, n_el, n_steps), dtype=np.int16)
    for t in range(n_steps):
        vis_t = visible[:, t]
        if vis_t.any():
            np.add.at(occupancy[:, :, t], (az_bin_mat[vis_t, t], el_bin_mat[vis_t, t]), 1)
    del az_bin_mat, el_bin_mat

    # ── 天空密度直方圖 ────────────────────────────────────────────────────────
    az_flat = az[visible]
    el_flat = el[visible]
    del el, az, visible

    az_bins = np.arange(0, 361, 10)
    el_bins = np.arange(el_min_f, 91, 5)
    sky_density: list[list[float]] = []
    if az_flat.size > 0 and len(el_bins) >= 2:
        hist, _, _ = np.histogram2d(az_flat, el_flat, bins=[az_bins, el_bins])
        mx = float(hist.max()) if hist.max() > 0 else 1.0
        for i in range(len(az_bins) - 1):
            for j in range(len(el_bins) - 1):
                if hist[i, j] > 0:
                    sky_density.append([
                        round(float((az_bins[i] + az_bins[i + 1]) / 2), 1),
                        round(float((el_bins[j] + el_bins[j + 1]) / 2), 1),
                        round(100.0 * float(hist[i, j]) / mx, 1),
                    ])

    # ── 時間序列 ──────────────────────────────────────────────────────────────
    gap_threshold = 1
    timeline: list[dict] = [
        {
            "ts":           times[j].isoformat(),
            "visible":      int(visible_counts[j]),
            "available":    bool(int(visible_counts[j]) >= gap_threshold),
            "rtt_floor_ms": None if np.isnan(rtt_floor[j]) else round(float(rtt_floor[j]), 2),
        }
        for j in range(n_steps)
    ]

    # ── 空窗偵測 ──────────────────────────────────────────────────────────────
    gaps: list[dict] = []
    gap_start: int | None = None
    for j, entry in enumerate(timeline):
        if not entry["available"] and gap_start is None:
            gap_start = j
        elif entry["available"] and gap_start is not None:
            dur = (j - gap_start) * step_min
            gaps.append({"start": timeline[gap_start]["ts"], "end": entry["ts"], "duration_min": dur})
            gap_start = None
    if gap_start is not None:
        dur = (n_steps - 1 - gap_start) * step_min
        gaps.append({"start": timeline[gap_start]["ts"], "end": timeline[-1]["ts"], "duration_min": dur})

    # ── 統計摘要 ──────────────────────────────────────────────────────────────
    vc   = visible_counts.astype(float)
    avail_steps = int((vc >= gap_threshold).sum())
    valid_rtt = rtt_floor[~np.isnan(rtt_floor)]

    result: dict[str, Any] = {
        "observer":    {"lat": lat, "lon": lon, "mask_deg": mask_deg},
        "timeline":    timeline,
        "gaps":        gaps,
        "sky_density": sky_density,
        "stats": {
            "total_sats":        len(starlink_nids),
            "mean_visible":      round(float(vc.mean()), 1),
            "min_visible":       int(vc.min()),
            "max_visible":       int(vc.max()),
            "gap_count":         len(gaps),
            "gap_total_min":     sum(g["duration_min"] for g in gaps),
            "availability_pct":  round(100.0 * avail_steps / n_steps, 2),
            "gap_threshold":     gap_threshold,
            "mean_rtt_floor_ms": round(float(valid_rtt.mean()), 2) if valid_rtt.size > 0 else None,
            "min_rtt_floor_ms":  round(float(valid_rtt.min()),  2) if valid_rtt.size > 0 else None,
            "max_rtt_floor_ms":  round(float(valid_rtt.max()),  2) if valid_rtt.size > 0 else None,
            "rtt_note": "純幾何傳播延遲；台灣無本地閘道時實際 RTT 估加 30~50ms",
        },
        "computed_at": t0.isoformat(),
        "hours":       hours,
        "step_min":    step_min,
        "mask_deg":    mask_deg,
    }

    occ_data = (occupancy, n_az, n_el, n_steps, el_min_f)
    logger.info("Starlink 計算完成：%.2f%% 可用，%d 次空窗", 100.0 * avail_steps / n_steps, len(gaps))
    return result, occ_data
