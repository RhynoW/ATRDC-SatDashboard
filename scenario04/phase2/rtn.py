"""RTN 軸與協方差速度語意（v0.3 第 4 節）。

RTN 軸：R = r/|r|，N = (r×v)/|r×v|，T = N×R；C 的三列依序為 R、T、N（GCRF → RTN）。
velocity_convention：
  inertial_velocity_components  δv_RTN = C · δv_GCRF（慣性速度誤差投影到 RTN 軸）
  rotating_frame_derivative     δv_RTN = d/dt(C δr) = C δv − ω_RTN × (C δr)
                                ω_RTN 取二體近似 (0, 0, |r×v|/|r|²)（Sprint 0 規格；攝動造成的 R 分量忽略）
"""
from __future__ import annotations

import numpy as np

CONVENTIONS = ("inertial_velocity_components", "rotating_frame_derivative")


def rtn_matrix(r, v) -> np.ndarray:
    r, v = np.asarray(r, float), np.asarray(v, float)
    R = r / np.linalg.norm(r)
    h = np.cross(r, v)
    N = h / np.linalg.norm(h)
    T = np.cross(N, R)
    return np.vstack([R, T, N])


def _skew(w) -> np.ndarray:
    return np.array([[0.0, -w[2], w[1]], [w[2], 0.0, -w[0]], [-w[1], w[0], 0.0]])


def jacobian_gcrf_to_rtn(r, v, convention: str) -> np.ndarray:
    if convention not in CONVENTIONS:
        raise ValueError(f"velocity_convention 須為 {CONVENTIONS}，收到 {convention!r}")
    C = rtn_matrix(r, v)
    J = np.zeros((6, 6))
    J[:3, :3] = C
    J[3:, 3:] = C
    if convention == "rotating_frame_derivative":
        r_, v_ = np.asarray(r, float), np.asarray(v, float)
        w = np.array([0.0, 0.0, np.linalg.norm(np.cross(r_, v_)) / np.dot(r_, r_)])
        J[3:, :3] = -_skew(w) @ C
    return J


def cov_rtn_to_gcrf(P_rtn, r, v, convention: str) -> np.ndarray:
    Jinv = np.linalg.inv(jacobian_gcrf_to_rtn(r, v, convention))
    return Jinv @ np.asarray(P_rtn, float) @ Jinv.T


def cov_gcrf_to_rtn(P, r, v, convention: str) -> np.ndarray:
    J = jacobian_gcrf_to_rtn(r, v, convention)
    return J @ np.asarray(P, float) @ J.T


def _omega_rtn(r, v) -> np.ndarray:
    """RTN 軸相對慣性系的角速度（RTN 分量，二體近似）：(0, 0, |r×v|/|r|²)。"""
    r_, v_ = np.asarray(r, float), np.asarray(v, float)
    return np.array([0.0, 0.0, np.linalg.norm(np.cross(r_, v_)) / np.dot(r_, r_)])


def relative_rtn(rt, vt, rc, vc):
    """追蹤星相對目標星的 RTN 狀態（m、m/s）；速度為旋轉 RTN 座標中的時間導數（HCW 的 ẋ, ẏ, ż）。"""
    C = rtn_matrix(rt, vt)
    rho = C @ (np.asarray(rc, float) - np.asarray(rt, float)) * 1000.0
    rhod = C @ (np.asarray(vc, float) - np.asarray(vt, float)) * 1000.0 - np.cross(_omega_rtn(rt, vt), rho)
    return rho, rhod


def rtn_offset_to_gcrf(rt, vt, r_m, v_ms):
    """relative_rtn 的反函數：目標星 GCRF 狀態＋RTN 相對狀態（m、m/s，旋轉座標導數）→ 追蹤星 GCRF（km、km/s）。"""
    C = rtn_matrix(rt, vt)
    rho = np.asarray(r_m, float)
    rc = np.asarray(rt, float) + C.T @ rho / 1000.0
    vc = np.asarray(vt, float) + C.T @ (np.asarray(v_ms, float) + np.cross(_omega_rtn(rt, vt), rho)) / 1000.0
    return rc, vc
