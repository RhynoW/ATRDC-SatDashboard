"""台北天球視角過頂預報：回傳各衛星過頂之逐點方位角／仰角軌跡（供 /taipei 天球圖與動畫使用）。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sgp4.api import Satrec, jday

from ..config import settings
from ..ingestion.index import get_index_for_time
from .coverage import OVERPASS_CATS, TAIPEI_OBS, get_overpass_candidates
from .propagate import HAS_SATREC_ARRAY

if HAS_SATREC_ARRAY:
    from sgp4.api import SatrecArray as _SatrecArray

logger = logging.getLogger(__name__)

_CHUNK = 400                 # 每批傳播衛星數（限制記憶體）
_MAX_EVALS = 6_000_000       # 衛星×時間點上限，超過則自動放大步長
_SKIP_NAME_TAGS = (" DEB", " R/B")   # 碎片與火箭體：天球圖只顯示運作中衛星
_STATIONARY_DEG = 3.0        # 整個時窗內角位移小於此值視為地球同步（不畫軌跡箭頭）


def _gmst_rad(jds: np.ndarray, frs: np.ndarray) -> np.ndarray:
    t_cent = ((jds - 2451545.0) + frs) / 36525.0
    return np.deg2rad((280.46061837 + 360.98564736629 * (jds - 2451545.0 + frs)
                       + 0.000387933 * t_cent ** 2) % 360.0)


def _subpoint_deg(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """ECEF(km) → 星下點大地緯度／經度（度，WGS-84，Bowring 迭代）。"""
    a = 6378.137
    e2 = (1 / 298.257223563) * (2 - 1 / 298.257223563)
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - e2))
    for _ in range(3):
        n = a / np.sqrt(1 - e2 * np.sin(lat) ** 2)
        lat = np.arctan2(z + e2 * n * np.sin(lat), p)
    return np.rad2deg(lat), np.rad2deg(lon)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """布林序列中連續 True 區間（含端點）。"""
    if not mask.any():
        return []
    d = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0] - 1))


def predict_taipei_sky_passes(
    ts: datetime,
    hours: float = 2.0,
    step_sec: float = 20.0,
    mask_deg: float = settings.MASK_DEG,
    min_el: float = 10.0,
    max_per_cat: int = 25,
) -> dict[str, Any]:
    """自 ts 起 hours 小時內，各類別最大仰角 ≥ min_el 者之過頂軌跡。

    每類別取最大仰角最高之 max_per_cat 筆（再依升起時間排序），
    track 為 [相對 ts 之秒數, 方位角°(北=0 順時針), 仰角°, 星下點緯度°, 星下點經度°]。
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.replace(second=0, microsecond=0)

    idx = get_index_for_time(ts)
    candidates = get_overpass_candidates(idx)
    n_all = sum(len(v) for v in candidates.values())

    step_sec = max(10.0, float(step_sec))
    n_steps = int(hours * 3600 / step_sec) + 1
    if n_all and n_all * n_steps > _MAX_EVALS:
        step_sec = max(step_sec, np.ceil(hours * 3600 * n_all / _MAX_EVALS))
        n_steps = int(hours * 3600 / step_sec) + 1

    offs = np.arange(n_steps) * step_sec
    times = [ts + timedelta(seconds=float(s)) for s in offs]
    jd_fr = np.array([jday(t.year, t.month, t.day, t.hour, t.minute,
                           t.second + t.microsecond * 1e-6) for t in times])
    jds = np.ascontiguousarray(jd_fr[:, 0])
    frs = np.ascontiguousarray(jd_fr[:, 1])
    gm = _gmst_rad(jds, frs)
    cg, sg = np.cos(gm), np.sin(gm)

    obs = TAIPEI_OBS
    sl, cl, so, co = obs["sl"], obs["cl"], obs["so"], obs["co"]
    x0, y0, z0 = obs["x0"], obs["y0"], obs["z0"]

    passes: list[dict[str, Any]] = []
    for cat, cfg in OVERPASS_CATS.items():
        nids = candidates.get(cat, [])
        if not nids or not HAS_SATREC_ARRAY:
            continue
        cat_passes: list[dict[str, Any]] = []
        for c0 in range(0, len(nids), _CHUNK):
            chunk = nids[c0:c0 + _CHUNK]
            try:
                sa = _SatrecArray([Satrec.twoline2rv(idx[n]["line1"], idx[n]["line2"])
                                   for n in chunk])
                e_raw, r_raw, _ = sa.sgp4(jds, frs)
            except Exception as exc:  # noqa: BLE001
                logger.warning("天球過頂傳播失敗 cat=%s: %s", cat, exc)
                continue
            xe = cg * r_raw[:, :, 0] + sg * r_raw[:, :, 1]
            ye = -sg * r_raw[:, :, 0] + cg * r_raw[:, :, 1]
            ze = r_raw[:, :, 2]
            dx, dy, dz = xe - x0, ye - y0, ze - z0
            up = cl * co * dx + cl * so * dy + sl * dz
            east = -so * dx + co * dy
            north = -sl * co * dx - sl * so * dy + cl * dz
            rng = np.sqrt(east ** 2 + north ** 2 + up ** 2)
            el = np.rad2deg(np.arcsin(np.clip(up / np.where(rng > 1e-3, rng, 1e-3), -1.0, 1.0)))
            el = np.where(e_raw == 0, el, -90.0)
            az = np.rad2deg(np.arctan2(east, north)) % 360.0
            sub_lat, sub_lon = _subpoint_deg(xe, ye, ze)

            for k in np.where((el >= mask_deg).any(axis=1))[0]:
                for a, b in _runs(el[k] >= mask_deg):
                    seg = el[k, a:b + 1]
                    mx = int(np.argmax(seg))
                    if seg[mx] < min_el:
                        continue
                    nid = chunk[k]
                    name = idx[nid]["name"]
                    if any(tag in name.upper() for tag in _SKIP_NAME_TAGS):
                        continue
                    track = [[int(offs[i]), round(float(az[k, i]), 1),
                              round(float(el[k, i]), 1),
                              round(float(sub_lat[k, i]), 2),
                              round(float(sub_lon[k, i]), 2)] for i in range(a, b + 1)]
                    stationary = bool(
                        a == 0 and b == n_steps - 1
                        and float(np.ptp(el[k, a:b + 1])) < _STATIONARY_DEG
                        and min(float(np.ptp(az[k, a:b + 1])),
                                360.0 - float(np.ptp(az[k, a:b + 1]))) < _STATIONARY_DEG)
                    if stationary:
                        track = [track[0], track[-1]]
                    cat_passes.append({
                        "norad_id": nid,
                        "name": name,
                        "cat": cat,
                        "color": cfg["color"],
                        "t_rise_utc": times[a].isoformat(),
                        "t_max_utc": times[a + mx].isoformat(),
                        "t_set_utc": times[b].isoformat(),
                        "max_el_deg": round(float(seg[mx]), 1),
                        "duration_s": int(offs[b] - offs[a]),
                        "clipped_start": bool(a == 0),
                        "clipped_end": bool(b == n_steps - 1),
                        "stationary": stationary,
                        "track": track,
                    })
        cat_passes.sort(key=lambda p: -p["max_el_deg"])
        passes.extend(cat_passes[:max_per_cat])

    passes.sort(key=lambda p: p["t_rise_utc"])
    return {
        "timestamp": ts.isoformat(),
        "hours": hours,
        "step_sec": step_sec,
        "mask_deg": mask_deg,
        "min_el_deg": min_el,
        "observer": {"lat": settings.TAIPEI_LAT, "lon": settings.TAIPEI_LON,
                     "h_km": settings.TAIPEI_H_KM},
        "categories": {c: {"label": cfg["label"], "sublabel": cfg["sublabel"],
                           "color": cfg["color"]} for c, cfg in OVERPASS_CATS.items()},
        "passes": passes,
    }
