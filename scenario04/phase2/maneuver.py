"""避碰決策（v0.3 Sprint 2）：沿跡向單脈衝避碰方案搜尋。

流程
  1. 基準篩選（e2e.screen_report）→ 目標事件：conjunction.tca 附近（≤ 0.25 週期）的事件；未指定時取 Pc 最大
     （無 Pc 時取距離最小）的事件。
  2. 可行性預檢（相對 TCA 的機動窗、最短通知時間、Δv 預算、沿跡向推力）→ infeasible_constraints＋原因。
  3. 參考軌跡 N0：在可用機動窗起點 τ_h 取主星 GCRF 狀態（TLE 物體經 SGP4→GCRF），以數值積分傳播到篩選窗終點。
     所有候選方案自 N0 的節點分岔，機動前後以同一數值模型比較（規劃第 2 節）。
  4. 線性預篩：沿 N0 積分 STM Φ(t, τ_h)（二體＋J2–J4 重力梯度，不含阻力偏導數），
     Δr_p(TCA) ≈ Φ_rv(TCA, t_b) · T̂ · Δv，投影到參考 TCA 的 B-plane；Pc 以小 HBR 近似
     HBR²/(2√det C₂)·exp(−m²/2) 預測（m 為 Mahalanobis 距離）。網格：n_times 機動時刻 × n_dv 量級 × ±T。
  5. 非線性驗證：每個機動時刻取線性預測達標的最小 Δv，依 Δv 由小到大驗證至多 max_verify 個：
     重新傳播 → 重新搜尋 TCA → 協方差沿候選軌跡傳播（含執行誤差）→ 適用性篩選與 Pc → 二次篩選。
  6. 狀態：feasible_solution_found／no_solution_found／infeasible_constraints；預檢另有
     no_maneuver_required（參考事件已達標）與 target_not_found。

Pc 目標只在兩物體皆為傳播模式協方差時採用（展示模式不作機動前後比較，規劃第 4 節）；否則以距離目標判定。
機動時刻對齊共同網格 g = step·⌈cov_step/step⌉，使 N0 節點、協方差節點與 STM 節點重合。
"""
from __future__ import annotations

import math
import time

import numpy as np

from . import constants as K
from . import e2e
from .covprop import CovarianceTrack, gravity_gradient
from .eop import Eop
from .ephemeris import NumericalEphemeris, RelativeState, Sgp4Ephemeris
from .events import build_event
from .frames import EarthRotationTable, earth_rotation, teme_to_gcrf
from .integrator import propagate
from .pc import encounter_frame
from .rtn import rtn_matrix
from .tca import find_minima_adaptive
from .timescale import UtcTime

CODE_VERSION = "phase2-sprint2"
STATUSES = ("feasible_solution_found", "no_solution_found", "infeasible_constraints",
            "no_maneuver_required", "target_not_found")


class PiecewiseEphemeris:
    """τ < t_split 用 before，τ ≥ t_split 用 after（機動後軌跡）。"""
    kind = "numerical"

    def __init__(self, before, after, t_split: float):
        self.b, self.a, self.ts = before, after, t_split
        self.span = (before.span[0], after.span[1])

    def rv(self, tau):
        return (self.a if tau >= self.ts else self.b).rv(tau)

    def rva(self, tau):
        return (self.a if tau >= self.ts else self.b).rva(tau)


def _osc(r, v) -> dict:
    rn, v2 = float(np.linalg.norm(r)), float(np.dot(v, v))
    a = 1.0 / (2.0 / rn - v2 / K.GM_KM3_S2)
    h = np.cross(r, v)
    e = math.sqrt(max(0.0, 1.0 - float(np.dot(h, h)) / (K.GM_KM3_S2 * a)))
    return {"a_km": a, "e": e, "period_s": 2 * math.pi * math.sqrt(abs(a) ** 3 / K.GM_KM3_S2),
            "rp_km": a * (1 - e), "ra_km": a * (1 + e)}


def _seconds(a: UtcTime, b: UtcTime) -> float:
    return (b.mjd - a.mjd) * K.DAY_S + (b.sec - a.sec)


def _pc_lin(m2, C2, hbr_km):
    """小 HBR 近似：HBR²/(2√det C₂)·exp(−½ mᵀC₂⁻¹m)。C2 缺或非正定時 None。"""
    if C2 is None:
        return None
    a, b, c = C2[0][0], C2[0][1], C2[1][1]
    det = a * c - b * b
    if not det > 0:
        return None
    q = (c * m2[0] * m2[0] - 2 * b * m2[0] * m2[1] + a * m2[1] * m2[1]) / det
    return hbr_km * hbr_km / (2.0 * math.sqrt(det)) * math.exp(-0.5 * q)


class _Stm:
    """沿星曆積分 Φ(t, t0)：dΦ/dt = A Φ，A = [[0, I], [G, 0]]；RK4 固定步長 h，節點 t0 + k·h。"""

    def __init__(self, eph, t0, h, ert, zonal):
        self.eph, self.t0, self.h, self.ert, self.zonal = eph, t0, h, ert, zonal
        self.Phi = [np.eye(6)]
        self._G = {}

    def _grad(self, tau):
        G = self._G.get(tau)
        if G is None:
            r, _ = self.eph.rv(tau)
            G = self._G[tau] = gravity_gradient(r, self.ert, tau, self.zonal)
        return G

    def _deriv(self, tau, P):
        out = np.zeros((6, 6))
        out[:3] = P[3:]
        out[3:] = self._grad(tau) @ P[:3]
        return out

    def _step(self, tau, P, h):
        k1 = self._deriv(tau, P)
        k2 = self._deriv(tau + 0.5 * h, P + 0.5 * h * k1)
        k3 = self._deriv(tau + 0.5 * h, P + 0.5 * h * k2)
        k4 = self._deriv(tau + h, P + h * k3)
        return P + h / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)

    def node(self, k):
        while len(self.Phi) <= k:
            j = len(self.Phi) - 1
            self.Phi.append(self._step(self.t0 + j * self.h, self.Phi[j], self.h))
        return self.Phi[k]

    def at(self, tau):
        k = int(math.floor((tau - self.t0) / self.h + 1e-9))
        P = self.node(k)
        dt = tau - (self.t0 + k * self.h)
        return P if abs(dt) < 1e-9 else self._step(self.t0 + k * self.h, P, dt)


def _infeasible(base_info: dict, reason: str, detail: dict) -> dict:
    return {**base_info, "status": "infeasible_constraints", "reason": reason, "feasibility": detail}


def _exec_error_cov(u, dv_kms, err: dict | None) -> np.ndarray:
    """機動執行誤差（GCRF 速度協方差）：大小誤差沿推力方向 σ = frac·Δv，指向誤差垂直方向 σ = Δv·θ。"""
    if not err or not err.get("included"):
        return np.zeros((3, 3))
    sp = float(err.get("mag_frac_sigma", 0.0)) * dv_kms
    sq = dv_kms * math.radians(float(err.get("point_deg_sigma", 0.0)))
    uu = np.outer(u, u)
    return sp * sp * uu + sq * sq * (np.eye(3) - uu)


def _catalog_ephemeris(o: dict, start: UtcTime, dur: float, step: float, fm_cfg: dict, ert, eop: Eop):
    eph = o["ephemeris"]
    if eph["type"] == "TLE":
        return Sgp4Ephemeris(eph["line1"], eph["line2"], start, ert, (0.0, dur))
    ep = UtcTime.parse(eph["epoch"])
    t0 = _seconds(start, ep)
    if t0 > 0:
        raise ValueError(f"二次篩選物體 {o['name']} 的狀態曆元晚於 screening.start")
    r, v = np.array(eph["r_km"], float), np.array(eph["v_kms"], float)
    if eph["frame"] == "TEME":
        r, v = teme_to_gcrf(r, v, earth_rotation(ep, eop))
    fm = e2e.object_force_model({**o, "cd": 0.0}, {**fm_cfg, "drag": False})
    return NumericalEphemeris(propagate(r, v, t0, dur, step, fm, ert))


def run(sc: dict, eop: Eop) -> dict:
    t_wall = time.perf_counter()
    val_ctx = e2e.build_context(sc, eop)
    if not val_ctx.get("ok"):
        v = val_ctx.get("validation") or {}
        if set(v.get("codes") or []) == {"E_NOTICE_WINDOW_CONFLICT"}:
            return {"status": "infeasible_constraints", "reason": "notice_window_conflict",
                    "scenario_id": sc.get("id"), "validation": v,
                    "feasibility": {"note": "最短通知時間與機動窗衝突：沒有合法的機動時刻"}}
        return val_ctx
    ctx = val_ctx
    ms = sc.get("maneuver_search")
    if not ms:
        return {"status": "invalid_scenario",
                "validation": {"ok": False, "codes": ["E_MANEUVER_SEARCH_MISSING"], "messages": ["需要 maneuver_search"]}}

    start, dur, objs, ert0 = ctx["start"], ctx["dur"], ctx["objs"], ctx["ert"]
    step, coarse, min_step, thr = ctx["step"], ctx["coarse"], ctx["min_step"], ctx["threshold_km"]
    fm_cfg = ctx["fm_cfg"]
    prim, secd = objs["primary"], objs["secondary"]
    eph_s = ctx["ephs"]["secondary"]
    cp0, cs0 = ctx["covs"]["primary"], ctx["covs"]["secondary"]
    base = e2e.screen_report(ctx)
    info = {"scenario_id": sc["id"], "start": start.iso(), "duration_s": dur, "threshold_km": thr,
            "objects": base["objects"]}

    # 1. 目標事件
    r0p, v0p = ctx["ephs"]["primary"].rv(0.0)
    period = _osc(r0p, v0p)["period_s"]
    match_w = K.MAN_TARGET_MATCH_FRAC_PERIOD * period
    events = base["events"]
    tgt_tca = (sc.get("conjunction") or {}).get("tca")
    if tgt_tca:
        tau_t = _seconds(start, UtcTime.parse(tgt_tca))
        near = [e for e in events if abs(e["tca_tau_s"] - tau_t) <= match_w]
        target = min(near, key=lambda e: abs(e["tca_tau_s"] - tau_t)) if near else None
        if target is None:
            return {**info, "status": "target_not_found", "reason": "no_event_near_conjunction_tca",
                    "screening": {"n_events": len(events)}}
    else:
        if not events:
            return {**info, "status": "target_not_found", "reason": "no_event_below_threshold",
                    "screening": {"n_events": 0}}
        target = sorted(events, key=lambda e: (-((e.get("pc") or {}).get("foster") or -1.0), e["miss_km"]))[0]
    tca0 = target["tca_tau_s"]

    # 2. 可行性預檢
    prop = prim.get("propulsion") or {}
    a_rel, b_rel = (float(x) for x in ms["window_rel_tca_s"])
    notice = float(prop.get("min_notice_s", 0.0))
    at_tau = _seconds(start, UtcTime.parse(sc["analysis_time"])) if sc.get("analysis_time") else 0.0
    earliest = at_tau + notice
    lo, hi = max(tca0 + a_rel, earliest, 0.0), tca0 + b_rel
    dv_lo, dv_hi = (float(x) for x in (ms.get("dv_ms") or K.MAN_DV_MS_DEFAULT))
    budget = prop.get("dv_budget_ms")
    dirs = prop.get("thrust_dirs")
    signs = [s for s in (1, -1) if dirs is None or ("T" if s > 0 else "-T") in dirs]
    feas = {"target_tca_utc": target["tca_utc"],
            "window_rel_tca_s": [a_rel, b_rel],
            "earliest_allowed_utc": start.plus(earliest).iso(),
            "latest_useful_utc": start.plus(hi).iso(),
            "dv_range_ms": [dv_lo, dv_hi], "dv_budget_ms": budget,
            "thrust_dirs": dirs, "along_track_signs": ["+T" if s > 0 else "-T" for s in signs]}
    info["target"] = target
    if earliest > hi:
        return _infeasible(info, "notice_window_conflict", feas)
    if lo > hi:
        return _infeasible(info, "window_before_data_start", feas)
    if not signs:
        return _infeasible(info, "no_along_track_thrust", feas)
    if budget is not None and float(budget) < dv_lo:
        return _infeasible(info, "dv_budget_below_min_dv", feas)
    dv_hi_eff = min(dv_hi, float(budget)) if budget is not None else dv_hi
    feas["window_utc"] = [start.plus(lo).iso(), start.plus(hi).iso()]
    feas["dv_search_ms"] = [dv_lo, dv_hi_eff]

    # 3. 參考軌跡 N0（自 τ_h = lo 起的數值傳播）
    cov_step = K.COV_STEP_GEO_S if ctx["geo"] else K.COV_STEP_LEO_S
    g = step * math.ceil(cov_step / step - 1e-9)
    # 地球定向表起點涵蓋二次篩選狀態向量物體的曆元（其數值積分自曆元開始）
    t_lo = ctx["t_lo"]
    for o in ((ms.get("secondary_screening") or {}).get("objects") or []):
        if o["ephemeris"]["type"] == "state":
            t_lo = min(t_lo, _seconds(start, UtcTime.parse(o["ephemeris"]["epoch"])))
    ert = EarthRotationTable(start, t_lo - 1.0, dur + 2 * g + 1.0, eop)
    fm_p = e2e.object_force_model(prim, fm_cfg)
    r_h, v_h = ctx["ephs"]["primary"].rv(lo)
    n0_traj = propagate(r_h, v_h, lo, dur, step, fm_p, ert)
    N0 = NumericalEphemeris(n0_traj)
    zonal = fm_cfg["zonal"]

    pcov = prim.get("covariance") or {}
    q = float(((pcov.get("propagation") or {}).get("process_noise") or {}).get("q_km2_s3", 0.0))
    n0_track = None
    if cp0[1] == "propagated":
        n0_track = CovarianceTrack(N0, cp0[4].at(lo), lo, dur, g, ert, zonal=zonal, q_km2_s3=q)

    def primary_cov(eph, track):
        if cp0[0] is None:
            return (None,) * 5
        if cp0[1] == "demo":
            s2 = np.square(np.asarray(pcov["sigma_km"], float))

            def f(tau):
                r, v = eph.rv(tau)
                C = rtn_matrix(r, v)
                return C.T @ np.diag(s2) @ C
            return f, "demo", cp0[2], cp0[3], None
        return (lambda tau: track.at(tau)[:3, :3]), "propagated", cp0[2], track.describe(), track

    cov_mode_ref, _ = e2e.combined_covariance(cp0, cs0)
    comparable = cp0[1] == "propagated" and cs0[1] == "propagated"
    hbr_km = (prim["hbr_m"] + secd["hbr_m"]) / 1000.0
    pc_target = ms.get("pc_target")
    min_miss = ms.get("min_miss_km")
    use_pc = pc_target is not None and comparable
    min_miss_eff = float(min_miss) if min_miss is not None else (None if use_pc else K.MAN_MIN_MISS_DEFAULT_KM)
    reject_km = float(min_miss) if min_miss is not None else K.MAN_MIN_MISS_DEFAULT_KM
    criteria = {"min_miss_km": min_miss_eff, "pc_target": pc_target, "pc_target_used": use_pc,
                "pc_comparable": comparable, "covariance_mode": cov_mode_ref,
                "new_conjunction_reject_km": reject_km,
                "note": ("Pc 目標採用：兩物體皆為傳播模式協方差" if use_pc else
                         "Pc 目標未採用：協方差非雙方傳播模式，機動前後 Pc 不可直接比較，以距離目標判定"
                         if pc_target is not None else "以距離目標判定")}

    def assess(eph_p, track, t_from):
        """回傳 (target_event, other_events_below_threshold, all_minima_taus)；找不到目標回傳 (None, ...)。"""
        rel = RelativeState(eph_p, eph_s)
        res = find_minima_adaptive(rel.rv, rel.rva, t_from, dur, coarse, thr, min_step)
        near = [m for m in res["all_minima"] if abs(m.tau - tca0) <= match_w]
        cp = primary_cov(eph_p, track)
        cov_mode, c3_at = e2e.combined_covariance(cp, cs0)
        smode = "propagated" if cov_mode in ("propagated", "mixed") else "demo"
        others = [m.tau for m in res["all_minima"]]
        srcs = [cp[2], cs0[2]] if c3_at is not None else None
        tgt, ap = None, (min(near, key=lambda m: m.miss_km) if near else None)
        if ap is not None:
            tgt = build_event(start, ap, rel, eph_p, c3_at, smode, hbr_km, others, (t_from, dur), srcs)
            if tgt.get("pc"):
                tgt["pc"]["covariance_mode"] = cov_mode
        rest = [{"tca_utc": start.plus(m.tau).iso(), "tca_tau_s": m.tau, "miss_km": m.miss_km,
                 "v_rel_kms": float(np.linalg.norm(m.v_rel))}
                for m in res["events"] if m is not ap]
        return tgt, rest, res

    ref_tgt, ref_rest, _ = assess(N0, n0_track, lo)
    if ref_tgt is None:
        return {**info, "status": "target_not_found", "reason": "reference_lost", "feasibility": feas}

    def pc_of(ev):
        return (ev.get("pc") or {}).get("foster")

    def meets(ev):
        ok_miss = min_miss_eff is None or ev["miss_km"] >= min_miss_eff
        p = pc_of(ev)
        ok_pc = (not use_pc) or (p is not None and p <= float(pc_target))
        return ok_miss and ok_pc

    reference = {"handover_utc": start.plus(lo).iso(), "handover_tau_s": lo,
                 "model": {"integrator": "RK4", "step_s": step, "force_model": fm_p.describe()},
                 "initial_state_source": ctx["prov_eph"]["primary"], "event": ref_tgt}
    info.update({"feasibility": feas, "criteria": criteria, "reference": reference})
    if meets(ref_tgt):
        return {**info, "status": "no_maneuver_required", "reason": "reference_meets_targets",
                "timing": {"elapsed_s": time.perf_counter() - t_wall}}

    # 4. 線性預篩
    k_max = int(math.floor((hi - lo) / g + 1e-9))
    n_times = int(ms.get("n_times", K.MAN_N_TIMES))
    if n_times <= 1 or k_max == 0:
        idx = [0]
    else:
        idx = sorted({int(round(i * k_max / (n_times - 1))) for i in range(n_times)})
    burn_taus = [lo + k * g for k in idx]
    n_dv = int(ms.get("n_dv", K.MAN_N_DV))
    dv_levels = [dv_hi_eff] if n_dv <= 1 else [dv_lo + i * (dv_hi_eff - dv_lo) / (n_dv - 1) for i in range(n_dv)]

    stm = _Stm(N0, lo, g, ert, zonal)
    Phi_T = stm.at(ref_tgt["tca_tau_s"])
    rel_ref = RelativeState(N0, eph_s)
    r_rel0, v_rel0 = rel_ref.rv(ref_tgt["tca_tau_s"])
    F = encounter_frame(r_rel0, v_rel0)
    m2_ref = F[:2] @ r_rel0
    C2_ref = np.array(ref_tgt["bplane"]["cov_km2"]) if ref_tgt.get("bplane") else None

    def step_index(tau):
        return int(round((tau - lo) / step))

    lin_map, lin_pass = [], []
    for tb in burn_taus:
        Phi_b = stm.at(tb)
        Phi_tb = np.linalg.solve(Phi_b.T, Phi_T.T).T          # Φ(TCA, t_b) = Φ(TCA, τ_h) Φ(t_b, τ_h)⁻¹
        kb = step_index(tb)
        rb, vb = n0_traj.r[kb], n0_traj.v[kb]
        T_hat = rtn_matrix(rb, vb)[1]
        b2 = F[:2] @ (Phi_tb[:3, 3:] @ T_hat)                 # km per km/s
        row = {"burn_tau_s": tb, "burn_utc": start.plus(tb).iso(), "min_dv_ms": {}}
        best = None
        for s in signs:
            key = "+T" if s > 0 else "-T"
            row["min_dv_ms"][key] = None
            for i, dv in enumerate(dv_levels):
                m2 = m2_ref - s * (dv / 1000.0) * b2
                miss_l = float(math.hypot(m2[0], m2[1]))
                pcl = _pc_lin(m2, C2_ref, hbr_km)
                ok = (min_miss_eff is None or miss_l >= min_miss_eff) and \
                     ((not use_pc) or (pcl is not None and pcl <= float(pc_target)))
                if ok:
                    row["min_dv_ms"][key] = dv
                    cand = (i, -miss_l, s, dv, miss_l, pcl)
                    if best is None or cand[:2] < best[:2]:
                        best = cand
                    break
        row["fallback"] = None
        # 最大 Δv 下的預測（無解時挑最佳候選用）
        fb = None
        for s in signs:
            m2 = m2_ref - s * (dv_hi_eff / 1000.0) * b2
            miss_l = float(math.hypot(m2[0], m2[1]))
            pcl = _pc_lin(m2, C2_ref, hbr_km)
            score = (pcl if (use_pc and pcl is not None) else 0.0, -miss_l)
            if fb is None or score < fb[0]:
                fb = (score, s, dv_hi_eff, miss_l, pcl)
        row["fallback"] = fb
        lin_map.append(row)
        if best is not None:
            lin_pass.append({"burn_tau_s": tb, "sign": best[2], "dv_ms": best[3], "lin_miss_km": best[4],
                             "lin_pc": best[5]})

    max_verify = int(ms.get("max_verify", K.MAN_MAX_VERIFY))
    if lin_pass:
        order = sorted(lin_pass, key=lambda c: (c["dv_ms"], -c["lin_miss_km"], c["burn_tau_s"]))[:max_verify]
        selection = "linear_pass"
    else:
        fbs = sorted(lin_map, key=lambda r: (r["fallback"][0], r["burn_tau_s"]))[:min(3, max_verify)]
        order = [{"burn_tau_s": r["burn_tau_s"], "sign": r["fallback"][1], "dv_ms": r["fallback"][2],
                  "lin_miss_km": r["fallback"][3], "lin_pc": r["fallback"][4]} for r in fbs]
        selection = "best_at_max_dv"
    for r in lin_map:
        r.pop("fallback", None)

    # 二次篩選目錄
    ss = ms.get("secondary_screening") or {}
    cat_objs = ss.get("objects") or []
    end_s = dur if not ss.get("window_days") else min(dur, lo + float(ss["window_days"]) * K.DAY_S)
    osc_h = _osc(r_h, v_h)
    da_max = 2.0 * osc_h["a_km"] ** 2 * float(np.linalg.norm(v_h)) * (dv_hi_eff / 1000.0) / K.GM_KM3_S2
    pad = thr + K.MAN_PREFILTER_MARGIN_KM + da_max
    catalog = []
    for o in cat_objs:
        eph_o = _catalog_ephemeris(o, start, dur, step, fm_cfg, ert, eop)
        ro, vo = eph_o.rv(lo)
        oo = _osc(ro, vo)
        keep = not (oo["rp_km"] > osc_h["ra_km"] + pad or oo["ra_km"] < osc_h["rp_km"] - pad)
        catalog.append({"name": o["name"], "norad_id": o.get("norad_id"), "eph": eph_o, "keep": keep})
    kept = [c for c in catalog if c["keep"]]
    for c in kept:
        c["ref_events"] = []
        if end_s > lo:
            rel = RelativeState(N0, c["eph"])
            res = find_minima_adaptive(rel.rv, rel.rva, lo, end_s, coarse, thr, min_step)
            c["ref_events"] = [(m.tau, m.miss_km) for m in res["events"]]

    def classify(ev: dict, ref_list, tb) -> dict | None:
        """機動後事件對照參考事件（同物體、TCA 相差 ≤ 0.25 週期）：new（無對應）、worsened（距離變小），其餘不列。"""
        refs = [(t, m) for t, m in ref_list if t >= tb - match_w and abs(ev["tca_tau_s"] - t) <= match_w]
        if not refs:
            return {**ev, "kind": "new", "ref_miss_km": None}
        rm = min(m for _, m in refs)
        return {**ev, "kind": "worsened", "ref_miss_km": rm} if ev["miss_km"] < rm else None

    def new_events(eph_c, tb):
        out = []
        if end_s <= tb:      # 二次篩選時間窗在機動之前即結束：無可檢查的區間
            return out
        for c in kept:
            rel = RelativeState(eph_c, c["eph"])
            res = find_minima_adaptive(rel.rv, rel.rva, tb, end_s, coarse, thr, min_step)
            for m in res["events"]:
                ev = classify({"object": c["name"], "norad_id": c["norad_id"],
                               "tca_utc": start.plus(m.tau).iso(), "tca_tau_s": m.tau, "miss_km": m.miss_km,
                               "v_rel_kms": float(np.linalg.norm(m.v_rel))}, c["ref_events"], tb)
                if ev is not None:
                    out.append(ev)
        return out

    exec_err = pcov.get("maneuver_execution_error")

    # 5. 非線性驗證
    verified, eph_of = [], {}
    for c in order:
        tb, s, dv = c["burn_tau_s"], c["sign"], c["dv_ms"]
        kb = step_index(tb)
        rb, vb = n0_traj.r[kb].copy(), n0_traj.v[kb].copy()
        u = s * rtn_matrix(rb, vb)[1]
        vn = vb + u * (dv / 1000.0)
        after = NumericalEphemeris(propagate(rb, vn, tb, dur, step, fm_p, ert))
        eph_c = PiecewiseEphemeris(N0, after, tb)
        track_c = None
        if n0_track is not None:
            Pb = n0_track.at(tb).copy()
            Pb[3:, 3:] += _exec_error_cov(u, dv / 1000.0, exec_err)
            track_c = CovarianceTrack(eph_c, Pb, tb, dur, g, ert, zonal=zonal, q_km2_s3=q)
        tgt, rest, _ = assess(eph_c, track_c, tb)
        o_b, o_a = _osc(rb, vb), _osc(rb, vn)
        mass, isp = prim.get("mass_kg"), prop.get("isp_s")
        cand = {"burn_utc": start.plus(tb).iso(), "burn_tau_s": tb, "burn_rel_tca_s": tb - tca0,
                "direction": "+T" if s > 0 else "-T", "dv_ms": dv,
                "linear": {"miss_km": c["lin_miss_km"], "pc_approx": c["lin_pc"]},
                "delta_a_km": o_a["a_km"] - o_b["a_km"], "delta_period_s": o_a["period_s"] - o_b["period_s"],
                "propellant_kg": (float(mass) * (1.0 - math.exp(-dv / (float(isp) * K.MAN_G0_MS2)))
                                  if mass and isp else None),
                "execution_error_included": bool(exec_err and exec_err.get("included")) and n0_track is not None}
        eph_of[id(cand)] = eph_c
        if tgt is None:
            cand.update({"event": None, "checks": {"target_found": False}, "passed": False,
                         "secondary_screening": {"status": "not_run", "reason": "target_not_found"}})
            verified.append(cand)
            continue
        checks = {"target_found": True}
        if min_miss_eff is not None:
            checks["miss"] = tgt["miss_km"] >= min_miss_eff
        if use_pc:
            p = pc_of(tgt)
            checks["pc"] = p is not None and p <= float(pc_target)
        ref_sec = [(e["tca_tau_s"], e["miss_km"]) for e in ref_rest]
        new_sec = [x for x in (classify({"object": secd["name"], "norad_id": secd.get("norad_id"), **e}, ref_sec, tb)
                               for e in rest) if x is not None]
        cand["event"] = tgt
        cand["d_miss_km"] = tgt["miss_km"] - ref_tgt["miss_km"]
        pa, pr = pc_of(tgt), pc_of(ref_tgt)
        cand["pc_before"], cand["pc_after"] = pr, pa
        cand["pc_comparable"] = comparable
        if all(checks.values()):
            cat_new = new_events(eph_c, tb)
            allnew = new_sec + cat_new
            checks["no_new_conjunction"] = not any(e["miss_km"] < reject_km for e in allnew)
            cand["new_conjunctions"] = allnew
            cand["secondary_screening"] = {"status": "done", "n_new": len(allnew)}
        else:
            cand["new_conjunctions"] = new_sec
            cand["secondary_screening"] = {"status": "not_run", "reason": "target_not_met"}
        cand["checks"] = checks
        cand["passed"] = all(checks.values())
        verified.append(cand)

    passed = [c for c in verified if c["passed"]]

    def rank(c):
        ev = c.get("event")
        return (not c["passed"], c["dv_ms"], -(ev["miss_km"] if ev else -1.0), c["burn_tau_s"])
    verified.sort(key=rank)
    best = verified[0] if verified else None
    status = "feasible_solution_found" if passed else "no_solution_found"
    reason = None
    if not passed:
        reason = "no_linear_candidate_met_targets" if selection == "best_at_max_dv" else "verified_candidates_failed"

    n_cat = len(cat_objs)
    coverage = (f"僅完成所提供 {n_cat} 顆物體的二次篩選（近遠地點預篩後 {len(kept)} 顆逐對搜尋），另含原次星；未涵蓋完整目錄"
                if n_cat else "未提供二次篩選物體清單：僅檢查與原次星的新增交會")

    # 6. 繪圖資料
    eph_best = eph_of[id(best)] if best and best.get("event") else None
    plot = _plot_data(lo, dur, tca0, period, N0, eph_s, ref_tgt, eph_best, best, hbr_km)

    return {
        **info, "status": status, "reason": reason,
        "searched": {"times": len(burn_taus), "dv_levels": len(dv_levels), "signs": feas["along_track_signs"],
                     "dv_search_ms": [dv_lo, dv_hi_eff], "grid_cells": len(burn_taus) * len(dv_levels) * len(signs),
                     "grid_step_s": g, "linear_pass_times": len(lin_pass), "selection": selection,
                     "max_verify": max_verify, "verified": len(verified), "passed": len(passed)},
        "linear_map": lin_map,
        "candidates": verified,
        "best": best,
        "secondary_screening": {"catalog": ss.get("catalog", "provided"), "n_objects": n_cat,
                                "n_after_prefilter": len(kept),
                                "window_utc": [start.plus(lo).iso(), start.plus(end_s).iso()],
                                "coverage_note": coverage},
        "plot": plot,
        "provenance": {"code_version": CODE_VERSION, "constants_version": K.CONSTANTS_VERSION,
                       "eop": eop.version(),
                       "reference_trajectory": reference["model"],
                       "linear_screening": "STM（二體＋J2–J4 重力梯度，不含阻力偏導數）；Pc 為小 HBR 近似",
                       "covariance": base["provenance"]["covariance"]},
        "timing": {"elapsed_s": time.perf_counter() - t_wall},
    }


def _plot_data(lo, dur, tca0, period, N0, eph_s, ref_tgt, eph_best, best, hbr_km) -> dict:
    n = K.MAN_PLOT_POINTS
    ts = [lo + (dur - lo) * i / (n - 1) for i in range(n)]
    d_ref = [float(np.linalg.norm(eph_s.rv(t)[0] - N0.rv(t)[0])) for t in ts]
    d_best = [float(np.linalg.norm(eph_s.rv(t)[0] - eph_best.rv(t)[0])) for t in ts] if eph_best else None
    # 全域：TCA ± 半週期（限制在 [lo, dur]）
    a, b = max(lo, tca0 - 0.5 * period), min(dur, tca0 + 0.5 * period)
    tg = [a + (b - a) * i / 120 for i in range(121)]

    def pts(eph):
        return [[round(float(x), 3) for x in eph.rv(t)[0]] for t in tg]
    glob = {"tau_s": tg, "primary_ref": pts(N0), "secondary": pts(eph_s),
            "primary_best": pts(eph_best) if eph_best else None,
            "burn_km": ([round(float(x), 3) for x in N0.rv(best["burn_tau_s"])[0]]
                        if eph_best and a <= best["burn_tau_s"] <= b else None)}
    # 局部：參考 TCA 時主星 RTN 座標（固定），次星相對主星的軌跡
    tc = ref_tgt["tca_tau_s"]
    rp, vp = N0.rv(tc)
    C = rtn_matrix(rp, vp)
    miss_b = best["event"]["miss_km"] if (eph_best and best.get("event")) else 0.0
    half = min(120.0, max(0.5, 3.0 * max(ref_tgt["miss_km"], miss_b, hbr_km) / max(ref_tgt["v_rel_kms"], 1e-6)))
    tl = [max(lo, tc - half) + (min(dur, tc + half) - max(lo, tc - half)) * i / 80 for i in range(81)]

    def relpts(eph):
        return [[round(float(x), 6) for x in C @ (eph_s.rv(t)[0] - eph.rv(t)[0])] for t in tl]
    local = {"frame": "參考主星於參考 TCA 的 RTN 軸（km）", "tau_s": tl, "rel_ref": relpts(N0),
             "rel_best": relpts(eph_best) if eph_best else None}
    return {"series": {"tau0_s": lo, "step_s": (dur - lo) / (n - 1), "d_ref_km": d_ref, "d_best_km": d_best},
            "global": glob, "local": local}
