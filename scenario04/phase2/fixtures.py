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


# ---------------------------------------------------------------- Sprint 2：避碰
MANEUVER_CATALOG_IDS = ("25544", "48274", "59103", "68762")


def maneuver_leo_scenario() -> dict:
    """以 e2e_leo_propagated 為基礎：TCA ≈ 06:00Z、miss 0.23 km、Pc 6.2e-6（傳播協方差）。
    目標 Pc ≤ 1e-7 且 miss ≥ 1 km；機動窗 TCA−5 h～TCA−30 min；最短通知 30 min；Δv 0.01–0.5 m/s。"""
    sc = json.loads((TESTDATA / "e2e_leo_propagated.json").read_text(encoding="utf-8"))
    tles = json.loads((TESTDATA / "tle_fixture.json").read_text(encoding="utf-8"))["tles"]
    sc["id"] = "maneuver-leo-01"
    sc["description"] = ("Sprint 2 避碰案例：合成 LEO 雙星（傳播協方差），TCA≈06:00Z、0.23 km、Pc 6.2e-6；"
                         "沿跡向單脈衝，目標 Pc ≤ 1e-7 且距離 ≥ 1 km；二次篩選 4 顆 TLE 物體")
    sc["analysis_time"] = E2E_START
    sc["conjunction"] = {"tca": "2026-11-20T06:00:00Z", "source": "screening"}
    p = sc["objects"][0]
    p["propulsion"] = {"isp_s": 220.0, "dv_budget_ms": 1.0, "min_notice_s": 1800.0, "thrust_dirs": ["T", "-T"]}
    p["covariance"]["maneuver_execution_error"] = {"included": True, "mag_frac_sigma": 0.02, "point_deg_sigma": 1.0}
    sc["maneuver_search"] = {
        "window_rel_tca_s": [-18000.0, -1800.0], "dv_ms": [0.01, 0.5], "pc_target": 1e-7, "min_miss_km": 1.0,
        "n_times": 24, "n_dv": 20, "max_verify": 6,
        "secondary_screening": {"catalog": "provided", "objects": [
            {"name": tles[k]["name"], "norad_id": int(k),
             "ephemeris": {"type": "TLE", "line1": tles[k]["line1"], "line2": tles[k]["line2"]}}
            for k in MANEUVER_CATALOG_IDS]}}
    return sc


def write_maneuver_fixtures():
    sc = maneuver_leo_scenario()
    (TESTDATA / "maneuver_leo.json").write_text(json.dumps(sc, indent=1, ensure_ascii=False), encoding="utf-8")
    print("寫入 maneuver_leo.json")


def _trap_object(sc: dict, burn_tau: float, sign: int, dv_ms: float, handover_tau: float, tau_star: float) -> dict:
    """在 τ* 經過指定候選方案機動後主星位置（徑向 +0.2 km）、垂直穿越的狀態向量物體（往回積分到 start）。
    參考軌跡（不機動）在 τ* 與它相距數公里，機動後變成 < 1 km → 應判為新增／變近的交會。"""
    from . import e2e
    from .rtn import rtn_matrix
    eop = load_snapshot()
    ctx = e2e.build_context(sc, eop)
    start, dur, step = ctx["start"], ctx["dur"], ctx["step"]
    g = step * math.ceil(K.COV_STEP_LEO_S / step - 1e-9)
    ert = EarthRotationTable(start, ctx["t_lo"] - 1.0, dur + 2 * g + 1.0, eop)
    prim = ctx["objs"]["primary"]
    fm_p = e2e.object_force_model(prim, ctx["fm_cfg"])
    r_h, v_h = ctx["ephs"]["primary"].rv(handover_tau)
    n0 = propagate(r_h, v_h, handover_tau, dur, step, fm_p, ert)
    kb = int(round((burn_tau - handover_tau) / step))
    rb, vb = n0.r[kb], n0.v[kb]
    vn = vb + sign * rtn_matrix(rb, vb)[1] * (dv_ms / 1000.0)
    tr = propagate(rb, vn, burn_tau, dur, step, fm_p, ert)
    ks = int(round((tau_star - burn_tau) / step))
    rc, vc = tr.r[ks], tr.v[ks]
    rhat = rc / np.linalg.norm(rc)
    ro = rc + 0.2 * rhat
    vo = np.cross(rhat, vc)
    vo = vo / np.linalg.norm(vo) * np.linalg.norm(vc)
    fm = ForceModel(zonal=ctx["fm_cfg"]["zonal"], drag=False)
    back = propagate(ro, vo, tau_star, 0.0, -step, fm, ert)
    return {"name": "TRAP-SYN", "ephemeris": {"type": "state", "epoch": E2E_START, "frame": "GCRF",
                                               "r_km": [float(x) for x in back.r[-1]],
                                               "v_kms": [float(x) for x in back.v[-1]], "source": "synthetic"}}


def write_maneuver_cases():
    """maneuver_cases.json：以 maneuver_leo.json 為底，ops（set／del，同 schema_cases）產生 L6 驗收變體。"""
    from . import maneuver
    sc = json.loads((TESTDATA / "maneuver_leo.json").read_text(encoding="utf-8"))
    base = maneuver.run(sc, load_snapshot())
    b = base["best"]
    trap = _trap_object(sc, b["burn_tau_s"], 1 if b["direction"] == "+T" else -1, b["dv_ms"],
                        base["reference"]["handover_tau_s"], 25200.0)
    objs = sc["maneuver_search"]["secondary_screening"]["objects"] + [trap]
    demo = lambda sig: {"mode": "demo", "sigma_km": sig, "source": "assumed", "independent_of_other": True}  # noqa: E731
    cases = [
        {"name": "base_feasible", "ops": []},
        {"name": "notice_window_conflict", "ops": [{"op": "set", "path": ["analysis_time"], "value": "2026-11-20T05:40:00Z"}]},
        {"name": "window_before_data_start", "ops": [
            {"op": "set", "path": ["analysis_time"], "value": "2026-11-18T00:00:00Z"},
            {"op": "set", "path": ["objects", 0, "propulsion", "min_notice_s"], "value": 0.0},
            {"op": "set", "path": ["maneuver_search", "window_rel_tca_s"], "value": [-90000.0, -86400.0]}]},
        {"name": "dv_budget_below_min", "ops": [{"op": "set", "path": ["objects", 0, "propulsion", "dv_budget_ms"], "value": 0.005}]},
        {"name": "no_along_track_thrust", "ops": [{"op": "set", "path": ["objects", 0, "propulsion", "thrust_dirs"], "value": ["R", "N"]}]},
        {"name": "no_solution_small_dv", "ops": [{"op": "set", "path": ["maneuver_search", "dv_ms"], "value": [0.001, 0.01]}]},
        {"name": "no_maneuver_required", "ops": [{"op": "set", "path": ["maneuver_search", "pc_target"], "value": 1e-4},
                                                 {"op": "set", "path": ["maneuver_search", "min_miss_km"], "value": 0.1}]},
        {"name": "target_not_found", "ops": [{"op": "set", "path": ["conjunction", "tca"], "value": "2026-11-20T03:00:00Z"}]},
        {"name": "demo_covariance_miss_only", "ops": [
            {"op": "set", "path": ["objects", 0, "covariance"], "value": demo([0.1, 0.5, 0.1])},
            {"op": "set", "path": ["objects", 1, "covariance"], "value": demo([0.2, 1.0, 0.2])}]},
        {"name": "new_conjunction_rejects_best", "ops": [
            {"op": "set", "path": ["maneuver_search", "secondary_screening", "objects"], "value": objs}]},
    ]
    out = {"note": "以 maneuver_leo.json 為底，依序套用 ops（set 設值、del 刪除；path 為鍵或索引）。"
                   "expected 為 Python 參考輸出摘要：status、reason、best（機動時刻 τ、方向、Δv）、驗證數與通過數。",
           "base": "maneuver_leo.json", "cases": []}
    for c in cases:
        doc = json.loads(json.dumps(sc))
        for op in c["ops"]:
            tgt = doc
            for k in op["path"][:-1]:
                tgt = tgt[k]
            if op["op"] == "set":
                tgt[op["path"][-1]] = op["value"]
            else:
                del tgt[op["path"][-1]]
        rep = maneuver.run(doc, load_snapshot())
        bb = rep.get("best")
        c["expected"] = {"status": rep["status"], "reason": rep.get("reason"),
                         "best": ({"burn_tau_s": bb["burn_tau_s"], "direction": bb["direction"], "dv_ms": bb["dv_ms"],
                                   "passed": bb["passed"]} if bb else None),
                         "verified": (rep.get("searched") or {}).get("verified"),
                         "passed": (rep.get("searched") or {}).get("passed")}
        print(c["name"], c["expected"])
        out["cases"].append(c)
    (TESTDATA / "maneuver_cases.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    base.pop("timing", None)
    (TESTDATA / "maneuver_leo.expected.json").write_text(json.dumps(base, indent=1, ensure_ascii=False), encoding="utf-8")
    print("寫入 maneuver_cases.json、maneuver_leo.expected.json")
