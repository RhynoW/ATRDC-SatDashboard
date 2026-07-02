"""台北覆蓋分析（Phase 1.2）：類別定義、即時覆蓋、過頂預報、時間軸版本。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sgp4.api import Satrec, jday

from ..config import settings
from ..ingestion.index import get_index_for_time, get_sat_index
from .coords import eci_to_elaz, eci_to_llh_batch, observer_ecef
from .propagate import HAS_SATREC_ARRAY, sgp4_propagate_raw

if HAS_SATREC_ARRAY:
    from sgp4.api import SatrecArray as _SatrecArray

logger = logging.getLogger(__name__)

# ── 過頂分析衛星類別（overpass_cats.yaml 熱重載）──────────────────────────────
_OVERPASS_CATS_DEFAULT: dict[str, dict] = {
    "US_EO": {
        "label":    "美國商用光學衛星",
        "sublabel": "Vantor/Maxar · Planet SkySat/Pelican",
        "color":    "#4488FF",
        "kw":       ["WORLDVIEW", "GEOEYE", "LEGION", "SKYSAT", "PELICAN"],
    },
    "CN_COMM": {
        "label":    "中國商用光學衛星",
        "sublabel": "SuperView · 高分 · 吉林",
        "color":    "#FF9800",
        "kw":       ["SUPERVIEW", "JILIN", "ZHUHAI", "GAOFEN"],
    },
    "CN_MIL": {
        "label":    "中國軍用偵察衛星",
        "sublabel": "遙感 Yaogan",
        "color":    "#F44336",
        "kw":       ["YAOGAN", "JIANBING"],
    },
    "TW_TASA": {
        "label":    "台灣 TASA 衛星",
        "sublabel": "Formosat-5 / -7 / -8",
        "color":    "#00E5FF",
        "kw":       [
            "FORMOSAT-5", "FORMOSAT 5", "FORMOSAT5",
            "FORMOSAT-7", "FORMOSAT 7", "FORMOSAT7",
            "FORMOSAT-8", "FORMOSAT 8", "FORMOSAT8",
            "COSMIC-2", "COSMIC2",
        ],
    },
}


def _load_overpass_cats() -> dict[str, dict]:
    """從 overpass_cats.yaml 載入類別定義；失敗時退回硬編碼預設。"""
    if settings.OVERPASS_CATS_FILE.exists():
        try:
            import yaml
            with settings.OVERPASS_CATS_FILE.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict) and data:
                logger.info("overpass_cats.yaml 載入成功: %d 類別", len(data))
                return data
        except Exception as exc:
            logger.warning("overpass_cats.yaml 載入失敗，使用預設: %s", exc)
    return _OVERPASS_CATS_DEFAULT


OVERPASS_CATS: dict[str, dict] = dict(_load_overpass_cats())


def reload_overpass_cats() -> list[str]:
    """熱重載類別定義（in-place 更新，既有引用不失效）；回傳類別 key 清單。"""
    new = _load_overpass_cats()
    OVERPASS_CATS.clear()
    OVERPASS_CATS.update(new)
    return list(OVERPASS_CATS.keys())


# 觀測站 ECEF（台北）預先計算
TAIPEI_OBS = observer_ecef(settings.TAIPEI_LAT, settings.TAIPEI_LON, settings.TAIPEI_H_KM)


def get_overpass_candidates(idx: dict) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {cat: [] for cat in OVERPASS_CATS}
    for nid, info in idx.items():
        name_up = info["name"].upper()
        for cat, cfg in OVERPASS_CATS.items():
            if any(kw in name_up for kw in cfg["kw"]):
                result[cat].append(nid)
                break
    return result


def compute_taipei_coverage(mask_deg: float = settings.MASK_DEG) -> dict[str, Any]:
    idx = get_sat_index()
    candidates = get_overpass_candidates(idx)
    t = datetime.now(timezone.utc)
    jd0, fr0 = jday(t.year, t.month, t.day, t.hour, t.minute,
                    t.second + t.microsecond * 1e-6)
    obs = TAIPEI_OBS

    all_nids = [nid for nids in candidates.values() for nid in nids]
    if not all_nids:
        return {
            "categories": {
                cat: {**cfg, "count": 0, "visible_count": 0, "satellites": []}
                for cat, cfg in OVERPASS_CATS.items()
            },
            "timestamp": t.isoformat(),
            "mask_deg":  mask_deg,
        }

    line1s = [idx[n]["line1"] for n in all_nids]
    line2s = [idx[n]["line2"] for n in all_nids]
    err_arr, r_arr = sgp4_propagate_raw(all_nids, line1s, line2s, t)

    valid = err_arr == 0
    el_all  = np.full(len(all_nids), -90.0)
    az_all  = np.zeros(len(all_nids))
    rng_all = np.zeros(len(all_nids))
    llh_all = np.full((len(all_nids), 3), np.nan)

    if valid.any():
        el_v, az_v, rng_v = eci_to_elaz(r_arr[valid], jd0, fr0, obs)
        el_all[valid]  = el_v
        az_all[valid]  = az_v
        rng_all[valid] = rng_v
        llh_all[valid] = eci_to_llh_batch(r_arr[valid], t)

    nid_to_i = {nid: i for i, nid in enumerate(all_nids)}
    result_cats: dict[str, Any] = {}

    for cat, cfg in OVERPASS_CATS.items():
        sats = []
        for nid in candidates[cat]:
            i = nid_to_i[nid]
            if err_arr[i] != 0 or np.isnan(llh_all[i, 0]):
                continue
            lat, lon, alt = llh_all[i, 0], llh_all[i, 1], llh_all[i, 2]
            if not (-500.0 < float(alt) < 80_000.0):
                continue
            el  = float(el_all[i])
            az  = float(az_all[i])
            rng = float(rng_all[i])
            sats.append({
                "norad_id":  nid,
                "name":      idx[nid]["name"],
                "lat":       round(float(lat), 4),
                "lon":       round(float(lon), 4),
                "alt_km":    round(float(alt), 1),
                "el_deg":    round(el, 2),
                "az_deg":    round(az, 2),
                "range_km":  round(rng, 1),
                "visible":   el >= mask_deg,
                "color":     cfg["color"],
            })
        result_cats[cat] = {
            "label":         cfg["label"],
            "sublabel":      cfg["sublabel"],
            "color":         cfg["color"],
            "count":         len(sats),
            "visible_count": sum(1 for s in sats if s["visible"]),
            "satellites":    sats,
        }

    return {"categories": result_cats, "timestamp": t.isoformat(), "mask_deg": mask_deg}


def predict_taipei_passes(
    hours:       float = 24.0,
    step_sec:    float = 60.0,
    mask_deg:    float = settings.MASK_DEG,
    max_per_cat: int   = 20,
) -> dict[str, Any]:
    idx = get_sat_index()
    candidates = get_overpass_candidates(idx)
    t0 = datetime.now(timezone.utc)
    obs = TAIPEI_OBS

    all_nids_flat = [nid for nids in candidates.values() for nid in nids]
    n_steps = int(hours * 3600 / step_sec)

    # 記憶體上限保護：估算矩陣大小，必要時自動縮減衛星數
    est_mb = len(all_nids_flat) * n_steps * 3 * 8 / 1024 ** 2
    if est_mb > settings.MAX_PASSES_MATRIX_MB:
        logger.warning(
            "過頂預報矩陣估計 %.0f MB > 上限 %d MB，自動縮減衛星數",
            est_mb, settings.MAX_PASSES_MATRIX_MB,
        )
        safe_n = int(settings.MAX_PASSES_MATRIX_MB * 1024 ** 2 / (n_steps * 3 * 8))
        # 按比例縮減各類別
        for cat in candidates:
            candidates[cat] = candidates[cat][:max(1, safe_n // len(candidates))]
    times   = [t0 + timedelta(seconds=i * step_sec) for i in range(n_steps)]

    # Build JD/FR arrays once
    jd_fr = np.array([
        jday(tt.year, tt.month, tt.day, tt.hour, tt.minute,
             tt.second + tt.microsecond * 1e-6)
        for tt in times
    ])
    jds = np.ascontiguousarray(jd_fr[:, 0])
    frs = np.ascontiguousarray(jd_fr[:, 1])

    # Pre-compute GMST for all time steps
    T_cent   = ((jds - 2451545.0) + frs) / 36525.0
    gmst_all = np.deg2rad(
        (280.46061837 + 360.98564736629 * (jds - 2451545.0 + frs)
         + 0.000387933 * T_cent ** 2) % 360.0)
    cg_all = np.cos(gmst_all)
    sg_all = np.sin(gmst_all)

    all_passes: dict[str, list] = {cat: [] for cat in OVERPASS_CATS}

    for cat, cfg in OVERPASS_CATS.items():
        nids = candidates[cat]
        if not nids:
            continue

        line1s = [idx[n]["line1"] for n in nids]
        line2s = [idx[n]["line2"] for n in nids]

        if not HAS_SATREC_ARRAY:
            logger.warning("SatrecArray 不可用，跳過過頂預報 cat=%s", cat)
            continue

        try:
            sa = _SatrecArray([Satrec.twoline2rv(l1, l2) for l1, l2 in zip(line1s, line2s)])
            e_raw, r_raw, _ = sa.sgp4(jds, frs)
            # e_raw: (N_sats, N_times)  r_raw: (N_sats, N_times, 3)
        except Exception as exc:
            logger.warning("過頂預報傳播失敗 cat=%s: %s", cat, exc)
            continue

        sl = obs["sl"]; cl = obs["cl"]; so = obs["so"]; co = obs["co"]
        x0 = obs["x0"]; y0 = obs["y0"]; z0 = obs["z0"]

        cat_passes: list[dict] = []

        for si, nid in enumerate(nids):
            r_ti = r_raw[si]   # (N_times, 3)
            e_ti = e_raw[si]   # (N_times,)
            ok   = (e_ti == 0)
            if not ok.any():
                continue

            # Vectorized ECI → ECEF → ENU → elevation
            xe = cg_all * r_ti[:, 0] + sg_all * r_ti[:, 1]
            ye = -sg_all * r_ti[:, 0] + cg_all * r_ti[:, 1]
            ze = r_ti[:, 2]
            dx = xe - x0; dy = ye - y0; dz = ze - z0
            U_enu = cl * co * dx + cl * so * dy + sl * dz
            E_enu = -so * dx + co * dy
            N_enu = -sl * co * dx - sl * so * dy + cl * dz
            rng   = np.sqrt(E_enu ** 2 + N_enu ** 2 + U_enu ** 2)
            safe  = np.where(rng > 0.001, rng, 0.001)
            el    = np.where(ok, np.rad2deg(np.arcsin(np.clip(U_enu / safe, -1.0, 1.0))), -90.0)

            above = el >= mask_deg
            if not above.any():
                continue

            transitions = np.diff(above.astype(int))
            rise_list   = list(np.where(transitions == 1)[0] + 1)
            set_list    = list(np.where(transitions == -1)[0] + 1)

            if above[0]:
                rise_list = [0] + rise_list
            if above[-1]:
                set_list  = set_list + [n_steps - 1]

            for ri, si_ in zip(rise_list, set_list):
                if ri >= si_:
                    continue
                seg_el     = el[ri: si_ + 1]
                mx_offset  = int(np.argmax(seg_el))
                max_el     = float(seg_el[mx_offset])
                mx_i       = ri + mx_offset
                t_rise     = times[ri]
                t_set      = times[min(si_, n_steps - 1)]
                t_max      = times[mx_i]
                duration_s = int((t_set - t_rise).total_seconds())
                cat_passes.append({
                    "norad_id":   nid,
                    "name":       idx[nid]["name"],
                    "t_rise_utc": t_rise.isoformat(),
                    "t_max_utc":  t_max.isoformat(),
                    "t_set_utc":  t_set.isoformat(),
                    "max_el_deg": round(max_el, 1),
                    "duration_s": duration_s,
                    "color":      cfg["color"],
                })

        cat_passes.sort(key=lambda x: x["t_rise_utc"])
        all_passes[cat] = cat_passes[:max_per_cat]

    result: dict[str, Any] = {}
    for cat, cfg in OVERPASS_CATS.items():
        result[cat] = {
            "label":    cfg["label"],
            "sublabel": cfg["sublabel"],
            "color":    cfg["color"],
            "passes":   all_passes[cat],
        }

    return {
        "categories": result,
        "hours":      hours,
        "mask_deg":   mask_deg,
        "timestamp":  t0.isoformat(),
    }


# ── 時間軸：指定時刻覆蓋 / 過頂 ───────────────────────────────────────────────

def compute_taipei_coverage_at(
    ts: datetime, mask_deg: float = settings.MASK_DEG,
) -> dict[str, Any]:
    """Coverage at an arbitrary time ts (past or future)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)

    idx = get_index_for_time(ts)
    candidates = get_overpass_candidates(idx)
    jd0, fr0 = jday(ts.year, ts.month, ts.day, ts.hour, ts.minute,
                    ts.second + ts.microsecond * 1e-6)
    obs = TAIPEI_OBS

    all_nids = [nid for nids in candidates.values() for nid in nids]
    empty_result: dict[str, Any] = {
        "categories": {
            cat: {"label": cfg["label"], "sublabel": cfg["sublabel"],
                  "color": cfg["color"], "count": 0, "visible_count": 0, "satellites": []}
            for cat, cfg in OVERPASS_CATS.items()
        },
        "timestamp":     ts.isoformat(),
        "mask_deg":      mask_deg,
        "is_historical": ts < now - timedelta(hours=1),
        "is_future":     ts > now + timedelta(hours=1),
    }
    if not all_nids:
        return empty_result

    line1s = [idx[n]["line1"] for n in all_nids]
    line2s = [idx[n]["line2"] for n in all_nids]
    err_arr, r_arr = sgp4_propagate_raw(all_nids, line1s, line2s, ts)

    valid = err_arr == 0
    el_all = np.full(len(all_nids), -90.0)
    az_all = np.zeros(len(all_nids))
    rng_all = np.zeros(len(all_nids))
    llh_all = np.full((len(all_nids), 3), np.nan)

    if valid.any():
        el_v, az_v, rng_v = eci_to_elaz(r_arr[valid], jd0, fr0, obs)
        el_all[valid]  = el_v
        az_all[valid]  = az_v
        rng_all[valid] = rng_v
        llh_all[valid] = eci_to_llh_batch(r_arr[valid], ts)

    nid_to_i = {nid: i for i, nid in enumerate(all_nids)}
    result_cats: dict[str, Any] = {}

    for cat, cfg in OVERPASS_CATS.items():
        sats = []
        for nid in candidates[cat]:
            i = nid_to_i[nid]
            if err_arr[i] != 0 or np.isnan(llh_all[i, 0]):
                continue
            lat, lon, alt = llh_all[i, 0], llh_all[i, 1], llh_all[i, 2]
            if not (-500.0 < float(alt) < 80_000.0):
                continue
            el  = float(el_all[i])
            sats.append({
                "norad_id":  nid,
                "name":      idx[nid]["name"],
                "lat":       round(float(lat), 4),
                "lon":       round(float(lon), 4),
                "alt_km":    round(float(alt), 1),
                "el_deg":    round(el, 2),
                "az_deg":    round(float(az_all[i]), 2),
                "range_km":  round(float(rng_all[i]), 1),
                "visible":   el >= mask_deg,
                "color":     cfg["color"],
            })
        result_cats[cat] = {
            "label":         cfg["label"],
            "sublabel":      cfg["sublabel"],
            "color":         cfg["color"],
            "count":         len(sats),
            "visible_count": sum(1 for s in sats if s["visible"]),
            "satellites":    sats,
        }

    return {
        "categories":    result_cats,
        "timestamp":     ts.isoformat(),
        "mask_deg":      mask_deg,
        "is_historical": ts < now - timedelta(hours=1),
        "is_future":     ts > now + timedelta(hours=1),
    }


def predict_taipei_passes_at(
    ts:          datetime,
    hours:       float = 24.0,
    step_sec:    float = 60.0,
    mask_deg:    float = settings.MASK_DEG,
    max_per_cat: int   = 20,
) -> dict[str, Any]:
    """Pass predictions starting from an arbitrary ts."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)

    idx = get_index_for_time(ts)
    candidates = get_overpass_candidates(idx)
    obs = TAIPEI_OBS

    n_steps = int(hours * 3600 / step_sec)
    times   = [ts + timedelta(seconds=i * step_sec) for i in range(n_steps)]
    jd_fr   = np.array([
        jday(tt.year, tt.month, tt.day, tt.hour, tt.minute,
             tt.second + tt.microsecond * 1e-6)
        for tt in times
    ])
    jds = np.ascontiguousarray(jd_fr[:, 0]); frs = np.ascontiguousarray(jd_fr[:, 1])

    T_cent   = ((jds - 2451545.0) + frs) / 36525.0
    gmst_all = np.deg2rad(
        (280.46061837 + 360.98564736629 * (jds - 2451545.0 + frs)
         + 0.000387933 * T_cent ** 2) % 360.0)
    cg_all = np.cos(gmst_all); sg_all = np.sin(gmst_all)

    all_passes: dict[str, list] = {cat: [] for cat in OVERPASS_CATS}

    for cat, cfg in OVERPASS_CATS.items():
        nids = candidates[cat]
        if not nids or not HAS_SATREC_ARRAY:
            continue

        line1s = [idx[n]["line1"] for n in nids]
        line2s = [idx[n]["line2"] for n in nids]

        try:
            sa = _SatrecArray([Satrec.twoline2rv(l1, l2) for l1, l2 in zip(line1s, line2s)])
            e_raw, r_raw, _ = sa.sgp4(jds, frs)
        except Exception as exc:
            logger.warning("時間軸過頂預報失敗 cat=%s: %s", cat, exc)
            continue

        sl = obs["sl"]; cl = obs["cl"]; so = obs["so"]; co = obs["co"]
        x0 = obs["x0"]; y0 = obs["y0"]; z0 = obs["z0"]
        cat_passes: list[dict] = []

        for sat_idx, nid in enumerate(nids):
            r_ti = r_raw[sat_idx]; e_ti = e_raw[sat_idx]; ok = (e_ti == 0)
            if not ok.any():
                continue
            xe = cg_all * r_ti[:, 0] + sg_all * r_ti[:, 1]
            ye = -sg_all * r_ti[:, 0] + cg_all * r_ti[:, 1]
            ze = r_ti[:, 2]
            dx = xe - x0; dy = ye - y0; dz = ze - z0
            U  = cl * co * dx + cl * so * dy + sl * dz
            E  = -so * dx + co * dy
            N  = -sl * co * dx - sl * so * dy + cl * dz
            rng = np.sqrt(E**2 + N**2 + U**2)
            safe = np.where(rng > 0.001, rng, 0.001)
            el  = np.where(ok, np.rad2deg(np.arcsin(np.clip(U / safe, -1.0, 1.0))), -90.0)

            above = el >= mask_deg
            if not above.any():
                continue

            trans = np.diff(above.astype(int))
            rises = list(np.where(trans == 1)[0] + 1)
            sets  = list(np.where(trans == -1)[0] + 1)
            if above[0]:  rises = [0] + rises
            if above[-1]: sets  = sets + [n_steps - 1]

            for ri, set_i in zip(rises, sets):
                if ri >= set_i:
                    continue
                seg = el[ri: set_i + 1]
                mx  = int(np.argmax(seg))
                cat_passes.append({
                    "norad_id":   nid,
                    "name":       idx[nid]["name"],
                    "t_rise_utc": times[ri].isoformat(),
                    "t_max_utc":  times[ri + mx].isoformat(),
                    "t_set_utc":  times[min(set_i, n_steps - 1)].isoformat(),
                    "max_el_deg": round(float(seg[mx]), 1),
                    "duration_s": int((times[min(set_i, n_steps - 1)] - times[ri]).total_seconds()),
                    "color":      cfg["color"],
                })

            if len(cat_passes) >= max_per_cat:
                break

        cat_passes.sort(key=lambda x: x["t_rise_utc"])
        all_passes[cat] = cat_passes[:max_per_cat]

    return {
        "categories": {
            cat: {"label": cfg["label"], "sublabel": cfg["sublabel"],
                  "color": cfg["color"], "passes": all_passes[cat]}
            for cat, cfg in OVERPASS_CATS.items()
        },
        "hours":       hours,
        "mask_deg":    mask_deg,
        "timestamp":   ts.isoformat(),
        "is_historical": ts < now - timedelta(hours=1),
        "is_future":     ts > now + timedelta(hours=1),
    }
