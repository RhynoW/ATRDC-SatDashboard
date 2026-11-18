"""TEME / GCRF / ITRF 狀態與協方差轉換（v0.3 第 3 節）。

GCRF → ITRF（IAU-2006/2000A，CIO 法，含 dX、dY）：
    X, Y = xy06(TT) + (dX, dY);  s = s06(TT, X, Y);  RC2I = c2ixys(X, Y, s)
    r_TIRS = R3(ERA(UT1)) · RC2I · r_GCRF
    v_TIRS = R3(ERA) · RC2I · v_GCRF − ω × r_TIRS          （v_GCRF 為慣性速度；ω = (0,0,ω⊕(1−LOD/86400))）
    r_ITRF = W · r_TIRS,  v_ITRF = W · v_TIRS               （W = pom00(xp, yp, s')）
TEME → ITRF（Vallado 2006）：
    r_PEF = R3(GMST82(UT1)) · r_TEME,  v_PEF = R3 · v_TEME − ω × r_PEF,  再乘 W
地球自轉項在每條轉換鏈中只出現一次（TIRS／PEF 這一層）。
所有轉換對 (r, v) 是線性的，6×6 Jacobian 由轉換作用在單位向量上得到，協方差以 J P Jᵀ 轉換。
"""
from __future__ import annotations

from dataclasses import dataclass

import erfa
import numpy as np

from .constants import OMEGA_E_RAD_S, PN_NODE_STEP_S, DAY_S
from .eop import Eop
from .timescale import UtcTime


def rz(theta: float) -> np.ndarray:
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])


@dataclass
class EarthRotation:
    """單一時刻的地球定向：RC2I、ERA、W、ω、GMST82。"""
    rc2i: np.ndarray
    era: float
    rpom: np.ndarray
    omega: float
    gmst82: float

    def m_gcrf_to_tirs(self) -> np.ndarray:
        return rz(self.era) @ self.rc2i

    def m_gcrf_to_itrf(self) -> np.ndarray:
        return self.rpom @ rz(self.era) @ self.rc2i

    def omega_gcrf(self) -> np.ndarray:
        """地球自轉向量在 GCRF 的表示（TIRS z 軸 = CIP）。"""
        return self.m_gcrf_to_tirs().T @ np.array([0.0, 0.0, self.omega])


def _pn_quantities(t: UtcTime, eop: Eop) -> tuple:
    ev = eop.at(t.mjd_float())
    tt1, tt2 = t.tt_jd(ev.dat_s)
    x, y = erfa.xy06(tt1, tt2)
    x, y = x + ev.dx_rad, y + ev.dy_rad
    s = erfa.s06(tt1, tt2, x, y)
    sp = erfa.sp00(tt1, tt2)
    return x, y, s, sp, ev


def earth_rotation(t: UtcTime, eop: Eop) -> EarthRotation:
    """直接計算（不內插），用於單一時刻的轉換與作為內插的參考。"""
    x, y, s, sp, ev = _pn_quantities(t, eop)
    u1, u2 = t.ut1_jd(ev.ut1_utc_s)
    return EarthRotation(
        rc2i=np.asarray(erfa.c2ixys(x, y, s)),
        era=float(erfa.era00(u1, u2)),
        rpom=np.asarray(erfa.pom00(ev.xp_rad, ev.yp_rad, sp)),
        omega=OMEGA_E_RAD_S * (1.0 - ev.lod_s / DAY_S),
        gmst82=float(erfa.gmst82(u1, u2)),
    )


class EarthRotationTable:
    """傳播區間的地球定向表：X、Y、s、xp、yp、s'、UT1−UTC、LOD 於 PN_NODE_STEP_S 節點線性內插；
    ERA 與 GMST 由內插後的 UT1 精確計算。內插誤差見 tests（< 1e-9 rad）。"""

    def __init__(self, epoch: UtcTime, t0_s: float, t1_s: float, eop: Eop, step_s: float = PN_NODE_STEP_S):
        self.epoch, self.step = epoch, step_s
        n0 = int(np.floor(t0_s / step_s)) - 1
        n1 = int(np.ceil(t1_s / step_s)) + 1
        self.n0 = n0
        vals = []
        for k in range(n0, n1 + 1):
            x, y, s, sp, ev = _pn_quantities(epoch.plus(k * step_s), eop)
            vals.append((x, y, s, ev.xp_rad, ev.yp_rad, sp, ev.ut1_utc_s, ev.lod_s))
        self.vals = np.array(vals)
        self.dat = eop.at(epoch.plus(t0_s).mjd_float()).dat_s
        if eop.at(epoch.plus(t1_s).mjd_float()).dat_s != self.dat:
            raise ValueError("傳播區間跨越閏秒，Sprint 0 不支援")

    def at(self, tau_s: float) -> EarthRotation:
        u = tau_s / self.step - self.n0
        i = min(max(int(np.floor(u)), 0), len(self.vals) - 2)
        f = u - i
        v = self.vals[i] + f * (self.vals[i + 1] - self.vals[i])
        x, y, s, xp, yp, sp, dut1, lod = v
        t = self.epoch.plus(tau_s)
        u1, u2 = t.ut1_jd(dut1)
        return EarthRotation(
            rc2i=np.asarray(erfa.c2ixys(x, y, s)),
            era=float(erfa.era00(u1, u2)),
            rpom=np.asarray(erfa.pom00(xp, yp, sp)),
            omega=OMEGA_E_RAD_S * (1.0 - lod / DAY_S),
            gmst82=float(erfa.gmst82(u1, u2)),
        )


# ── 狀態轉換 ──────────────────────────────────────────────────────────────────

def gcrf_to_itrf(r, v, er: EarthRotation):
    m = er.m_gcrf_to_tirs()
    rt = m @ np.asarray(r, float)
    vt = m @ np.asarray(v, float) - np.cross([0.0, 0.0, er.omega], rt)
    return er.rpom @ rt, er.rpom @ vt


def itrf_to_gcrf(r, v, er: EarthRotation):
    rt = er.rpom.T @ np.asarray(r, float)
    vt = er.rpom.T @ np.asarray(v, float)
    m = er.m_gcrf_to_tirs()
    return m.T @ rt, m.T @ (vt + np.cross([0.0, 0.0, er.omega], rt))


def teme_to_itrf(r, v, er: EarthRotation):
    m = rz(er.gmst82)
    rp = m @ np.asarray(r, float)
    vp = m @ np.asarray(v, float) - np.cross([0.0, 0.0, er.omega], rp)
    return er.rpom @ rp, er.rpom @ vp


def itrf_to_teme(r, v, er: EarthRotation):
    rp = er.rpom.T @ np.asarray(r, float)
    vp = er.rpom.T @ np.asarray(v, float)
    m = rz(er.gmst82)
    return m.T @ rp, m.T @ (vp + np.cross([0.0, 0.0, er.omega], rp))


def teme_to_gcrf(r, v, er: EarthRotation):
    return itrf_to_gcrf(*teme_to_itrf(r, v, er), er)


def gcrf_to_teme(r, v, er: EarthRotation):
    return itrf_to_teme(*gcrf_to_itrf(r, v, er), er)


def state_jacobian(fn, er: EarthRotation) -> np.ndarray:
    """線性狀態轉換的 6×6 Jacobian：J[:, k] = fn(e_k)。"""
    J = np.zeros((6, 6))
    for k in range(6):
        e = np.zeros(6)
        e[k] = 1.0
        r, v = fn(e[:3], e[3:], er)
        J[:3, k], J[3:, k] = r, v
    return J


def transform_covariance(P: np.ndarray, fn, er: EarthRotation) -> np.ndarray:
    J = state_jacobian(fn, er)
    return J @ np.asarray(P, float) @ J.T
