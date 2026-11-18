"""最小端到端案例（Sprint 0 出場條件第 3 項）：

場景 JSON → 驗證 → 初始狀態（state 或 TLE→SGP4→TEME）→ GCRF → RK4 數值傳播（二體＋J2–J4［＋阻力］）
→ 近接極小值搜尋 → 固定協方差（展示模式）→ 適用性篩選 → Foster／Chan Pc → 報告（JSON＋Markdown）。
"""
from __future__ import annotations

import math

import numpy as np

from . import constants as K
from .eop import Eop
from .forces import ExponentialAtmosphere, ForceModel
from .frames import EarthRotationTable, earth_rotation, teme_to_gcrf
from .integrator import Trajectory, propagate
from .pc import pc_chan, pc_foster, project
from .rtn import rtn_matrix
from .schema import validate
from .screening import screen
from .tca import find_minima
from .timescale import UtcTime

CODE_VERSION = "phase2-sprint0"


def _seconds(a: UtcTime, b: UtcTime) -> float:
    return (b.mjd - a.mjd) * K.DAY_S + (b.sec - a.sec)


def initial_state_gcrf(obj: dict, start: UtcTime, eop: Eop):
    """回傳 (r, v, τ0)：GCRF 初始狀態與其相對 screening.start 的時刻。TLE 物件在 start 時刻以 SGP4 取狀態。"""
    eph = obj["ephemeris"]
    if eph["type"] == "state":
        ep = UtcTime.parse(eph["epoch"])
        r, v = np.array(eph["r_km"], float), np.array(eph["v_kms"], float)
        if eph["frame"] == "TEME":
            r, v = teme_to_gcrf(r, v, earth_rotation(ep, eop))
        return r, v, _seconds(start, ep)
    from sgp4.api import Satrec
    sat = Satrec.twoline2rv(eph["line1"], eph["line2"])
    jd, fr = K.MJD0 + start.mjd, start.sec / K.DAY_S
    e, r, v = sat.sgp4(jd, fr)
    if e != 0:
        raise ValueError(f"SGP4 錯誤碼 {e}（{obj['name']}）")
    rg, vg = teme_to_gcrf(np.array(r), np.array(v), earth_rotation(start, eop))
    return rg, vg, 0.0


def _demo_cov_gcrf(obj: dict, r, v) -> np.ndarray | None:
    cov = obj.get("covariance")
    if not cov or cov["mode"] != "demo":
        return None
    C = rtn_matrix(r, v)
    return C.T @ np.diag(np.square(cov["sigma_km"])) @ C


def run(sc: dict, eop: Eop) -> dict:
    val = validate(sc)
    if not val["ok"]:
        return {"status": "invalid_scenario", "validation": val}
    start = UtcTime.parse(sc["screening"]["start"])
    dur = float(sc["screening"]["duration_s"])
    objs = {o["role"]: o for o in sc["objects"]}
    regimes = {o.get("orbit_regime", "LEO") for o in sc["objects"]}
    step = float(sc["screening"].get("step_s") or (K.RK4_STEP_GEO_S if regimes == {"GEO"} else K.RK4_STEP_LEO_S))
    fm_cfg = sc["force_model"]

    init = {role: initial_state_gcrf(o, start, eop) for role, o in objs.items()}
    t_lo = min(min(t0 for _, _, t0 in init.values()), 0.0)
    if any(t0 > 0 for _, _, t0 in init.values()):
        return {"status": "invalid_scenario",
                "validation": {"ok": False, "codes": ["E_STATE_EPOCH_AFTER_START"], "messages": []}}
    ert = EarthRotationTable(start, t_lo, dur, eop) if (fm_cfg["zonal"] or fm_cfg["drag"]) else None

    traj: dict[str, Trajectory] = {}
    for role, (r0, v0, t0) in init.items():
        o = objs[role]
        fm = ForceModel(zonal=fm_cfg["zonal"], drag=fm_cfg["drag"],
                        ballistic_m2_kg=float(o.get("cd", 0.0)) * float(o.get("area_to_mass_m2kg", 0.0)),
                        atmos=ExponentialAtmosphere())
        traj[role] = propagate(r0, v0, t0, dur, step, fm, ert)

    P, S = traj["primary"], traj["secondary"]

    def rel(tau):
        rp, vp, ap = P.state(tau)
        rs, vs, as_ = S.state(tau)
        return rs - rp, vs - vp, as_ - ap

    thr = float(sc["screening"]["threshold_km"])
    res = find_minima(rel, 0.0, dur, step, thr)
    hbr_km = (objs["primary"]["hbr_m"] + objs["secondary"]["hbr_m"]) / 1000.0
    other = [m.tau for m in res["all_minima"]]

    def c3_at(tau):
        rp, vp, _ = P.state(tau)
        rs, vs, _ = S.state(tau)
        cp = _demo_cov_gcrf(objs["primary"], rp, vp)
        cs = _demo_cov_gcrf(objs["secondary"], rs, vs)
        if cp is None or cs is None:
            return None
        return cp + cs

    have_cov = c3_at(0.0) is not None
    events = []
    for ev in res["events"]:
        rp, vp, _ = P.state(ev.tau)
        a_semi = 1.0 / (2.0 / np.linalg.norm(rp) - np.dot(vp, vp) / K.GM_KM3_S2)
        period = 2 * math.pi * math.sqrt(a_semi ** 3 / K.GM_KM3_S2)
        C = rtn_matrix(rp, vp)
        out = {
            "tca_utc": start.plus(ev.tau).iso(), "tca_tau_s": ev.tau, "flag": ev.flag,
            "miss_km": ev.miss_km, "v_rel_kms": float(np.linalg.norm(ev.v_rel)),
            "miss_rtn_km": [float(x) for x in C @ ev.r_rel],
            "approach_angle_deg": float(math.degrees(math.acos(max(-1.0, min(1.0,
                np.dot(vp, vp + ev.v_rel) / (np.linalg.norm(vp) * np.linalg.norm(vp + ev.v_rel))))))),
            "accuracy_note": "TCA 與距離為數值解（求根容差 1e-6 s）；預報準確度受輸入狀態與模型不確定性限制",
        }
        scr = screen(rel, ev.tau, c3_at if have_cov else None, "demo", hbr_km, period, other, (0.0, dur))
        out["applicability"] = scr
        if have_cov:
            C3 = c3_at(ev.tau)
            miss2, C2, _F = project(ev.r_rel, ev.v_rel, C3)
            out["bplane"] = {"miss_km": [float(x) for x in miss2], "cov_km2": C2.tolist()}
            out["pc"] = {"foster": pc_foster(miss2, C2, hbr_km), "chan": pc_chan(miss2, C2, hbr_km),
                         "hbr_km": hbr_km, "covariance_mode": "demo",
                         "covariance_source": [objs[k]["covariance"]["source"] for k in ("primary", "secondary")],
                         "interpretation": ("通過目前啟發式篩選；Pc 為展示模式假設下的值"
                                            if scr["status"] == "passes_initial_screen" else
                                            "未通過或無法判定適用性篩選；二維 Pc 僅供參考")}
        events.append(out)

    gmin = res["global_min"]
    return {
        "status": "ok",
        "scenario_id": sc["id"],
        "provenance": {"code_version": CODE_VERSION, "constants_version": K.CONSTANTS_VERSION,
                       "eop": eop.version(), "frames": {"sgp4_output": "TEME", "propagation": "GCRF",
                                                         "precession_nutation": "IAU-2006/2000A CIO (xy06+s06), dX/dY applied"},
                       "integrator": {"method": "RK4 fixed-step", "step_s": step,
                                      "interpolation": "quintic Hermite (r, v, a)"},
                       "force_model": ForceModel(zonal=fm_cfg["zonal"], drag=fm_cfg["drag"]).describe()},
        "search": {**res["resolution"], "n_minima": len(res["all_minima"]),
                   "global_min": ({"tca_utc": start.plus(gmin.tau).iso(), "miss_km": gmin.miss_km, "flag": gmin.flag}
                                  if gmin else None)},
        "events": events,
    }


def to_markdown(rep: dict) -> str:
    if rep.get("status") != "ok":
        return f"# 場景無效\n\n錯誤代碼：{', '.join(rep['validation']['codes'])}\n"
    p = rep["provenance"]
    lines = [f"# 端到端報告：{rep['scenario_id']}", "",
             f"- 程式版本：{p['code_version']}；常數：{p['constants_version']}",
             f"- EOP：{p['eop']['file']}（sha256 {p['eop']['sha256']}，最後觀測值 {p['eop']['last_observed']}）",
             f"- 積分：{p['integrator']['method']}，步長 {p['integrator']['step_s']} s；力模型：{p['force_model']}",
             f"- 搜尋：節點間距 {rep['search']['node_step_s']} s、門檻 {rep['search']['threshold_km']} km、"
             f"極小值 {rep['search']['n_minima']} 個", "",
             "| TCA (UTC) | 距離 km | 相對速度 km/s | 適用性 | Pc Foster | Pc Chan |",
             "|---|---|---|---|---|---|"]
    for e in rep["events"]:
        pc = e.get("pc") or {}
        lines.append(f"| {e['tca_utc']} | {e['miss_km']:.4f} | {e['v_rel_kms']:.4f} | "
                     f"{e['applicability']['status']} | {pc.get('foster', float('nan')):.3e} | {pc.get('chan', float('nan')):.3e} |")
    lines += ["", "註：Pc 為展示模式（固定 RTN σ、忽略相關項）下的值；TCA 求根精度不代表預報準確度。"]
    return "\n".join(lines) + "\n"
