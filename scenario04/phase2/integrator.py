"""固定步長 RK4（GCRF）與五次 Hermite 內插星曆。

固定步長讓 Python 與 Rust 的節點完全相同，跨語言差異只剩浮點運算順序。
節點之間以 (r, v, a) 的五次 Hermite 內插，位置誤差 O(h⁶)。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .forces import ForceModel
from .frames import EarthRotationTable


@dataclass
class Trajectory:
    t0: float                 # 第一個節點的 τ（秒）
    h: float                  # 步長（秒）
    r: np.ndarray             # (n, 3) km
    v: np.ndarray             # (n, 3) km/s
    a: np.ndarray             # (n, 3) km/s²

    @property
    def n(self) -> int:
        return len(self.r)

    @property
    def t1(self) -> float:
        return self.t0 + (self.n - 1) * self.h

    def node_time(self, k: int) -> float:
        return self.t0 + k * self.h

    def state(self, tau: float):
        """回傳 (r, v, a)；超出範圍時拋出。"""
        if tau < self.t0 - 1e-9 or tau > self.t1 + 1e-9:
            raise ValueError(f"τ={tau} 超出星曆範圍 [{self.t0}, {self.t1}]")
        u = (tau - self.t0) / self.h
        k = min(max(int(np.floor(u)), 0), self.n - 2)
        return hermite5(self.r[k], self.v[k], self.a[k], self.r[k + 1], self.v[k + 1], self.a[k + 1],
                        self.h, u - k)


def hermite5(r0, v0, a0, r1, v1, a1, h: float, s: float):
    s2, s3, s4, s5 = s * s, s ** 3, s ** 4, s ** 5
    H = (1 - 10 * s3 + 15 * s4 - 6 * s5,
         s - 6 * s3 + 8 * s4 - 3 * s5,
         0.5 * (s2 - 3 * s3 + 3 * s4 - s5),
         10 * s3 - 15 * s4 + 6 * s5,
         -4 * s3 + 7 * s4 - 3 * s5,
         0.5 * (s3 - 2 * s4 + s5))
    dH = (-30 * s2 + 60 * s3 - 30 * s4,
          1 - 18 * s2 + 32 * s3 - 15 * s4,
          0.5 * (2 * s - 9 * s2 + 12 * s3 - 5 * s4),
          30 * s2 - 60 * s3 + 30 * s4,
          -12 * s2 + 28 * s3 - 15 * s4,
          0.5 * (3 * s2 - 8 * s3 + 5 * s4))
    ddH = (-60 * s + 180 * s2 - 120 * s3,
           -36 * s + 96 * s2 - 60 * s3,
           0.5 * (2 - 18 * s + 36 * s2 - 20 * s3),
           60 * s - 180 * s2 + 120 * s3,
           -24 * s + 84 * s2 - 60 * s3,
           0.5 * (6 * s - 24 * s2 + 20 * s3))
    terms = (r0, h * v0, h * h * a0, r1, h * v1, h * h * a1)
    p = sum(c * t for c, t in zip(H, terms))
    dp = sum(c * t for c, t in zip(dH, terms)) / h
    ddp = sum(c * t for c, t in zip(ddH, terms)) / (h * h)
    return p, dp, ddp


def propagate(r0, v0, t0: float, t1: float, h: float, fm: ForceModel, ert: EarthRotationTable | None) -> Trajectory:
    """自 τ=t0 的 GCRF 狀態積分到 t1（t1 − t0 須為 h 的整數倍，否則延伸到下一個節點）。"""
    n = int(np.ceil((t1 - t0) / h - 1e-9)) + 1
    R = np.zeros((n, 3)); V = np.zeros((n, 3)); A = np.zeros((n, 3))
    r, v = np.asarray(r0, float).copy(), np.asarray(v0, float).copy()

    def f(tau, r_, v_):
        er = ert.at(tau) if ert is not None else None
        return fm.accel(r_, v_, er)

    for k in range(n):
        tau = t0 + k * h
        a = f(tau, r, v)
        R[k], V[k], A[k] = r, v, a
        if k == n - 1:
            break
        k1r, k1v = v, a
        k2r, k2v = v + 0.5 * h * k1v, f(tau + 0.5 * h, r + 0.5 * h * k1r, v + 0.5 * h * k1v)
        k3r, k3v = v + 0.5 * h * k2v, f(tau + 0.5 * h, r + 0.5 * h * k2r, v + 0.5 * h * k2v)
        k4r, k4v = v + h * k3v, f(tau + h, r + h * k3r, v + h * k3v)
        r = r + h / 6.0 * (k1r + 2 * k2r + 2 * k3r + k4r)
        v = v + h / 6.0 * (k1v + 2 * k2v + 2 * k3v + k4v)
    return Trajectory(t0, h, R, V, A)
