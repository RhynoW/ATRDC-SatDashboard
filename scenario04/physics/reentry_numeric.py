"""高橢圓軌道再入數值傳播（第二階段）：J2＋月日點質量＋NRLMSIS 阻力＋可變彈道係數，Monte Carlo 足跡。

- 初始狀態：以 SGP4 於 TLE epoch 之 TEME 位置／速度（視為準慣性系）。
- 攝動：地球點質量＋J2；太陽、月球點質量（astropy 內建星曆，10 分鐘網格內插）；
        大氣阻力（pymsis NRLMSIS 2.1，含 CelesTrak 太空天氣；僅於高度 < 1,500 km 計算）；大氣共轉。
- 積分：scipy DOP853，事件：大地高 ≤ interface_km（預設 80 km）即視為再入。
- Monte Carlo：彈道係數 Bc=m/(Cd·A) 與密度尺度因子抽樣 → 再入時刻／落點分布。

精度定位：公開 TLE 級初始軌道＋經驗大氣模型，屬「TLE-derived」估算；再入前最後幾圈對密度極敏感。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy.integrate import solve_ivp
from sgp4.api import Satrec, jday

from .coords import eci_to_llh_batch, gmst_rad

MU = 398600.4418            # km^3/s^2
RE = 6378.137               # km
J2 = 1.08262668e-3
MU_SUN = 1.32712440018e11
MU_MOON = 4902.800066
OMEGA_E = 7.2921150e-5      # rad/s


@dataclass
class Body:
    mass_kg: float = 550.0          # Cluster 目前質量（ESA：約 550 kg）
    area_m2: float = 5.0            # 迎風面積（Ø2.9 m×1.3 m 圓柱：側面 3.8、端面 6.6）
    cd: float = 2.2

    @property
    def bc(self) -> float:          # kg/m^2
        return self.mass_kg / (self.cd * self.area_m2)


class Ephem:
    """太陽／月球 GCRS 位置（km），10 分鐘網格＋線性內插（誤差對點質量攝動可忽略）。"""

    def __init__(self, t0: datetime, t1: datetime, step_min: float = 10.0):
        from astropy.time import Time
        from astropy.coordinates import get_body, solar_system_ephemeris
        import astropy.units as u
        n = int((t1 - t0).total_seconds() / (step_min * 60)) + 2
        self.t0 = t0
        self.dt = step_min * 60.0
        ts = Time([t0 + timedelta(seconds=k * self.dt) for k in range(n)])
        with solar_system_ephemeris.set("builtin"):
            self.sun = get_body("sun", ts).cartesian.xyz.to(u.km).value.T      # (n,3)
            self.moon = get_body("moon", ts).cartesian.xyz.to(u.km).value.T

    def at(self, tsec: float):
        x = tsec / self.dt
        i = int(min(max(x, 0), len(self.sun) - 2)); f = x - i
        return self.sun[i] * (1 - f) + self.sun[i + 1] * f, self.moon[i] * (1 - f) + self.moon[i + 1] * f


class Atmos:
    """NRLMSIS 2.1 密度（kg/m^3）；失敗時退回指數大氣。"""

    def __init__(self, scale: float = 1.0):
        self.scale = scale
        try:
            import pymsis  # noqa: F401
            self.ok = True
        except Exception:  # noqa: BLE001
            self.ok = False
        self.calls = 0

    def rho(self, t: datetime, lat: float, lon: float, alt_km: float) -> float:
        self.calls += 1
        if self.ok:
            try:
                import pymsis
                d = pymsis.calculate(np.datetime64(t.replace(tzinfo=None)), [lon], [lat], [alt_km])
                v = float(np.asarray(d).reshape(-1)[0])
                if np.isfinite(v) and v > 0:
                    return v * self.scale
            except Exception:  # noqa: BLE001
                pass
        # 指數大氣退回（US76 粗略）
        h0, rho0, H = 100.0, 5.6e-7, 5.9
        if alt_km > 150: h0, rho0, H = 150.0, 2.1e-9, 22.5
        if alt_km > 300: h0, rho0, H = 300.0, 1.9e-11, 53.0
        return rho0 * math.exp(-(alt_km - h0) / H) * self.scale


def tle_state(line1: str, line2: str):
    """TLE epoch 之 TEME 位置(km)／速度(km/s) 與 epoch。"""
    sat = Satrec.twoline2rv(line1, line2)
    yy = int(line1[18:20]); year = 2000 + yy if yy < 57 else 1900 + yy
    ep = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=float(line1[20:32]) - 1.0)
    jd, fr = jday(ep.year, ep.month, ep.day, ep.hour, ep.minute, ep.second + ep.microsecond / 1e6)
    e, r, v = sat.sgp4(jd, fr)
    if e:
        raise ValueError(f"SGP4 error {e}")
    return ep, np.array(r, float), np.array(v, float)


def propagate(line1: str, line2: str, body: Body, t_end: datetime, atmos_scale: float = 1.0,
              interface_km: float = 80.0, drag_below_km: float = 1500.0, rtol: float = 1e-9) -> dict:
    """數值傳播至再入（大地高 ≤ interface_km）或 t_end。回傳再入時刻／落點與軌跡摘要。"""
    ep, r0, v0 = tle_state(line1, line2)
    eph = Ephem(ep, t_end + timedelta(hours=1))
    atm = Atmos(atmos_scale)
    bc = body.bc
    ep_jd, ep_fr = jday(ep.year, ep.month, ep.day, ep.hour, ep.minute, ep.second + ep.microsecond / 1e6)

    def llh(r, tsec):
        t = ep + timedelta(seconds=tsec)
        return eci_to_llh_batch(r[None, :], t)[0], t

    def rhs(tsec, y):
        r = y[:3]; v = y[3:]
        rn = np.linalg.norm(r)
        a = -MU * r / rn ** 3
        # J2
        z2 = (r[2] / rn) ** 2; k = 1.5 * J2 * MU * RE ** 2 / rn ** 5
        a += k * np.array([r[0] * (5 * z2 - 1), r[1] * (5 * z2 - 1), r[2] * (5 * z2 - 3)])
        # 第三體
        rs, rm = eph.at(tsec)
        for mu3, r3 in ((MU_SUN, rs), (MU_MOON, rm)):
            d = r3 - r
            a += mu3 * (d / np.linalg.norm(d) ** 3 - r3 / np.linalg.norm(r3) ** 3)
        # 阻力（僅低高度）
        if rn - RE < drag_below_km:
            (lat, lon, alt), t = llh(r, tsec)
            if alt < drag_below_km:
                rho = atm.rho(t, lat, lon, max(alt, 0.0))
                vrel = v - np.cross([0, 0, OMEGA_E], r)           # 大氣共轉
                vn = np.linalg.norm(vrel)
                a += -0.5 * rho * vn * vrel * 1000.0 / bc            # km/s^2（rho kg/m^3, v km/s → ×1000）
        return np.concatenate([v, a])

    def hit(tsec, y):
        (lat, lon, alt), _ = llh(y[:3], tsec)
        return alt - interface_km
    hit.terminal = True; hit.direction = -1

    T = (t_end - ep).total_seconds()
    sol = solve_ivp(rhs, (0.0, T), np.concatenate([r0, v0]), method="DOP853", rtol=rtol, atol=1e-9,
                    events=hit, max_step=600.0, dense_output=False)
    out = {"tle_epoch": ep.isoformat().replace("+00:00", "Z"), "bc_kg_m2": round(bc, 1), "atmos_scale": atmos_scale,
           "interface_km": interface_km, "n_steps": int(sol.t.size), "msis_calls": atm.calls, "reentered": False}
    if sol.t_events and len(sol.t_events[0]):
        te = float(sol.t_events[0][0]); ye = sol.y_events[0][0]
        (lat, lon, alt), t = llh(ye[:3], te)
        out.update(reentered=True, t=t.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                   lat=round(float(lat), 2), lon=round(float(lon), 2), alt_km=round(float(alt), 1),
                   speed_kms=round(float(np.linalg.norm(ye[3:])), 2))
    else:
        (lat, lon, alt), t = llh(sol.y[:3, -1], float(sol.t[-1]))
        out.update(t_end=t.isoformat(), alt_end_km=round(float(alt), 1))
    return out


def monte_carlo(line1: str, line2: str, t_end: datetime, n: int = 40, seed: int = 7,
                mass_kg: float = 550.0, area_range=(3.8, 6.6), cd: float = 2.2,
                rho_sigma_ln: float = 0.3, interface_km: float = 80.0, base_scale: float = 1.0) -> dict:
    """Bc 與密度尺度抽樣（以 base_scale 為中心，回測校準值）→ 再入時刻／落點分布。"""
    rng = np.random.default_rng(seed)
    runs = []
    for k in range(n):
        area = float(rng.uniform(*area_range)); scale = float(base_scale * np.exp(rng.normal(0.0, rho_sigma_ln)))
        r = propagate(line1, line2, Body(mass_kg, area, cd), t_end, atmos_scale=scale, interface_km=interface_km)
        r["area_m2"] = round(area, 2); runs.append(r)
    hits = [r for r in runs if r["reentered"]]
    out = {"n": n, "n_reentered": len(hits), "interface_km": interface_km, "base_scale": base_scale, "runs": runs}
    if hits:
        ts = np.array([datetime.fromisoformat(r["t"].replace("Z", "+00:00")).timestamp() for r in hits])
        lons = np.array([r["lon"] for r in hits]); lats = np.array([r["lat"] for r in hits])
        # 經度以圓統計取中位（避免 ±180 斷裂）
        ang = np.deg2rad(lons); mlon = math.degrees(math.atan2(np.sin(ang).mean(), np.cos(ang).mean()))
        def iso(x): return datetime.fromtimestamp(float(x), tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        out.update(t_median=iso(np.median(ts)), t_p05=iso(np.percentile(ts, 5)), t_p95=iso(np.percentile(ts, 95)),
                   lat_median=round(float(np.median(lats)), 2), lon_median=round(mlon, 2),
                   lat_p05=round(float(np.percentile(lats, 5)), 2), lat_p95=round(float(np.percentile(lats, 95)), 2),
                   spread_hours=round(float(np.percentile(ts, 95) - np.percentile(ts, 5)) / 3600.0, 2))
        # 各再入圈次分群（以 20 h 為界）
        order = np.argsort(ts); groups = []; cur = [order[0]]
        for i in order[1:]:
            if ts[i] - ts[cur[-1]] < 20 * 3600: cur.append(i)
            else: groups.append(cur); cur = [i]
        groups.append(cur)
        out["pass_groups"] = [{"n": len(g), "share": round(len(g) / len(hits), 2), "t_median": iso(np.median(ts[g])),
                               "lat": round(float(np.median(lats[g])), 1), "lon": round(float(np.median(lons[g])), 1)} for g in groups]
    return out
