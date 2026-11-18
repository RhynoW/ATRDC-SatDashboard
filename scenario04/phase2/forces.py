"""力模型（v0.3 第 3 節）。所有加速度單位 km/s²。

二體：a = −μ r/|r|³（GCRF）
帶諧 J2–J4：r_ITRF = M r_GCRF，於 ITRF 計算，a_GCRF = Mᵀ a_ITRF
阻力：v_rel,GCRF = v_GCRF − ω⊕,GCRF × r_GCRF（只在此處處理地球自轉一次）
      a = −½ ρ B |v_rel| v_rel × 1000（ρ kg/m³、B = C_d A/m m²/kg、v km/s）
      ρ 的大地高度由 r_ITRF 以 SOFA gc2gd（WGS-84）計算
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import erfa
import numpy as np

from .constants import GM_KM3_S2, J2, J3, J4, RE_KM, WGS84_N
from .frames import EarthRotation


def accel_two_body(r) -> np.ndarray:
    r = np.asarray(r, float)
    n = np.linalg.norm(r)
    return -GM_KM3_S2 * r / n ** 3


def accel_zonal_itrf(r, j2=J2, j3=J3, j4=J4) -> np.ndarray:
    """J2–J4 帶諧加速度（ITRF）。公式為位勢 U = −(μ/r) Σ Jn (Re/r)^n Pn(z/r) 的梯度（測試以數值梯度核對）。"""
    x, y, z = (float(c) for c in r)
    r2 = x * x + y * y + z * z
    rn = math.sqrt(r2)
    z2 = z * z / r2
    mu, R = GM_KM3_S2, RE_KM
    # J2
    f2 = -1.5 * j2 * mu * R ** 2 / rn ** 5
    a2 = np.array([f2 * x * (1 - 5 * z2), f2 * y * (1 - 5 * z2), f2 * z * (3 - 5 * z2)])
    # J3
    f3 = -2.5 * j3 * mu * R ** 3 / rn ** 7
    a3 = np.array([f3 * x * (3 * z - 7 * z ** 3 / r2), f3 * y * (3 * z - 7 * z ** 3 / r2),
                   f3 * (6 * z * z - 7 * z ** 4 / r2 - 0.6 * r2)])
    # J4
    f4 = 1.875 * j4 * mu * R ** 4 / rn ** 7
    a4 = np.array([f4 * x * (1 - 14 * z2 + 21 * z2 * z2), f4 * y * (1 - 14 * z2 + 21 * z2 * z2),
                   f4 * z * (5 - 70.0 / 3.0 * z2 + 21 * z2 * z2)])
    return a2 + a3 + a4


def zonal_potential(r, j2=J2, j3=J3, j4=J4) -> float:
    """帶諧擾動位勢（不含二體項），供測試以數值梯度核對 accel_zonal_itrf。"""
    rn = float(np.linalg.norm(r))
    s = float(r[2]) / rn
    p2 = 0.5 * (3 * s * s - 1)
    p3 = 0.5 * (5 * s ** 3 - 3 * s)
    p4 = (35 * s ** 4 - 30 * s * s + 3) / 8.0
    q = RE_KM / rn
    return -GM_KM3_S2 / rn * (j2 * q ** 2 * p2 + j3 * q ** 3 * p3 + j4 * q ** 4 * p4)


@dataclass
class ExponentialAtmosphere:
    """單層指數大氣（測試與跨語言比對用；作業用密度模型為 NRLMSISE-00，Sprint 1 接入）。"""
    h0_km: float = 400.0
    rho0: float = 3.725e-12
    scale_km: float = 58.515

    def density(self, h_km: float) -> float:
        return self.rho0 * math.exp(-(h_km - self.h0_km) / self.scale_km)


def geodetic_height_km(r_itrf) -> float:
    _elong, _phi, h = erfa.gc2gd(WGS84_N, np.asarray(r_itrf, float) * 1000.0)
    return float(h) / 1000.0


def accel_drag(r, v, er: EarthRotation, ballistic_m2_kg: float, atmos) -> np.ndarray:
    r, v = np.asarray(r, float), np.asarray(v, float)
    w = er.omega_gcrf()
    # ω × r（手寫外積：與 np.cross 相同運算式、逐位相同，但快約 20 倍）
    v_rel = v - np.array([w[1] * r[2] - w[2] * r[1], w[2] * r[0] - w[0] * r[2], w[0] * r[1] - w[1] * r[0]])
    h = geodetic_height_km(er.m_gcrf_to_itrf() @ r)
    rho = atmos.density(h)
    return -0.5 * rho * ballistic_m2_kg * np.linalg.norm(v_rel) * v_rel * 1000.0


@dataclass
class ForceModel:
    zonal: bool = True
    drag: bool = False
    ballistic_m2_kg: float = 0.0          # C_d · A / m
    atmos: object = field(default_factory=ExponentialAtmosphere)

    def describe(self) -> dict:
        return {"two_body": True, "zonal": "J2-J4" if self.zonal else None,
                "drag": ({"model": type(self.atmos).__name__, "ballistic_m2_kg": self.ballistic_m2_kg}
                         if self.drag else None)}

    def accel(self, r, v, er: EarthRotation | None) -> np.ndarray:
        a = accel_two_body(r)
        if self.zonal:
            m = er.m_gcrf_to_itrf()
            a = a + m.T @ accel_zonal_itrf(m @ np.asarray(r, float))
        if self.drag:
            a = a + accel_drag(r, v, er, self.ballistic_m2_kg, self.atmos)
        return a
