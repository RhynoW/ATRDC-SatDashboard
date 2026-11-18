"""近接極小值搜尋（v0.3 Sprint 1 雛形；Sprint 0 用於端到端案例）。

g(τ) = r_rel · v_rel 為距離平方的一半之導數。於節點掃描 g 由負轉正的區間，
以 Brent 法求根，再以 dg/dτ = |v_rel|² + r_rel · a_rel > 0 確認為極小值。
搜尋窗端點的極小值標記 window_edge。輸出定義為「在指定節點間距與篩選門檻下辨識出的近接極小值」，
並附搜尋解析度中繼資料；節點間距內發生兩次以上極小值的情況不保證辨識（Sprint 1 自適應細分處理）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .constants import BRENT_XTOL_S

RelState = Callable[[float], tuple]   # τ → (r_rel, v_rel, a_rel)


def brent_root(f: Callable[[float], float], a: float, b: float, fa: float, fb: float,
               xtol: float = BRENT_XTOL_S, maxiter: int = 200) -> float:
    """Brent（1973）zeroin。要求 fa、fb 異號（或其一為 0）。與 Rust 版逐步相同。"""
    if fa == 0.0:
        return a
    if fb == 0.0:
        return b
    c, fc = a, fa
    d = e = b - a
    for _ in range(maxiter):
        if (fb > 0) == (fc > 0):
            c, fc = a, fa
            d = e = b - a
        if abs(fc) < abs(fb):
            a, b, c = b, c, b
            fa, fb, fc = fb, fc, fb
        tol = 2.0 * 2.220446049250313e-16 * abs(b) + 0.5 * xtol
        m = 0.5 * (c - b)
        if abs(m) <= tol or fb == 0.0:
            return b
        if abs(e) >= tol and abs(fa) > abs(fb):
            s = fb / fa
            if a == c:
                p, q = 2.0 * m * s, 1.0 - s
            else:
                q_, r_ = fa / fc, fb / fc
                p = s * (2.0 * m * q_ * (q_ - r_) - (b - a) * (r_ - 1.0))
                q = (q_ - 1.0) * (r_ - 1.0) * (s - 1.0)
            if p > 0:
                q = -q
            else:
                p = -p
            if 2.0 * p < min(3.0 * m * q - abs(tol * q), abs(e * q)):
                e, d = d, p / q
            else:
                d = e = m
        else:
            d = e = m
        a, fa = b, fb
        b += d if abs(d) > tol else (tol if m > 0 else -tol)
        fb = f(b)
    return b


@dataclass
class Approach:
    tau: float
    miss_km: float
    r_rel: np.ndarray
    v_rel: np.ndarray
    a_rel: np.ndarray
    dgdt: float
    flag: str                    # interior | window_edge_start | window_edge_end

    def to_json(self) -> dict:
        return {"tau_s": self.tau, "miss_km": self.miss_km, "v_rel_kms": float(np.linalg.norm(self.v_rel)),
                "r_rel_km": [float(x) for x in self.r_rel], "v_rel_vec_kms": [float(x) for x in self.v_rel],
                "dgdt": self.dgdt, "flag": self.flag}


def _approach(rel: RelState, tau: float, flag: str) -> Approach:
    r, v, a = rel(tau)
    return Approach(tau, float(np.linalg.norm(r)), r, v, a, float(np.dot(v, v) + np.dot(r, a)), flag)


def find_minima(rel: RelState, t0: float, t1: float, step: float, threshold_km: float | None = None) -> dict:
    n = int(round((t1 - t0) / step))
    taus = [t0 + k * step for k in range(n + 1)]
    taus[-1] = t1
    g_nodes = []
    for tau in taus:
        r, v, _ = rel(tau)
        g_nodes.append(float(np.dot(r, v)))

    def g(tau):
        r, v, _ = rel(tau)
        return float(np.dot(r, v))

    found: list[Approach] = []
    if g_nodes[0] > 0:
        found.append(_approach(rel, taus[0], "window_edge_start"))
    for k in range(n):
        ga, gb = g_nodes[k], g_nodes[k + 1]
        if ga < 0 <= gb:
            root = brent_root(g, taus[k], taus[k + 1], ga, gb)
            found.append(_approach(rel, root, "interior"))
    if g_nodes[-1] < 0:
        found.append(_approach(rel, taus[-1], "window_edge_end"))
    for ap in found:
        if ap.flag == "interior" and not ap.dgdt > 0:
            ap.flag = "degenerate"
    events = [ap for ap in found if threshold_km is None or ap.miss_km <= threshold_km]
    gmin = min(found, key=lambda x: x.miss_km) if found else None
    return {
        "events": events,
        "all_minima": found,
        "global_min": gmin,
        "resolution": {"node_step_s": step, "threshold_km": threshold_km, "root_xtol_s": BRENT_XTOL_S,
                       "note": "節點間距內的多重極小值不保證辨識"},
    }
