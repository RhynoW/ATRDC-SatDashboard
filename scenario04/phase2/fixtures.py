"""產生凍結的共用測試資料（執行一次，結果提交到 testdata/；之後 Python 與 Rust 都讀同一份檔案）。

用法：py -3.13 -m scenario04.phase2.fixtures
"""
from __future__ import annotations

import json
import math

import numpy as np

from . import constants as K
from .eop import TESTDATA, load_snapshot
from .forces import ForceModel
from .frames import EarthRotationTable
from .integrator import propagate
from .kepler import Elements, state as kstate
from .timescale import UtcTime

E2E_START = "2026-11-20T00:00:00Z"
T_STAR = 21600.0          # 合成接近時刻（τ）


def _leo_e2e_states():
    """在 τ = T_STAR 讓兩星位於同一點附近（徑向 +0.20 km、越軌 +0.30 km），以完整力模型（二體＋J2–J4）
    往回積分到 τ = 0 得到初始狀態。正向傳播後應在 T_STAR 附近找回約 0.36 km 的接近。"""
    eop = load_snapshot()
    start = UtcTime.parse(E2E_START)
    p = Elements(6878.137, 0.0005, math.radians(51.6), math.radians(30.0), 0.0, 0.0)
    s = Elements(6878.137, 0.0005, math.radians(97.4), math.radians(30.0), 0.0, 0.0)
    rp, vp, _ = kstate(p, 0.0)
    rs, vs, _ = kstate(s, 0.0)
    R = rp / np.linalg.norm(rp)
    N = np.cross(rp, vp); N /= np.linalg.norm(N)
    rs = rs + 0.20 * R + 0.30 * N
    ert = EarthRotationTable(start, -10.0, T_STAR + 10.0, eop)
    fm = ForceModel(zonal=True, drag=False)
    out = {}
    for role, (r, v) in {"primary": (rp, vp), "secondary": (rs, vs)}.items():
        tr = propagate(r, v, T_STAR, 0.0, -K.RK4_STEP_LEO_S, fm, ert)
        out[role] = (tr.r[-1], tr.v[-1])
    return out


def leo_e2e_scenario() -> dict:
    st = _leo_e2e_states()
    obj = lambda role, name, sig: {  # noqa: E731
        "role": role, "name": name, "orbit_regime": "LEO",
        "ephemeris": {"type": "state", "epoch": E2E_START, "frame": "GCRF",
                      "r_km": [float(x) for x in st[role][0]], "v_kms": [float(x) for x in st[role][1]],
                      "source": "synthetic"},
        "covariance": {"mode": "demo", "sigma_km": sig, "source": "assumed", "independent_of_other": True},
        "hbr_m": 5.0, "mass_kg": 260.0, "area_to_mass_m2kg": 0.01, "cd": 2.2,
    }
    return {
        "schema": "satdash.scenario/v1", "id": "e2e-leo-synthetic-01",
        "description": "Sprint 0 最小端到端案例：合成 LEO 雙星，τ=21600 s 附近約 0.36 km 接近，相對速度約 5.6 km/s",
        "time": {"system": "UTC", "eop_source": "celestrak EOP-All.csv", "eop_version": "snapshot 2026-11-18"},
        "frames": {"sgp4_output": "TEME", "propagation": "GCRF"},
        "objects": [obj("primary", "SYN-P", [0.1, 0.5, 0.1]), obj("secondary", "SYN-S", [0.2, 1.0, 0.2])],
        "screening": {"start": E2E_START, "duration_s": 43200.0, "step_s": 10.0, "threshold_km": 25.0},
        "force_model": {"zonal": True, "drag": False},
    }


def main():
    TESTDATA.mkdir(exist_ok=True)
    sc = leo_e2e_scenario()
    (TESTDATA / "e2e_leo_synthetic.json").write_text(json.dumps(sc, indent=1, ensure_ascii=False), encoding="utf-8")
    print("寫入 e2e_leo_synthetic.json")


if __name__ == "__main__":
    main()
