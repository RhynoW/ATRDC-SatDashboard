"""星曆來源（GCRF）：數值積分（五次 Hermite）或 SGP4（TEME → GCRF）。

介面：rv(τ) → (r, v)；rva(τ) → (r, v, a)。τ 為自 screening.start 起算的秒。
SGP4 的加速度以 GCRF 速度中央差分（δ = 0.5 s）取得，只在極小值確認時使用。
SGP4 常數：WGS-72、opsmode 'i'（python-sgp4 預設；Rust 版以相同組合建構）。
"""
from __future__ import annotations

import numpy as np

from .constants import DAY_S, MJD0
from .frames import EarthRotationTable, teme_to_gcrf
from .integrator import Trajectory
from .timescale import UtcTime

SGP4_ACCEL_DT_S = 0.5


class NumericalEphemeris:
    kind = "numerical"

    def __init__(self, traj: Trajectory):
        self.traj = traj
        self.span = (min(traj.t0, traj.t1), max(traj.t0, traj.t1))

    def rv(self, tau):
        r, v, _ = self.traj.state(tau)
        return r, v

    def rva(self, tau):
        return self.traj.state(tau)


class Sgp4Ephemeris:
    kind = "sgp4"

    def __init__(self, line1: str, line2: str, start: UtcTime, ert: EarthRotationTable, span: tuple[float, float]):
        from sgp4.api import Satrec
        self.sat = Satrec.twoline2rv(line1, line2)
        self.start, self.ert, self.span = start, ert, span

    def _teme(self, tau):
        t = self.start.plus(tau)
        e, r, v = self.sat.sgp4(MJD0 + t.mjd, t.sec / DAY_S)
        if e != 0:
            raise ValueError(f"SGP4 錯誤碼 {e}（τ={tau:.1f} s）")
        return np.array(r), np.array(v)

    def rv(self, tau):
        r, v = self._teme(tau)
        return teme_to_gcrf(r, v, self.ert.at(tau))

    def rva(self, tau):
        r, v = self.rv(tau)
        _, vp = self.rv(tau + SGP4_ACCEL_DT_S)
        _, vm = self.rv(tau - SGP4_ACCEL_DT_S)
        return r, v, (vp - vm) / (2 * SGP4_ACCEL_DT_S)


class RelativeState:
    """次星減主星；rv 供掃描，rva 供極小值確認。"""

    def __init__(self, primary, secondary):
        self.p, self.s = primary, secondary
        self.n_evals = 0

    def rv(self, tau):
        self.n_evals += 1
        rp, vp = self.p.rv(tau)
        rs, vs = self.s.rv(tau)
        return rs - rp, vs - vp

    def rva(self, tau):
        rp, vp, ap = self.p.rva(tau)
        rs, vs, as_ = self.s.rva(tau)
        return rs - rp, vs - vp, as_ - ap

    __call__ = rva
