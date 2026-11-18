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
from .constants import SEARCH_ACCEL_BOUND_KM_S2 as K_ACCEL_BOUND
from .constants import SEARCH_COARSE_STEP_S as K_COARSE_STEP_S
from .constants import SEARCH_LOW_VREL_KM_S as K_LOW_VREL
from .constants import SEARCH_MIN_STEP_S as K_MIN_STEP_S
from .constants import SEARCH_SUBDIVIDE as K_SUBDIVIDE
from .constants import SEARCH_TIDAL_COEF_S2 as K_TIDAL_COEF
from .constants import SEARCH_PERT_ACCEL_KM_S2 as K_PERT_ACCEL
from .constants import SEARCH_OMEGA_MAX_RAD_S as K_OMEGA_MAX

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


def _golden_min(f, a: float, b: float, tol: float = 1e-6, maxiter: int = 200) -> float:
    """距離的一維黃金分割極小化（低相對速度時與 g 求根互相確認）。"""
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c, d = b - gr * (b - a), a + gr * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(maxiter):
        if abs(b - a) <= tol:
            break
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - gr * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + gr * (b - a)
            fd = f(d)
    return 0.5 * (a + b)


def find_minima_adaptive(rel_rv: Callable[[float], tuple], rel_rva: RelState, t0: float, t1: float,
                         coarse_step: float = K_COARSE_STEP_S, threshold_km: float | None = None,
                         min_step: float = K_MIN_STEP_S, accel_bound: float = K_ACCEL_BOUND) -> dict:
    """自適應近接極小值搜尋（v0.3 Sprint 1）。

    1. 以 coarse_step 掃描 g = r·v 與距離 d。
    2. 對每個區間 [a, b]：相對加速度上界 a_max = min(accel_bound, 3μ/Re³·d_up + 攝動差上界)，
       v_max = max(|v_a|, |v_b|) + a_max·h，距離下界 LB = min(d_a, d_b) − v_max·h/2，
       |d²g/dτ²| 上界 G₂ = 3 v_max a_max + d_up (潮汐係數·v_max + a_max ω_max)，g 偏離弦至多 G₂h²/8。
       當 LB < 門檻、且端點 g 加減 G₂h²/8 可能跨過 0（區間內可能藏有未偵測的 g 根）、且 h > min_step 時，
       把區間六等分遞迴細分；否則為葉區間。兩端相對速度都低於 LOW_VREL（對接或共位）時不細分。
    3. 葉區間內 g 由負轉正 → Brent 求根，dg/dτ > 0 確認極小值；
       |v_rel| < LOW_VREL 時另以黃金分割對距離極小化，兩者取距離較小者並標記 low_relative_velocity。
    4. 搜尋窗端點：g(t0) > 0 或 g(t1) < 0 → window_edge。
    保證：任何距離低於門檻、且與相鄰極小值相隔超過 min_step 的近接都會被辨識；
    門檻以上的極小值只在其所在區間的解析度下辨識（供多重接近檢查與全窗最小值）。
    """
    n = max(1, int(math.ceil((t1 - t0) / coarse_step - 1e-9)))
    taus = [t0 + k * coarse_step for k in range(n)] + [t1]

    def ev(tau):
        r, v = rel_rv(tau)
        return (float(np.linalg.norm(r)), float(np.dot(r, v)), float(np.linalg.norm(v)))

    def g(tau):
        r, v = rel_rv(tau)
        return float(np.dot(r, v))

    nodes = [ev(t) for t in taus]
    found: list[Approach] = []
    n_refined = 0
    finest = coarse_step

    def leaf(a, b, sa, sb):
        if sa[1] < 0 <= sb[1]:
            root = brent_root(g, a, b, sa[1], sb[1])
            ap = _approach(rel_rva, root, "interior")
            if float(np.linalg.norm(ap.v_rel)) < K_LOW_VREL:
                tg = _golden_min(lambda t: float(np.linalg.norm(rel_rv(t)[0])), a, b)
                alt = _approach(rel_rva, tg, "interior")
                if alt.miss_km < ap.miss_km:
                    ap = alt
                ap.flag = "low_relative_velocity"
            found.append(ap)

    stack = [(taus[k], taus[k + 1], nodes[k], nodes[k + 1]) for k in range(n)][::-1]
    while stack:
        a, b, sa, sb = stack.pop()
        h = b - a
        if threshold_km is not None and h > min_step * (1 + 1e-9) and max(sa[2], sb[2]) >= K_LOW_VREL:
            # 相對加速度上界：近距離時為潮汐項 3μ/r³·d 量級＋攝動差，遠距離時以 accel_bound 封頂
            d_up = max(sa[0], sb[0]) + (max(sa[2], sb[2]) + accel_bound * h) * h / 2.0
            amax = min(accel_bound, K_TIDAL_COEF * d_up + K_PERT_ACCEL)
            vmax = max(sa[2], sb[2]) + amax * h
            lb = min(sa[0], sb[0]) - vmax * h / 2.0
            # |d²g/dτ²| = |3 v·a + r·j| 上界；相對 jerk 上界 = 潮汐係數·v_max + a_max·ω_max
            jmax = K_TIDAL_COEF * vmax + amax * K_OMEGA_MAX
            dev = (3.0 * vmax * amax + d_up * jmax) * h * h / 8.0   # g 偏離弦的最大量
            ga, gb = sa[1], sb[1]
            if ga < 0 and gb < 0:
                hidden_root_possible = max(ga, gb) + dev >= 0
            elif ga > 0 and gb > 0:
                hidden_root_possible = min(ga, gb) - dev <= 0
            else:
                hidden_root_possible = dev >= min(abs(ga), abs(gb))
            if lb < threshold_km and hidden_root_possible:
                m = int(min(K_SUBDIVIDE, math.ceil(h / min_step - 1e-9)))
                sub = [a + h * i / m for i in range(m + 1)]
                sub[-1] = b
                sv = [sa] + [ev(t) for t in sub[1:-1]] + [sb]
                n_refined += 1
                finest = min(finest, h / m)
                stack.extend([(sub[i], sub[i + 1], sv[i], sv[i + 1]) for i in range(m)][::-1])
                continue
        leaf(a, b, sa, sb)

    if nodes[0][1] > 0:
        found.insert(0, _approach(rel_rva, taus[0], "window_edge_start"))
    if nodes[-1][1] < 0:
        found.append(_approach(rel_rva, taus[-1], "window_edge_end"))
    for ap in found:
        if ap.flag in ("interior", "low_relative_velocity") and not ap.dgdt > 0:
            ap.flag = "degenerate"
    found.sort(key=lambda x: x.tau)
    events = [ap for ap in found if threshold_km is None or ap.miss_km <= threshold_km]
    gmin = min(found, key=lambda x: x.miss_km) if found else None
    return {
        "events": events, "all_minima": found, "global_min": gmin,
        "resolution": {"method": "adaptive", "coarse_step_s": coarse_step, "min_step_s": min_step,
                       "finest_step_used_s": finest, "threshold_km": threshold_km, "accel_bound_km_s2": accel_bound,
                       "root_xtol_s": BRENT_XTOL_S, "n_refined_intervals": n_refined,
                       "guarantee": "門檻以下、且與相鄰極小值相隔超過最小步長的近接必被辨識",
                       "note": "TCA 與距離為數值解精度，不代表預報準確度"},
    }


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
