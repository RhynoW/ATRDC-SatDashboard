"""二維 Pc 適用性啟發式篩選（v0.3 第 5 節）。回傳三態：passes_initial_screen／fails_initial_screen／undetermined。

指標：
  T_enc = 2kσ_v/|v_rel|（σ_v：相對位置協方差在 v_rel 方向的標準差）
  δ_lin = TCA ± T_enc/2 內實際相對軌跡偏離 TCA 切線的最大距離
  協方差變化（傳播模式）：B-plane 協方差行列式、兩主軸尺度的相對變化與主軸旋轉角
  多重接近：T_enc 窗內是否另有近接極小值
判定值為待驗證的啟發式（constants.SCREEN_*），Sprint 4 以 Monte Carlo 校正。
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np

from . import constants as K
from .pc import eig2, project


def _is_pd(C) -> bool:
    try:
        np.linalg.cholesky(np.asarray(C, float))
        return True
    except np.linalg.LinAlgError:
        return False


def screen(rel: Callable, tca_tau: float, C3_at: Callable[[float], np.ndarray] | None, cov_mode: str,
           hbr_km: float, period_s: float, other_minima_tau: list[float], span: tuple[float, float]) -> dict:
    out = {"status": "undetermined", "reasons": [], "metrics": {},
           "thresholds": {"k_sigma": K.SCREEN_K_SIGMA, "tenc_max_frac_period": K.SCREEN_TENC_MAX_FRAC_PERIOD,
                          "lin_max_frac": K.SCREEN_LIN_MAX_FRAC, "cov_det_max_rel": K.SCREEN_COV_DET_MAX_REL,
                          "cov_axis_max_rel": K.SCREEN_COV_AXIS_MAX_REL,
                          "cov_rot_max_deg": K.SCREEN_COV_ROT_MAX_DEG},
           "note": "待驗證的啟發式篩選，非二維方法有效性的證明"}
    if C3_at is None:
        out["reasons"].append("missing_covariance")
        return out
    C3 = np.asarray(C3_at(tca_tau), float)
    if not _is_pd(C3):
        out["reasons"].append("covariance_not_positive_definite")
        return out
    r0, v0, _ = rel(tca_tau)
    vn = float(np.linalg.norm(v0))
    if vn <= 0:
        out["reasons"].append("zero_relative_velocity")
        return out
    z = v0 / vn
    sigma_v = math.sqrt(float(z @ C3 @ z))
    t_enc = 2 * K.SCREEN_K_SIGMA * sigma_v / vn
    lo, hi = tca_tau - 0.5 * t_enc, tca_tau + 0.5 * t_enc
    if lo < span[0] or hi > span[1]:
        out["metrics"]["t_enc_s"] = t_enc
        out["reasons"].append("encounter_window_exceeds_ephemeris")
        return out
    # 直線度
    n = 21
    dev = 0.0
    for i in range(n):
        tau = lo + (hi - lo) * i / (n - 1)
        r, _, _ = rel(tau)
        dev = max(dev, float(np.linalg.norm(r - (r0 + v0 * (tau - tca_tau)))))
    miss2, C2, _ = project(r0, v0, C3)
    l1, l2, th0 = eig2(C2)
    lin_ref = hbr_km + math.sqrt(max(l2, 0.0))
    m = out["metrics"]
    m.update({"t_enc_s": t_enc, "period_s": period_s, "t_enc_frac_period": t_enc / period_s,
              "sigma_v_km": sigma_v, "delta_lin_km": dev, "delta_lin_ref_km": lin_ref})
    fails = []
    if t_enc / period_s >= K.SCREEN_TENC_MAX_FRAC_PERIOD:
        fails.append("encounter_too_long")
    if dev >= K.SCREEN_LIN_MAX_FRAC * lin_ref:
        fails.append("relative_motion_not_rectilinear")
    if cov_mode == "propagated":
        def b_cov(tau):
            r_, v_, _ = rel(tau)
            return project(r_, v_, C3_at(tau))[1]
        Ca, Cb = b_cov(lo), b_cov(hi)
        la1, la2, tha = eig2(Ca)
        lb1, lb2, thb = eig2(Cb)
        det_rel = abs(la1 * la2 - lb1 * lb2) / (l1 * l2)
        ax_rel = max(abs(math.sqrt(la1) - math.sqrt(lb1)) / math.sqrt(l1),
                     abs(math.sqrt(la2) - math.sqrt(lb2)) / math.sqrt(l2))
        rot = abs(math.degrees(math.atan2(math.sin(tha - thb), math.cos(tha - thb))))
        rot = min(rot, 180 - rot)
        m.update({"cov_det_rel_change": det_rel, "cov_axis_rel_change": ax_rel, "cov_rot_deg": rot})
        if det_rel >= K.SCREEN_COV_DET_MAX_REL or ax_rel >= K.SCREEN_COV_AXIS_MAX_REL or rot >= K.SCREEN_COV_ROT_MAX_DEG:
            fails.append("covariance_changes_during_encounter")
    else:
        m["covariance_note"] = "展示模式：協方差假設在交會期間不變，未檢查協方差變化"
    if any(lo <= t <= hi for t in other_minima_tau if abs(t - tca_tau) > 1e-9):
        fails.append("multiple_approaches_in_window")
    out["reasons"] = fails
    out["status"] = "fails_initial_screen" if fails else "passes_initial_screen"
    return out
