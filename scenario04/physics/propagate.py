"""SGP4 傳播（Phase 1.2）：向量化（SatrecArray）優先，退回逐顆傳播。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sgp4.api import Satrec, jday

from .coords import eci_to_llh_batch

logger = logging.getLogger(__name__)

try:
    from sgp4.api import SatrecArray as _SatrecArray
    HAS_SATREC_ARRAY = True
except ImportError:
    _SatrecArray = None  # type: ignore[assignment]
    HAS_SATREC_ARRAY = False


def sgp4_propagate_raw(
    nids:   list[int],
    line1s: list[str],
    line2s: list[str],
    t:      datetime,
) -> tuple[np.ndarray, np.ndarray]:
    n   = len(nids)
    jd0, fr0 = jday(t.year, t.month, t.day, t.hour, t.minute,
                    t.second + t.microsecond * 1e-6)

    if HAS_SATREC_ARRAY and n > 1:
        try:
            sats = _SatrecArray([Satrec.twoline2rv(l1, l2) for l1, l2 in zip(line1s, line2s)])
            e_raw, r_raw, _ = sats.sgp4(np.array([jd0]), np.array([fr0]))
            return e_raw[:, 0].astype(int), r_raw[:, 0, :]
        except Exception as exc:
            logger.debug("SatrecArray 傳播失敗，退回逐顆: %s", exc)

    err_arr = np.zeros(n, dtype=int)
    r_arr   = np.zeros((n, 3), dtype=float)
    for i, (nid, l1, l2) in enumerate(zip(nids, line1s, line2s)):
        try:
            sat = Satrec.twoline2rv(l1, l2)
            err, r, _ = sat.sgp4(jd0, fr0)
            err_arr[i] = err
            if err == 0:
                r_arr[i] = r
            else:
                # SGP4 錯誤碼說明：1=mean eccentricity, 2=mean motion,
                # 3=pert eccentricity, 4=semi-latus rectum, 6=decay
                logger.warning(
                    "SGP4 err=%d NORAD=%d (可能已衰退/TLE過時, epoch=%s)",
                    err, nid,
                    l1[18:32].strip() if l1 and len(l1) > 32 else "N/A",
                )
        except Exception as exc:
            err_arr[i] = 99  # 自訂：解析失敗（非 SGP4 運算錯誤）
            logger.error(
                "TLE 解析失敗 NORAD=%d: %s | line1='%s'",
                nid, exc, l1[:69] if l1 else "<empty>",
            )
    return err_arr, r_arr


def propagate_batch(
    nids: list[int],
    idx:  dict[int, dict[str, Any]],
) -> list[tuple[float, float, float] | None]:
    if not nids:
        return []
    line1s = [idx[n]["line1"] for n in nids]
    line2s = [idx[n]["line2"] for n in nids]
    t = datetime.now(timezone.utc)

    err_arr, r_arr = sgp4_propagate_raw(nids, line1s, line2s, t)
    valid = err_arr == 0

    if not valid.any():
        return [None] * len(nids)

    llh = np.full((len(nids), 3), np.nan)
    llh[valid] = eci_to_llh_batch(r_arr[valid], t)

    results: list[tuple[float, float, float] | None] = []
    for i in range(len(nids)):
        if not valid[i] or np.isnan(llh[i, 0]):
            results.append(None)
            continue
        lat, lon, alt = float(llh[i, 0]), float(llh[i, 1]), float(llh[i, 2])
        if not (-500.0 < alt < 80_000.0):
            results.append(None)
            continue
        results.append((lat, lon, alt))
    return results


def propagate_arc(
    line1: str, line2: str,
    hours: float = 2.0, pts: int = 120,
) -> list[dict[str, float]]:
    """SGP4 外推軌道弧（預設 2h / 121 點），供前端畫軌跡折線用。"""
    sat    = Satrec.twoline2rv(line1, line2)
    now    = datetime.now(timezone.utc)
    step_s = hours * 3600.0 / pts
    positions: list[dict[str, float]] = []
    for i in range(pts + 1):
        t = now + timedelta(seconds=i * step_s)
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                      t.second + t.microsecond * 1e-6)
        err, r_eci, _ = sat.sgp4(jd, fr)
        if err != 0:
            continue
        try:
            llh = eci_to_llh_batch(np.array([r_eci], dtype=float), t)
            lat, lon, alt = float(llh[0, 0]), float(llh[0, 1]), float(llh[0, 2])
        except Exception:
            continue
        if not (-500.0 < alt < 80_000.0):
            continue
        positions.append({
            "lat":    round(lat, 4),
            "lon":    round(lon, 4),
            "alt_km": round(alt, 1),
        })
    return positions


def propagate_now(line1: str, line2: str) -> tuple[float, float, float] | None:
    try:
        sat = Satrec.twoline2rv(line1, line2)
        t   = datetime.now(timezone.utc)
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                      t.second + t.microsecond * 1e-6)
        err, r_eci, _ = sat.sgp4(jd, fr)
        if err != 0:
            return None
        llh = eci_to_llh_batch(np.array([r_eci], dtype=float), t)
        lat, lon, alt = float(llh[0, 0]), float(llh[0, 1]), float(llh[0, 2])
        if not (-500.0 < alt < 80_000.0):
            return None
        return lat, lon, alt
    except Exception:
        return None
