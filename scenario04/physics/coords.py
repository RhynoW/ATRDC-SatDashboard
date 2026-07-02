"""座標轉換（Phase 1.2）：純數學運算，無 Flask / DB 依賴。

ECI → LLH（大地座標）、ECI → 站心仰角/方位、GMST 計算。
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
from sgp4.api import jday

R_EARTH_KM = 6378.137
F_EARTH    = 1 / 298.257223563
E2         = F_EARTH * (2 - F_EARTH)


def gmst_iau2006(jd: float, fr: float) -> float:
    """IAU 2006 精確 GMST（弧度），適用 GEO 高精度需求。"""
    T = ((jd - 2451545.0) + fr) / 36525.0
    deg = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0 + fr)
        + 0.000387933 * T ** 2
        - T ** 3 / 38710000.0
    ) % 360.0
    return float(np.deg2rad(deg))


def gmst_rad(jd: float, fr: float) -> float:
    T = ((jd - 2451545.0) + fr) / 36525.0
    return np.deg2rad(
        (280.46061837 + 360.98564736629 * (jd - 2451545.0 + fr)
         + 0.000387933 * T ** 2) % 360.0)


def eci_to_llh_batch(r_arr: np.ndarray, t: datetime) -> np.ndarray:
    """ECI (N,3) km → (N,3) [lat_deg, lon_deg, alt_km]。"""
    x, y, z = r_arr[:, 0], r_arr[:, 1], r_arr[:, 2]
    jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                  t.second + t.microsecond * 1e-6)

    # GEO（alt > 10000 km）自動使用 IAU2006 精確 GMST
    r_mag = np.sqrt(x ** 2 + y ** 2 + z ** 2)
    if bool(np.any(r_mag > R_EARTH_KM + 10_000.0)):
        gmst = gmst_iau2006(jd, fr)
    else:
        T_cent = ((jd - 2451545.0) + fr) / 36525.0
        gmst   = np.deg2rad(
            (280.46061837 + 360.98564736629 * (jd - 2451545.0 + fr)
             + 0.000387933 * T_cent ** 2) % 360.0)

    xe = np.cos(gmst) * x + np.sin(gmst) * y
    ye = -np.sin(gmst) * x + np.cos(gmst) * y
    ze = z
    lon = np.arctan2(ye, xe)
    rr  = np.sqrt(xe ** 2 + ye ** 2)
    lat = np.arctan2(ze, rr * (1.0 - E2))
    alt = np.zeros(len(r_arr), dtype=float)
    for _ in range(5):
        sl    = np.sin(lat)
        N_arr = R_EARTH_KM / np.sqrt(1.0 - E2 * sl ** 2)
        cl    = np.cos(lat)
        alt   = np.where(np.abs(cl) > 1e-9,
                         rr / cl - N_arr,
                         np.abs(ze) / (1.0 - E2) - N_arr)
        lat   = np.arctan2(ze, rr * (1.0 - E2 * (N_arr / (N_arr + alt))))
    return np.column_stack([np.rad2deg(lat), np.rad2deg(lon), alt])


def observer_ecef(lat_deg: float, lon_deg: float, h_km: float) -> dict:
    lat = np.deg2rad(lat_deg)
    lon = np.deg2rad(lon_deg)
    sl = float(np.sin(lat)); cl = float(np.cos(lat))
    so = float(np.sin(lon)); co = float(np.cos(lon))
    N  = R_EARTH_KM / np.sqrt(1.0 - E2 * sl ** 2)
    x0 = (N + h_km) * cl * co
    y0 = (N + h_km) * cl * so
    z0 = (N * (1.0 - E2) + h_km) * sl
    return {"x0": float(x0), "y0": float(y0), "z0": float(z0),
            "sl": sl, "cl": cl, "so": so, "co": co}


def eci_to_elaz(
    r_eci: np.ndarray,   # (N, 3) ECI km
    jd: float, fr: float,
    obs: dict,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (el_deg, az_deg, range_km) arrays of shape (N,)."""
    gmst = gmst_rad(jd, fr)
    cg = np.cos(gmst); sg = np.sin(gmst)
    xe = cg * r_eci[:, 0] + sg * r_eci[:, 1]
    ye = -sg * r_eci[:, 0] + cg * r_eci[:, 1]
    ze = r_eci[:, 2]
    dx = xe - obs["x0"]; dy = ye - obs["y0"]; dz = ze - obs["z0"]
    sl = obs["sl"]; cl = obs["cl"]; so = obs["so"]; co = obs["co"]
    E_enu = -so * dx + co * dy
    N_enu = -sl * co * dx - sl * so * dy + cl * dz
    U_enu =  cl * co * dx + cl * so * dy + sl * dz
    rng   = np.sqrt(E_enu ** 2 + N_enu ** 2 + U_enu ** 2)
    safe  = np.where(rng > 0.001, rng, 0.001)
    el    = np.rad2deg(np.arcsin(np.clip(U_enu / safe, -1.0, 1.0)))
    az    = np.rad2deg(np.arctan2(E_enu, N_enu)) % 360.0
    return el, az, rng
