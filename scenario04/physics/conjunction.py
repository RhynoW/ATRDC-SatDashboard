"""接近事件掃描與碰撞機率（Phase 1.2）：KD-tree 配對 + Chan (2008) Pc。"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from .. import cache
from ..config import settings
from ..ingestion.index import get_sat_index
from .coords import eci_to_llh_batch
from .propagate import HAS_SATREC_ARRAY, sgp4_propagate_raw

logger = logging.getLogger(__name__)

try:
    from scipy.spatial import cKDTree as _cKDTree
    HAS_KDTREE = True
except ImportError:
    _cKDTree = None  # type: ignore[assignment]
    HAS_KDTREE = False


def compute_pc_chan(
    miss_km: float,
    sigma_r: float = settings.SIGMA_R_KM,
    sigma_t: float = settings.SIGMA_T_KM,
    r_sat:   float = settings.SAT_RADIUS_KM,
) -> float:
    """Chan (2008) 2-D 碰撞機率近似（假設最接近點平面法）。"""
    sigma_sq = sigma_r ** 2 + sigma_t ** 2
    if sigma_sq <= 0:
        return 0.0
    try:
        x = (r_sat ** 2) / (2.0 * sigma_sq)
        pc_base = 1.0 - math.exp(-x) if x < 50 else 1.0
        miss_factor = math.exp(-0.5 * (miss_km ** 2) / sigma_sq)
        return round(pc_base * miss_factor, 8)
    except Exception:
        return 0.0


def build_conjunction_summary(
    threshold_km: float = settings.CONJ_THRESHOLD_KM,
    max_pairs:    int   = 200,
) -> dict[str, Any]:
    if not HAS_KDTREE:
        return {"error": "scipy 未安裝，無法執行接近事件掃描",
                "count": 0, "pairs": [], "threshold_km": threshold_km}

    idx = get_sat_index()
    if len(idx) < 2:
        return {"count": 0, "pairs": [], "threshold_km": threshold_km, "total_scanned": 0}

    all_nids = list(idx.keys())
    line1s   = [idx[n]["line1"] for n in all_nids]
    line2s   = [idx[n]["line2"] for n in all_nids]
    t = datetime.now(timezone.utc)

    t0 = time.monotonic()
    err_arr, r_arr = sgp4_propagate_raw(all_nids, line1s, line2s, t)
    t_sgp4 = time.monotonic() - t0

    ok      = (err_arr == 0)
    ok_nids = [all_nids[i] for i in range(len(all_nids)) if ok[i]]
    ok_r    = r_arr[ok]

    if len(ok_nids) < 2:
        return {"count": 0, "pairs": [], "threshold_km": threshold_km,
                "total_scanned": len(ok_nids)}

    ok_llh = eci_to_llh_batch(ok_r, t)
    alt_ok = (ok_llh[:, 2] > -500.0) & (ok_llh[:, 2] < 80_000.0)
    filt_nids = [ok_nids[i] for i in range(len(ok_nids)) if alt_ok[i]]
    filt_r    = ok_r[alt_ok]
    filt_llh  = ok_llh[alt_ok]

    if len(filt_nids) < 2:
        return {"count": 0, "pairs": [], "threshold_km": threshold_km,
                "total_scanned": len(filt_nids)}

    t1        = time.monotonic()
    tree      = _cKDTree(filt_r)
    pairs_set = tree.query_pairs(threshold_km)
    t_kd      = time.monotonic() - t1

    logger.info(
        "接近事件掃描: %d 有效衛星，閾值 %.0f km，配對 %d，SGP4 %.2f s，KD %.2f s",
        len(filt_nids), threshold_km, len(pairs_set), t_sgp4, t_kd,
    )

    pairs: list[dict[str, Any]] = []
    for i, j in pairs_set:
        miss = float(np.linalg.norm(filt_r[i] - filt_r[j]))
        nid_a, nid_b = filt_nids[i], filt_nids[j]
        pc = compute_pc_chan(miss)
        pairs.append({
            "primary_norad":    nid_a,
            "primary_name":     idx[nid_a]["name"],
            "primary_purpose":  idx[nid_a]["purpose"],
            "primary_lat":      round(float(filt_llh[i, 0]), 4),
            "primary_lon":      round(float(filt_llh[i, 1]), 4),
            "primary_alt_km":   round(float(filt_llh[i, 2]), 1),
            "secondary_norad":  nid_b,
            "secondary_name":   idx[nid_b]["name"],
            "secondary_purpose":idx[nid_b]["purpose"],
            "secondary_lat":    round(float(filt_llh[j, 0]), 4),
            "secondary_lon":    round(float(filt_llh[j, 1]), 4),
            "secondary_alt_km": round(float(filt_llh[j, 2]), 1),
            "miss_km":          round(miss, 3),
            "Pc":               pc,
            "Pc_str":           f"{pc:.2e}",
            "risk_level":       "RED" if pc > 1e-4 else "AMBER" if pc > 1e-6 else "GREEN",
        })

    pairs.sort(key=lambda x: x["miss_km"])
    if len(pairs) > max_pairs:
        pairs = pairs[:max_pairs]

    elapsed = time.monotonic() - t0
    return {
        "count":         len(pairs),
        "threshold_km":  threshold_km,
        "max_pairs":     max_pairs,
        "pairs":         pairs,
        "total_scanned": len(filt_nids),
        "elapsed_sec":   round(elapsed, 2),
        "vectorized":    HAS_SATREC_ARRAY,
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    }


# ── TTL 快取（threshold 改變時強制重算）──────────────────────────────────────
_conj_cache:     dict[str, Any] | None = None
_conj_loaded_at: float = 0.0


def get_conjunctions(
    threshold_km: float = settings.CONJ_THRESHOLD_KM,
    max_pairs:    int   = 200,
) -> dict[str, Any]:
    global _conj_cache, _conj_loaded_at
    cache_key = f"conj:{threshold_km:.1f}"
    cached = cache.cache_get(cache_key)
    if cached:
        return cached
    if (_conj_cache is None
            or (time.monotonic() - _conj_loaded_at) > settings.CONJ_TTL
            or _conj_cache.get("threshold_km") != threshold_km):
        _conj_cache     = build_conjunction_summary(threshold_km, max_pairs)
        _conj_loaded_at = time.monotonic()
    cache.cache_set(cache_key, _conj_cache, ttl=settings.CONJ_TTL)
    return _conj_cache
