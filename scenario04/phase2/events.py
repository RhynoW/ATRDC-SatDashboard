"""單一近接事件的輸出組裝（端到端與批次共用）：會合幾何、交會平面、適用性篩選、Pc。"""
from __future__ import annotations

import math

import numpy as np

from .constants import GM_KM3_S2
from .pc import pc_chan, pc_foster, project
from .rtn import rtn_matrix
from .screening import screen
from .timescale import UtcTime

ACCURACY_NOTE = "TCA 與距離為數值解（求根容差 1e-6 s），代表求根精度；預報準確度受輸入狀態（TLE 級約公里）與模型不確定性限制"


def build_event(start: UtcTime, ap, rel, eph_p, c3_at, cov_mode: str, hbr_km: float,
                other_minima: list[float], span: tuple[float, float], cov_sources: list[str] | None) -> dict:
    rp, vp = eph_p.rv(ap.tau)
    a_semi = 1.0 / (2.0 / np.linalg.norm(rp) - np.dot(vp, vp) / GM_KM3_S2)
    period = 2 * math.pi * math.sqrt(abs(a_semi) ** 3 / GM_KM3_S2)
    C = rtn_matrix(rp, vp)
    vs = vp + ap.v_rel
    cosang = max(-1.0, min(1.0, float(np.dot(vp, vs) / (np.linalg.norm(vp) * np.linalg.norm(vs)))))
    out = {
        "tca_utc": start.plus(ap.tau).iso(), "tca_tau_s": ap.tau, "flag": ap.flag,
        "miss_km": ap.miss_km, "v_rel_kms": float(np.linalg.norm(ap.v_rel)),
        "miss_rtn_km": [float(x) for x in C @ ap.r_rel],
        "approach_angle_deg": math.degrees(math.acos(cosang)),
        "accuracy": {"root_precision_s": 1e-6, "note": ACCURACY_NOTE},
    }
    scr = screen(rel, ap.tau, c3_at, cov_mode, hbr_km, period, other_minima, span)
    out["applicability"] = scr
    if c3_at is not None:
        C3 = c3_at(ap.tau)
        miss2, C2, _F = project(ap.r_rel, ap.v_rel, C3)
        out["bplane"] = {"miss_km": [float(x) for x in miss2], "cov_km2": C2.tolist()}
        try:
            pf, pch = pc_foster(miss2, C2, hbr_km), pc_chan(miss2, C2, hbr_km)
        except ValueError:
            pf = pch = None
        out["pc"] = {"foster": pf, "chan": pch, "hbr_km": hbr_km, "covariance_mode": cov_mode,
                     "covariance_source": cov_sources,
                     "interpretation": ("通過目前啟發式篩選；Pc 為所列協方差假設下的值"
                                        if scr["status"] == "passes_initial_screen" else
                                        "未通過或無法判定適用性篩選；二維 Pc 僅供參考")}
    return out
