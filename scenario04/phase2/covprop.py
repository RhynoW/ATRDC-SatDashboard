"""協方差傳播模式（v0.3 第 4 節）：沿名義軌跡（SGP4 或數值星曆）以數值動力學傳播 6×6 GCRF 協方差。

    dP/dt = A P + P Aᵀ + Q_c,  A = [[0, I], [G, 0]],  Q_c = diag(0, 0, 0, q, q, q)
    等價於 P(t) = Φ P(t₀) Φᵀ + Q(t)。
G = ∂a/∂r：二體項解析式 μ/r³(3 r̂ r̂ᵀ − I)；J2–J4 帶諧項於 ITRF 以中央差分（步長 COV_JAC_DR_KM）
求得後旋回 GCRF。阻力對位置、速度的偏導數不納入（Sprint 1 規格；結果中標示）。
RK4 固定步長（LEO 20 s、GEO 300 s）；節點之間的時刻以前一節點做一次 RK4 子步取得。
SGP4 只提供名義軌跡；協方差傳播依據的是數值模型，不宣稱與 SGP4 完全一致。
"""
from __future__ import annotations

import numpy as np

from .constants import COV_JAC_DR_KM, GM_KM3_S2
from .forces import accel_zonal_itrf
from .frames import EarthRotationTable


def gravity_gradient(r_gcrf, ert: EarthRotationTable | None, tau: float, zonal: bool) -> np.ndarray:
    r = np.asarray(r_gcrf, float)
    n = np.linalg.norm(r)
    u = r / n
    G = GM_KM3_S2 / n ** 3 * (3.0 * np.outer(u, u) - np.eye(3))
    if zonal and ert is not None:
        M = ert.at(tau).m_gcrf_to_itrf()
        ri = M @ r
        Gz = np.zeros((3, 3))
        for k in range(3):
            e = np.zeros(3)
            e[k] = COV_JAC_DR_KM
            Gz[:, k] = (accel_zonal_itrf(ri + e) - accel_zonal_itrf(ri - e)) / (2 * COV_JAC_DR_KM)
        G = G + M.T @ Gz @ M
    return G


class CovarianceTrack:
    def __init__(self, eph, P0, t0: float, t1: float, h: float, ert: EarthRotationTable | None,
                 zonal: bool = True, q_km2_s3: float = 0.0):
        """延遲傳播：節點只積分到實際查詢過的最晚時刻（≤ t1）。A 矩陣依 τ 快取（RK4 的 k2/k3 與相鄰步端點共用）。"""
        self.eph, self.ert, self.zonal, self.q = eph, ert, zonal, q_km2_s3
        self.h, self.t0, self.t1 = h, t0, t1
        self.P = [np.asarray(P0, float).copy()]
        self._G: dict[float, np.ndarray] = {}

    def _extend_to(self, tau: float):
        while self.t0 + (len(self.P) - 1) * self.h < tau - 1e-9:
            k = len(self.P) - 1
            self.P.append(self._step(self.t0 + k * self.h, self.P[k], self.h))
            self._G.pop(self.t0 + (k - 1) * self.h, None)

    def _gradient(self, tau):
        G = self._G.get(tau)
        if G is None:
            r, _ = self.eph.rv(tau)
            G = self._G[tau] = gravity_gradient(r, self.ert, tau, self.zonal)
        return G

    def _deriv(self, tau, P):
        A = np.zeros((6, 6))
        A[:3, 3:] = np.eye(3)
        A[3:, :3] = self._gradient(tau)
        D = A @ P + P @ A.T
        if self.q:
            D[3:, 3:] += self.q * np.eye(3)
        return D

    def _step(self, tau, P, h):
        k1 = self._deriv(tau, P)
        k2 = self._deriv(tau + 0.5 * h, P + 0.5 * h * k1)
        k3 = self._deriv(tau + 0.5 * h, P + 0.5 * h * k2)
        k4 = self._deriv(tau + h, P + h * k3)
        out = P + h / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
        return 0.5 * (out + out.T)

    def at(self, tau: float) -> np.ndarray:
        if tau > self.t1 + 1e-6:
            raise ValueError(f"τ={tau} 超出協方差傳播範圍 {self.t1}")
        self._extend_to(tau)
        u = (tau - self.t0) / self.h
        k = min(max(int(np.floor(u)), 0), len(self.P) - 1)
        dt = tau - (self.t0 + k * self.h)
        if abs(dt) < 1e-9:
            return self.P[k].copy()
        return self._step(self.t0 + k * self.h, self.P[k], dt)

    def describe(self) -> dict:
        return {"method": "numerical_variational_gcrf", "integrator": "RK4", "step_s": self.h,
                "gravity_partials": "two-body analytic + J2–J4 central difference" if self.zonal else "two-body analytic",
                "drag_partials": "not included", "process_noise": {"model": "white_accel" if self.q else "none",
                                                                   "q_km2_s3": self.q}}
