"""二維碰撞機率：Foster（交會平面數值積分）與 Chan（等面積級數）。

交會平面：ẑ = v_rel/|v_rel|；x̂ = r_rel 垂直 v_rel 的分量方向；ŷ = ẑ × x̂。
3×3 相對位置協方差投影為 2×2：C2 = P C3 Pᵀ（P 的兩列為 x̂、ŷ）。miss 向量在平面內為 (d, 0)。
Foster：以圓盤中心為原點的極座標積分，徑向 Gauss–Legendre、角向梯形（週期函數）。
Chan：主軸座標 u = R²/(σx σy)、v = xm²/σx² + ym²/σy²，
      Pc = e^{−v/2} Σ_m (v/2)^m/m! · [1 − e^{−u/2} Σ_{k≤m} (u/2)^k/k!]
"""
from __future__ import annotations

import math

import numpy as np

from .constants import CHAN_MAX_TERMS, GL_ANGULAR_N, GL_RADIAL_N


def gauss_legendre(n: int):
    """[-1, 1] 的 Gauss–Legendre 節點與權重（Newton 迭代；與 Rust 版同算法）。"""
    x = np.zeros(n)
    w = np.zeros(n)
    for i in range(n):
        z = math.cos(math.pi * (i + 0.75) / (n + 0.5))
        for _ in range(100):
            p0, p1 = 1.0, z
            for k in range(2, n + 1):
                p0, p1 = p1, ((2 * k - 1) * z * p1 - (k - 1) * p0) / k
            dp = n * (z * p1 - p0) / (z * z - 1)
            dz = p1 / dp
            z -= dz
            if abs(dz) < 1e-15:
                break
        p0, p1 = 1.0, z
        for k in range(2, n + 1):
            p0, p1 = p1, ((2 * k - 1) * z * p1 - (k - 1) * p0) / k
        dp = n * (z * p1 - p0) / (z * z - 1)
        x[i] = z
        w[i] = 2.0 / ((1 - z * z) * dp * dp)
    return x, w


def encounter_frame(r_rel, v_rel) -> np.ndarray:
    r, v = np.asarray(r_rel, float), np.asarray(v_rel, float)
    z = v / np.linalg.norm(v)
    rp = r - np.dot(r, z) * z
    nrp = np.linalg.norm(rp)
    if nrp < 1e-12:   # 正面零距離：任取與 ẑ 垂直的方向
        tmp = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        rp = tmp - np.dot(tmp, z) * z
        nrp = np.linalg.norm(rp)
    x = rp / nrp
    y = np.cross(z, x)
    return np.vstack([x, y, z])


def project(r_rel, v_rel, C3):
    F = encounter_frame(r_rel, v_rel)
    P = F[:2]
    C2 = P @ np.asarray(C3, float) @ P.T
    C2 = 0.5 * (C2 + C2.T)
    miss = P @ np.asarray(r_rel, float)
    return miss, C2, F


def pc_foster(miss2, C2, hbr_km: float, nr: int = GL_RADIAL_N, nt: int = GL_ANGULAR_N) -> float:
    a, b, c = C2[0, 0], C2[0, 1], C2[1, 1]
    det = a * c - b * b
    if not det > 0:
        raise ValueError("C2 非正定")
    ia, ib, ic = c / det, -b / det, a / det
    norm = 1.0 / (2 * math.pi * math.sqrt(det))
    xg, wg = gauss_legendre(nr)
    total = 0.0
    for xi, wi in zip(xg, wg):
        rho = 0.5 * hbr_km * (xi + 1.0)
        acc = 0.0
        for j in range(nt):
            th = 2 * math.pi * j / nt
            x = miss2[0] + rho * math.cos(th)
            y = miss2[1] + rho * math.sin(th)
            acc += math.exp(-0.5 * (ia * x * x + 2 * ib * x * y + ic * y * y))
        total += wi * rho * acc * (2 * math.pi / nt)
    return float(norm * total * 0.5 * hbr_km)


def eig2(C2):
    """2×2 對稱矩陣特徵值（大到小）與主軸角（封閉解；與 Rust 版相同）。"""
    a, b, c = C2[0, 0], C2[0, 1], C2[1, 1]
    m = 0.5 * (a + c)
    d = math.sqrt(0.25 * (a - c) ** 2 + b * b)
    theta = 0.5 * math.atan2(2 * b, a - c)
    return m + d, m - d, theta


def pc_chan(miss2, C2, hbr_km: float, max_terms: int = CHAN_MAX_TERMS) -> float:
    l1, l2, th = eig2(C2)
    if not l2 > 0:
        raise ValueError("C2 非正定")
    sx, sy = math.sqrt(l1), math.sqrt(l2)
    ct, st = math.cos(th), math.sin(th)
    xm = ct * miss2[0] + st * miss2[1]
    ym = -st * miss2[0] + ct * miss2[1]
    u = hbr_km ** 2 / (sx * sy)
    v = xm * xm / l1 + ym * ym / l2
    # Pc = Σ_m w_m · tail_m，w_m = e^{−v/2}(v/2)^m/m!（遞迴，不溢位），
    # tail_m = 1 − e^{−u/2} Σ_{k≤m} (u/2)^k/k! = Σ_{k>m} p_k（Poisson 尾項，由大 k 往小 k 累加，避免相減相消）
    if 0.5 * v > 700.0:          # e^{−v/2} 下溢：miss 遠大於 σ
        return 0.0
    max_terms = max_terms + int(math.ceil(0.5 * v))
    kmax = max_terms + 60
    pk = [0.0] * (kmax + 1)
    pk[0] = math.exp(-0.5 * u)
    for k in range(1, kmax + 1):
        pk[k] = pk[k - 1] * 0.5 * u / k
    tail = [0.0] * (kmax + 1)          # tail[m] = Σ_{k>m} p_k
    acc = 0.0
    for k in range(kmax, 0, -1):
        acc += pk[k]
        tail[k - 1] = acc
    w = math.exp(-0.5 * v)             # w_m
    total = 0.0
    for m in range(max_terms):
        if m > 0:
            w *= 0.5 * v / m
        term = w * tail[m]
        total += term
        if m > 0.5 * v + 2 and term < 1e-18 * max(total, 1e-300):
            break
    return float(total)
