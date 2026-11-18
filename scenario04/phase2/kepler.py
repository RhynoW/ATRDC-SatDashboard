"""二體解析解（橢圓軌道），作為 L0 解析測試與跨語言「演算法層」比對的共同輸入。"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .constants import GM_KM3_S2


@dataclass(frozen=True)
class Elements:
    a_km: float
    e: float
    i_rad: float
    raan_rad: float
    argp_rad: float
    m0_rad: float          # τ = 0 的平近點角


def solve_kepler(M: float, e: float) -> float:
    E = M if e < 0.8 else math.pi
    for _ in range(50):
        d = (E - e * math.sin(E) - M) / (1 - e * math.cos(E))
        E -= d
        if abs(d) < 1e-15:
            break
    return E


def state(el: Elements, tau: float, mu: float = GM_KM3_S2):
    """回傳 (r, v, a)，慣性框架 km、km/s、km/s²。"""
    n = math.sqrt(mu / el.a_km ** 3)
    M = math.fmod(el.m0_rad + n * tau, 2 * math.pi)
    E = solve_kepler(M, el.e)
    cE, sE = math.cos(E), math.sin(E)
    b = el.a_km * math.sqrt(1 - el.e ** 2)
    xp, yp = el.a_km * (cE - el.e), b * sE
    rr = el.a_km * (1 - el.e * cE)
    xdot, ydot = -el.a_km * n * sE * el.a_km / rr, b * n * cE * el.a_km / rr
    cO, sO = math.cos(el.raan_rad), math.sin(el.raan_rad)
    cw, sw = math.cos(el.argp_rad), math.sin(el.argp_rad)
    ci, si = math.cos(el.i_rad), math.sin(el.i_rad)
    P = np.array([cO * cw - sO * sw * ci, sO * cw + cO * sw * ci, sw * si])
    Q = np.array([-cO * sw - sO * cw * ci, -sO * sw + cO * cw * ci, cw * si])
    r = xp * P + yp * Q
    v = xdot * P + ydot * Q
    a = -mu * r / np.linalg.norm(r) ** 3
    return r, v, a
