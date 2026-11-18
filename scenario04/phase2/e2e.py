"""場景端到端：場景 JSON → 驗證 → 星曆（SGP4 或數值，GCRF）→ 自適應近接搜尋 → 協方差（展示或傳播模式）
→ 適用性篩選 → Foster／Chan Pc → 報告（JSON＋Markdown）。

screening.propagator：auto（TLE 用 SGP4、狀態向量用數值積分；預設）、sgp4（全部 SGP4，僅限 TLE）、
numerical（全部數值積分；TLE 物件先以 SGP4 取 start 時刻狀態）。
"""
from __future__ import annotations

import numpy as np

from . import constants as K
from .covprop import CovarianceTrack
from .eop import Eop
from .ephemeris import NumericalEphemeris, RelativeState, Sgp4Ephemeris
from .events import build_event
from .forces import ExponentialAtmosphere, ForceModel
from .frames import EarthRotationTable, earth_rotation, teme_to_gcrf
from .integrator import propagate
from .rtn import cov_rtn_to_gcrf, rtn_matrix
from .schema import validate
from .tca import find_minima_adaptive
from .timescale import UtcTime

CODE_VERSION = "phase2-sprint1"


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
    e, r, v = sat.sgp4(K.MJD0 + start.mjd, start.sec / K.DAY_S)
    if e != 0:
        raise ValueError(f"SGP4 錯誤碼 {e}（{obj['name']}）")
    rg, vg = teme_to_gcrf(np.array(r), np.array(v), earth_rotation(start, eop))
    return rg, vg, 0.0


def _object_covariance(obj, eph, t0, dur, ert, zonal, regime_geo):
    """回傳 (c3_at, mode, source, describe, track)；無協方差則全為 None。track 僅傳播模式有值。"""
    cov = obj.get("covariance")
    if not cov:
        return None, None, None, None, None
    if cov["mode"] == "demo":
        s2 = np.square(np.asarray(cov["sigma_km"], float))

        def f(tau):
            r, v = eph.rv(tau)
            C = rtn_matrix(r, v)
            return C.T @ np.diag(s2) @ C
        return f, "demo", cov["source"], {"method": "demo", "sigma_km": cov["sigma_km"]}, None
    P0 = np.array(cov["matrix_6x6"], float)
    r0, v0 = eph.rv(t0)
    if cov["frame"] == "RTN":
        P0 = cov_rtn_to_gcrf(P0, r0, v0, cov["velocity_convention"])
    q = float(((cov.get("propagation") or {}).get("process_noise") or {}).get("q_km2_s3", 0.0))
    step = K.COV_STEP_GEO_S if regime_geo else K.COV_STEP_LEO_S
    track = CovarianceTrack(eph, P0, t0, dur, step, ert, zonal=zonal, q_km2_s3=q)
    return (lambda tau: track.at(tau)[:3, :3]), "propagated", cov["source"], track.describe(), track


def object_force_model(o: dict, fm_cfg: dict) -> ForceModel:
    return ForceModel(zonal=fm_cfg["zonal"], drag=fm_cfg["drag"],
                      ballistic_m2_kg=float(o.get("cd", 0.0)) * float(o.get("area_to_mass_m2kg", 0.0)),
                      atmos=ExponentialAtmosphere())


def build_context(sc: dict, eop: Eop) -> dict:
    """驗證並建立星曆、協方差。成功回傳 {"ok": True, ...}；否則回傳 invalid_scenario 報告。"""
    val = validate(sc)
    if not val["ok"]:
        return {"status": "invalid_scenario", "validation": val}
    scr_cfg = sc["screening"]
    start = UtcTime.parse(scr_cfg["start"])
    dur = float(scr_cfg["duration_s"])
    objs = {o["role"]: o for o in sc["objects"]}
    regimes = {o.get("orbit_regime", "LEO") for o in sc["objects"]}
    geo = regimes == {"GEO"}
    step = float(scr_cfg.get("step_s") or (K.RK4_STEP_GEO_S if geo else K.RK4_STEP_LEO_S))
    coarse = float(scr_cfg.get("coarse_step_s") or K.SEARCH_COARSE_STEP_S)
    min_step = float(scr_cfg.get("min_step_s") or K.SEARCH_MIN_STEP_S)
    mode = scr_cfg.get("propagator", "auto")
    fm_cfg = sc["force_model"]

    t_lo = 0.0
    for o in sc["objects"]:
        if o["ephemeris"]["type"] == "state":
            t_lo = min(t_lo, _seconds(start, UtcTime.parse(o["ephemeris"]["epoch"])))
            if _seconds(start, UtcTime.parse(o["ephemeris"]["epoch"])) > 0:
                return {"status": "invalid_scenario",
                        "validation": {"ok": False, "codes": ["E_STATE_EPOCH_AFTER_START"], "messages": []}}
    ert = EarthRotationTable(start, t_lo - 1.0, dur + 1.0, eop)

    ephs, prov_eph, covs = {}, {}, {}
    for role, o in objs.items():
        use_sgp4 = o["ephemeris"]["type"] == "TLE" and mode in ("auto", "sgp4")
        if use_sgp4:
            ephs[role] = Sgp4Ephemeris(o["ephemeris"]["line1"], o["ephemeris"]["line2"], start, ert, (0.0, dur))
            prov_eph[role] = "SGP4 (WGS-72, opsmode i) → GCRF"
            t0 = 0.0
        else:
            r0, v0, t0 = initial_state_gcrf(o, start, eop)
            fm = object_force_model(o, fm_cfg)
            ephs[role] = NumericalEphemeris(propagate(r0, v0, t0, dur, step, fm, ert))
            prov_eph[role] = f"numerical RK4 {step:g} s, {fm.describe()}"
        covs[role] = _object_covariance(o, ephs[role], t0, dur, ert, fm_cfg["zonal"], geo)
    return {"ok": True, "sc": sc, "start": start, "dur": dur, "objs": objs, "geo": geo, "step": step,
            "coarse": coarse, "min_step": min_step, "mode": mode, "fm_cfg": fm_cfg, "t_lo": t_lo, "ert": ert,
            "ephs": ephs, "prov_eph": prov_eph, "covs": covs, "eop": eop, "threshold_km": float(scr_cfg["threshold_km"])}


def combined_covariance(cp, cs):
    """回傳 (cov_mode, c3_at)；任一物體缺協方差則 (None, None)。cp、cs 為 _object_covariance 的回傳值。"""
    if cp[0] is None or cs[0] is None:
        return None, None
    cov_mode = "propagated" if "propagated" in (cp[1], cs[1]) else "demo"
    if cp[1] != cs[1]:
        cov_mode = "mixed"
    return cov_mode, (lambda tau: cp[0](tau) + cs[0](tau))


def run(sc: dict, eop: Eop) -> dict:
    ctx = build_context(sc, eop)
    if not ctx.get("ok"):
        return ctx
    return screen_report(ctx)


def screen_report(ctx: dict) -> dict:
    sc, start, dur, objs, ephs, covs = ctx["sc"], ctx["start"], ctx["dur"], ctx["objs"], ctx["ephs"], ctx["covs"]
    coarse, min_step, mode, prov_eph = ctx["coarse"], ctx["min_step"], ctx["mode"], ctx["prov_eph"]
    rel = RelativeState(ephs["primary"], ephs["secondary"])
    thr = ctx["threshold_km"]
    res = find_minima_adaptive(rel.rv, rel.rva, 0.0, dur, coarse, thr, min_step)
    hbr_km = (objs["primary"]["hbr_m"] + objs["secondary"]["hbr_m"]) / 1000.0
    others = [m.tau for m in res["all_minima"]]
    cp, cs = covs["primary"], covs["secondary"]
    cov_mode, c3_at = combined_covariance(cp, cs)
    have_cov = c3_at is not None
    screen_mode = "propagated" if cov_mode in ("propagated", "mixed") else "demo"
    events = [build_event(start, ap, rel, ephs["primary"], c3_at, screen_mode, hbr_km, others, (0.0, dur),
                          [cp[2], cs[2]] if have_cov else None) for ap in res["events"]]
    for e in events:
        if e.get("pc"):
            e["pc"]["covariance_mode"] = cov_mode

    gmin = res["global_min"]
    n_search_evals = rel.n_evals
    n_series = int(min(600, max(2, dur / coarse + 1)))
    s_step = dur / (n_series - 1)
    series = {"step_s": s_step, "d_km": [float(np.linalg.norm(rel.rv(i * s_step)[0])) for i in range(n_series)]}
    return {
        "status": "ok",
        "scenario_id": sc["id"],
        "start": start.iso(), "duration_s": dur, "threshold_km": thr,
        "objects": {r: {"name": o["name"], "norad_id": o.get("norad_id")} for r, o in objs.items()},
        "series": series,
        "provenance": {"code_version": CODE_VERSION, "constants_version": K.CONSTANTS_VERSION,
                       "eop": ctx["eop"].version(),
                       "frames": {"sgp4_output": "TEME", "propagation": "GCRF",
                                  "precession_nutation": "IAU-2006/2000A CIO (xy06+s06), dX/dY applied"},
                       "propagator_mode": mode, "ephemeris": prov_eph,
                       "covariance": {"primary": cp[3], "secondary": cs[3]}},
        "search": {**res["resolution"], "n_minima": len(res["all_minima"]), "n_evals": n_search_evals,
                   "global_min": ({"tca_utc": start.plus(gmin.tau).iso(), "miss_km": gmin.miss_km, "flag": gmin.flag}
                                  if gmin else None)},
        "events": events,
    }


def to_markdown(rep: dict) -> str:
    if rep.get("status") != "ok":
        return f"# 場景無效\n\n錯誤代碼：{', '.join(rep['validation']['codes'])}\n"
    p = rep["provenance"]
    s = rep["search"]
    lines = [f"# 端到端報告：{rep['scenario_id']}", "",
             f"- 程式版本：{p['code_version']}；常數：{p['constants_version']}",
             f"- EOP：{p['eop']['file']}（sha256 {p['eop']['sha256']}，最後觀測值 {p['eop']['last_observed']}）",
             f"- 星曆：主星 {p['ephemeris']['primary']}；次星 {p['ephemeris']['secondary']}",
             f"- 搜尋：自適應，粗掃 {s['coarse_step_s']} s、最小步長 {s['min_step_s']} s、門檻 {s['threshold_km']} km；"
             f"極小值 {s['n_minima']} 個", "",
             "| TCA (UTC) | 距離 km | 相對速度 km/s | 適用性 | Pc Foster | Pc Chan |",
             "|---|---|---|---|---|---|"]
    for e in rep["events"]:
        pc = e.get("pc") or {}
        f = pc.get("foster")
        c = pc.get("chan")
        lines.append(f"| {e['tca_utc']} | {e['miss_km']:.4f} | {e['v_rel_kms']:.4f} | "
                     f"{e['applicability']['status']} | {'—' if f is None else f'{f:.3e}'} | {'—' if c is None else f'{c:.3e}'} |")
    lines += ["", "註：TCA 與距離的位數代表求根精度，不代表預報準確度；Pc 依所列協方差假設計算。"]
    return "\n".join(lines) + "\n"
