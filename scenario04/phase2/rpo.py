"""RPO 控制模擬（v0.3 Sprint 3）：HCW 設計、閉迴路非線性重播、安全監控、abort_to_hold。

座標：目標星（primary）RTN 軸，x = R（徑向）、y = T（沿跡）、z = N（越軌）；相對狀態單位 m、m/s，
速度為旋轉 RTN 座標中的時間導數（HCW 的 ẋ, ẏ, ż）。追蹤星為 secondary。

段落（rpo.sequence）
  transfer    雙脈衝 HCW 瞄準：起點依實際相對狀態解 Φ_rv v₀ = r_f − Φ_rr r₀；到達時把相對速度歸零
  hold        脈衝式死區保持：偏離駐留點超過 deadband 時，以 HCW 瞄準在 retarget_s 內回到駐留點，到達時歸零
  fly_around  HCW 自然 2:1 橢圓：v = (n·y₀/2, −2n·x₀, 0)，經 revolutions 圈後歸零
同一控制律分別在 HCW 線性模型（設計）與 GCRF 數值傳播（二體＋J2–J4＋阻力，閉迴路重播）中執行。
v1 執行模型：理想脈衝、完美導航（控制律直接讀取真實相對狀態）。

安全監控（每個節點）：安全區侵入、接近速度（距離 < closing_range_m）、走廊半角（距離 < corridor_range_m、
段落 corridor = true；fly_around 預設 false）。
abort_to_hold：每個控制段起點與每 update_s 預存一筆撤離脈衝（HCW＋當下差分阻力（常數）受迫解、5 種執行誤差情境皆須達標，26 方向 × 25 量級網格，取 safe_orbits 圈內距離
≥ max(keep_out, min(margin·keep_out, 0.95·目前距離))、且圈末距離 ≥ max(2·margin·keep_out, 1.5·目前距離) 的最小 Δv），並在非線性模型下以 5 種執行誤差情境
（標稱、大小 ±3σ、兩個垂直方向指向 3σ）與差分阻力驗證 safe_orbits 圈內不侵入安全區。
故障注入：comm_loss_start_s 起停止執行控制，滑行 trigger_s 後以中斷前最後一筆預存脈衝撤離。
"""
from __future__ import annotations

import math
import time
from itertools import product

import numpy as np

from . import constants as K
from . import e2e
from .eop import Eop
from .ephemeris import NumericalEphemeris
from .forces import accel_drag
from .frames import EarthRotationTable
from .integrator import propagate
from .rtn import relative_rtn, rtn_matrix, rtn_offset_to_gcrf
from .schema import validate
from .timescale import UtcTime

CODE_VERSION = "phase2-sprint3"
AXES = {"+R": (1.0, 0.0, 0.0), "-R": (-1.0, 0.0, 0.0), "+T": (0.0, 1.0, 0.0), "-T": (0.0, -1.0, 0.0),
        "+N": (0.0, 0.0, 1.0), "-N": (0.0, 0.0, -1.0)}
ABORT_DIRS = [np.array(d, float) / math.sqrt(sum(x * x for x in d))
              for d in product((-1, 0, 1), repeat=3) if d != (0, 0, 0)]


class DesignError(Exception):
    def __init__(self, code: str, segment: int, detail: dict):
        super().__init__(code)
        self.code, self.segment, self.detail = code, segment, detail


def hcw_stm(n: float, t: float) -> np.ndarray:
    s, c = math.sin(n * t), math.cos(n * t)
    return np.array([
        [4 - 3 * c, 0, 0, s / n, 2 * (1 - c) / n, 0],
        [6 * (s - n * t), 1, 0, -2 * (1 - c) / n, (4 * s - 3 * n * t) / n, 0],
        [0, 0, c, 0, 0, s / n],
        [3 * n * s, 0, 0, c, 2 * s, 0],
        [-6 * n * (1 - c), 0, 0, -2 * s, 4 * c - 3, 0],
        [0, 0, -n * s, 0, 0, c]], float)


def hcw_target(r0, rf, n: float, T: float, segment: int) -> np.ndarray:
    """回傳起點所需相對速度 v₀（m/s）。Φ_rv 近奇異（轉移時間接近整數週期）時拋 DesignError。"""
    P = hcw_stm(n, T)
    A = P[:3, 3:]
    if abs(np.linalg.det(A * n)) < 1e-9:
        raise DesignError("transfer_singular", segment, {"duration_s": T})
    return np.linalg.solve(A, np.asarray(rf, float) - P[:3, :3] @ np.asarray(r0, float))


def hcw_forced(n: float, t: float, a) -> np.ndarray:
    """HCW 在常數加速度 a = (a_x, a_y, a_z)（m/s²，RTN）下的特解位置（自 t = 0 起，零初始狀態）。"""
    s, c = math.sin(n * t), math.cos(n * t)
    ax, ay, az = a
    n2 = n * n
    return np.array([ax / n2 * (1 - c) + 2 * ay / n2 * (n * t - s),
                     -2 * ax / n2 * (n * t - s) + ay / n2 * (4 * (1 - c) - 1.5 * n2 * t * t),
                     az / n2 * (1 - c)])


def drag_rel_rtn(fm_t, fm_c, rt, vt, rc, vc, er) -> np.ndarray:
    """差分阻力加速度（追蹤星減目標星），目標星 RTN 分量，m/s²。"""
    a = np.zeros(3)
    if fm_c.drag:
        a = a + accel_drag(rc, vc, er, fm_c.ballistic_m2_kg, fm_c.atmos)
    if fm_t.drag:
        a = a - accel_drag(rt, vt, er, fm_t.ballistic_m2_kg, fm_t.atmos)
    return rtn_matrix(rt, vt) @ a * 1000.0


def mean_motion(r, v) -> float:
    a = 1.0 / (2.0 / float(np.linalg.norm(r)) - float(np.dot(v, v)) / K.GM_KM3_S2)
    return math.sqrt(K.GM_KM3_S2 / a ** 3)


def _ecc(r, v) -> float:
    rn, v2 = float(np.linalg.norm(r)), float(np.dot(v, v))
    a = 1.0 / (2.0 / rn - v2 / K.GM_KM3_S2)
    h = np.cross(r, v)
    return math.sqrt(max(0.0, 1.0 - float(np.dot(h, h)) / (K.GM_KM3_S2 * a)))


def _rk4(r, v, tau, h, fm, ert):
    """與 integrator.propagate 的單步完全相同。"""
    def f(t, r_, v_):
        return fm.accel(r_, v_, ert.at(t))
    a = f(tau, r, v)
    k1r, k1v = v, a
    k2r, k2v = v + 0.5 * h * k1v, f(tau + 0.5 * h, r + 0.5 * h * k1r, v + 0.5 * h * k1v)
    k3r, k3v = v + 0.5 * h * k2v, f(tau + 0.5 * h, r + 0.5 * h * k2r, v + 0.5 * h * k2v)
    k4r, k4v = v + h * k3v, f(tau + h, r + h * k3r, v + h * k3v)
    return (r + h / 6.0 * (k1r + 2 * k2r + 2 * k3r + k4r), v + h / 6.0 * (k1v + 2 * k2v + 2 * k3v + k4v))


def _steps(x: float, h: float) -> int:
    return max(1, int(math.floor(x / h + 0.5)))


class _HcwPlant:
    def __init__(self, rho, rhod, n, h):
        self.x = np.concatenate([rho, rhod]).astype(float)
        self.n, self.Phi, self.k = n, hcw_stm(n, h), 0

    def rel(self):
        return self.x[:3].copy(), self.x[3:].copy()

    def mean_motion(self):
        return self.n

    def apply(self, dv):
        self.x[3:] = self.x[3:] + dv

    def step(self):
        self.x = self.Phi @ self.x
        self.k += 1

    def state(self):
        return self.x.copy()


class _NlPlant:
    def __init__(self, tr, rc, vc, h, fm, ert):
        self.tr, self.rc, self.vc, self.h, self.fm, self.ert, self.k = tr, rc.copy(), vc.copy(), h, fm, ert, 0

    def _t(self):
        return self.tr.r[self.k], self.tr.v[self.k]

    def rel(self):
        rt, vt = self._t()
        return relative_rtn(rt, vt, self.rc, self.vc)

    def mean_motion(self):
        return mean_motion(*self._t())

    def apply(self, dv):
        rt, vt = self._t()
        self.vc = self.vc + rtn_matrix(rt, vt).T @ np.asarray(dv, float) / 1000.0

    def step(self):
        self.rc, self.vc = _rk4(self.rc, self.vc, self.k * self.h, self.h, self.fm, self.ert)
        self.k += 1

    def state(self):
        return self.rc.copy(), self.vc.copy()


def _simulate(plant, segs, nsteps, h, ctl, record_nodes=()):
    """在指定 plant 上執行控制序列。回傳 (burns, segments, samples, records)。samples：每節點 (k, seg, ρ, ρ̇)（脈衝後）。"""
    burns, seg_out, samples, rec = [], [], [], {}
    record_nodes = set(record_nodes)
    max_burn, retarget, db_default = ctl["max_burn_ms"], ctl["retarget_s"], ctl["deadband_m"]

    def burn(dv, kind, si):
        dv = np.asarray(dv, float)
        plant.apply(dv)
        burns.append({"t_s": plant.k * h, "segment": si, "kind": kind,
                      "dv_rtn_ms": [float(x) for x in dv], "dv_ms": float(np.linalg.norm(dv))})

    def sample_and_step(si):
        rho, rhod = plant.rel()
        samples.append((plant.k, si, rho, rhod))
        if plant.k in record_nodes:
            rec[plant.k] = plant.state()
        plant.step()

    hold_pt = None
    for si, (seg, ns) in enumerate(zip(segs, nsteps)):
        k0, nb0 = plant.k, len(burns)
        info = {"index": si, "type": seg["type"], "label": seg.get("label", ""), "start_s": k0 * h, "end_s": (k0 + ns) * h}
        if seg["type"] == "transfer":
            tgt = np.asarray(seg["to_rtn_m"], float)
            rho, rhod = plant.rel()
            v0 = hcw_target(rho, tgt, plant.mean_motion(), ns * h, si)
            dv = v0 - rhod
            if float(np.linalg.norm(dv)) > max_burn:
                raise DesignError("transfer_dv_exceeds", si, {"dv_ms": float(np.linalg.norm(dv)), "max_burn_ms": max_burn})
            burn(dv, "transfer_start", si)
            for _ in range(ns):
                sample_and_step(si)
            rho, rhod = plant.rel()
            info["target_rtn_m"] = [float(x) for x in tgt]
            info["arrival_error_m"] = float(np.linalg.norm(rho - tgt))
            burn(-rhod, "transfer_arrival", si)
            hold_pt = tgt
        elif seg["type"] == "hold":
            if "at_rtn_m" in seg:
                p = np.asarray(seg["at_rtn_m"], float)
            else:
                p = hold_pt if hold_pt is not None else plant.rel()[0]
            db = float(seg.get("deadband_m", db_default))
            rsteps = _steps(retarget, h)
            corr_end = None
            for j in range(ns):
                rho, rhod = plant.rel()
                if corr_end is not None and plant.k == corr_end:
                    burn(-rhod, "deadband_null", si)
                    corr_end = None
                elif corr_end is None and float(np.linalg.norm(rho - p)) > db:
                    nr = min(rsteps, ns - j)
                    if nr >= 2:
                        dv = hcw_target(rho, p, plant.mean_motion(), nr * h, si) - rhod
                        if float(np.linalg.norm(dv)) <= max_burn:
                            burn(dv, "deadband_start", si)
                            corr_end = plant.k + nr
                sample_and_step(si)
            if corr_end is not None and plant.k == corr_end:
                burn(-plant.rel()[1], "deadband_null", si)
            info["hold_rtn_m"] = [float(x) for x in p]
            info["deadband_m"] = db
            hold_pt = p
        else:  # fly_around
            rho, rhod = plant.rel()
            n = plant.mean_motion()
            start_rho = rho.copy()
            burn(np.array([n * rho[1] / 2.0, -2.0 * n * rho[0], 0.0]) - rhod, "fly_around_start", si)
            for _ in range(ns):
                sample_and_step(si)
            rho, rhod = plant.rel()
            info["target_rtn_m"] = [float(x) for x in start_rho]
            info["arrival_error_m"] = float(np.linalg.norm(rho - start_rho))
            burn(-rhod, "fly_around_end", si)
            hold_pt = start_rho
        info["n_burns"] = len(burns) - nb0
        info["dv_ms"] = float(sum(b["dv_ms"] for b in burns[nb0:]))
        seg_out.append(info)
    rho, rhod = plant.rel()
    samples.append((plant.k, len(segs) - 1, rho, rhod))
    if plant.k in record_nodes:
        rec[plant.k] = plant.state()
    return burns, seg_out, samples, rec


def _monitor(samples, segs, lim, h):
    axis = np.array(AXES[lim["corridor_axis"]])
    viol: dict = {}
    min_d, max_c = math.inf, -math.inf
    ko, mc, cr, rr = lim["keep_out_m"], lim["max_closing_ms"], lim["corridor_range_m"], lim["closing_range_m"]
    half = lim["corridor_half_angle_deg"]

    def add(kind, si, k, val, limit, worse):
        key = (kind, si)
        v = viol.get(key)
        if v is None:
            viol[key] = {"type": kind, "segment": si, "first_t_s": k * h, "last_t_s": k * h, "count": 1,
                         "worst": val, "limit": limit}
        else:
            v["last_t_s"] = k * h
            v["count"] += 1
            if worse(val, v["worst"]):
                v["worst"] = val

    for k, si, rho, rhod in samples:
        d = float(np.linalg.norm(rho))
        c = -float(np.dot(rho, rhod)) / d if d > 0 else 0.0
        min_d = min(min_d, d)
        max_c = max(max_c, c)
        if d < ko:
            add("keep_out", si, k, d, ko, lambda a, b: a < b)
        if d < rr and c > mc:
            add("closing_speed", si, k, c, mc, lambda a, b: a > b)
        seg = segs[si]
        if seg.get("corridor", seg["type"] != "fly_around") and d < cr and d > 0:
            ang = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(rho, axis)) / d))))
            if ang > half:
                add("corridor", si, k, ang, half, lambda a, b: a > b)
    out = sorted(viol.values(), key=lambda v: (v["first_t_s"], v["type"]))
    return out, min_d, max_c


def _timeline(samples, h):
    n = len(samples)
    stride = max(1, math.ceil(n / K.RPO_TIMELINE_MAX))
    idx = [i for i in range(n) if i % stride == 0]
    if idx[-1] != n - 1:
        idx.append(n - 1)
    out = {"t_s": [], "rtn_m": [], "dist_m": [], "closing_ms": [], "segment": []}
    for i in idx:
        k, si, rho, rhod = samples[i]
        d = float(np.linalg.norm(rho))
        out["t_s"].append(k * h)
        out["rtn_m"].append([round(float(x), 3) for x in rho])
        out["dist_m"].append(round(d, 3))
        out["closing_ms"].append(round(-float(np.dot(rho, rhod)) / d if d > 0 else 0.0, 5))
        out["segment"].append(si)
    return out


def abort_search(rho, rhod, n, keep_out, horizon_s, margin, dv_max, a_rel, mag_frac, point_deg):
    """HCW（含常數差分加速度 a_rel 的受迫解）下單一撤離脈衝的最小 Δv 搜尋；5 種執行誤差情境都須達標。
    回傳 dict（dv_rtn_ms、hcw_min_dist_m、hcw_end_dist_m、required_m、escape_m、hcw_safe）。"""
    m_samp = int(horizon_s // K.RPO_ABORT_SAMPLE_S) + 1
    Phis = [hcw_stm(n, j * K.RPO_ABORT_SAMPLE_S) for j in range(m_samp)]
    base = np.array([P[:3, :3] @ rho + P[:3, 3:] @ rhod + hcw_forced(n, j * K.RPO_ABORT_SAMPLE_S, a_rel)
                     for j, P in enumerate(Phis)])
    G = np.array([P[:3, 3:] for P in Phis])
    d0 = float(np.linalg.norm(rho))
    req = max(keep_out, min(margin * keep_out, 0.95 * d0))
    esc = max(2.0 * margin * keep_out, K.RPO_ABORT_ESCAPE_FACTOR * d0)
    lo, N = K.RPO_ABORT_MIN_DV_MS, K.RPO_ABORT_N_DV
    mags = [lo * (dv_max / lo) ** (i / (N - 1)) for i in range(N)]
    best = None
    for m in mags:
        for u in ABORT_DIRS:
            dv = m * u
            md, de = math.inf, math.inf
            for _label, dvp in perturbations(dv, mag_frac, point_deg):
                dist = np.linalg.norm(base + G @ dvp, axis=1)
                md, de = min(md, float(np.min(dist))), min(de, float(dist[-1]))
            if md >= req and de >= esc:
                return {"dv_rtn_ms": [float(x) for x in dv], "dv_ms": m, "hcw_min_dist_m": md, "hcw_end_dist_m": de,
                        "required_m": req, "escape_m": esc, "hcw_safe": True}
            score = min(md / req, de / esc)
            if best is None or score > best[0]:
                best = (score, dv, m, md, de)
    return {"dv_rtn_ms": [float(x) for x in best[1]], "dv_ms": best[2], "hcw_min_dist_m": best[3], "hcw_end_dist_m": best[4],
            "required_m": req, "escape_m": esc, "hcw_safe": False}


def perturbations(dv, mag_frac, point_deg):
    """5 種執行誤差情境（3σ）：nominal、mag_plus、mag_minus、point_1、point_2。"""
    dv = np.asarray(dv, float)
    m = float(np.linalg.norm(dv))
    u = dv / m
    e1 = np.cross(u, np.array([1.0, 0.0, 0.0]))
    if np.linalg.norm(e1) < 1e-6:
        e1 = np.cross(u, np.array([0.0, 0.0, 1.0]))
    e1 = e1 / np.linalg.norm(e1)
    e2 = np.cross(u, e1)
    sm, th = 3.0 * mag_frac, math.radians(3.0 * point_deg)
    return [("nominal", dv), ("mag_plus", dv * (1 + sm)), ("mag_minus", dv * (1 - sm)),
            ("point_1", m * (math.cos(th) * u + math.sin(th) * e1)),
            ("point_2", m * (math.cos(th) * u + math.sin(th) * e2))]


def run(sc: dict, eop: Eop) -> dict:
    t_wall = time.perf_counter()
    val = validate(sc)
    if not val["ok"]:
        return {"status": "invalid_scenario", "validation": val}
    rpo = sc.get("rpo")
    if not rpo:
        return {"status": "invalid_scenario",
                "validation": {"ok": False, "codes": ["E_RPO_MISSING"], "messages": ["需要 rpo"]}}
    start = UtcTime.parse(sc["screening"]["start"])
    h = float(sc["screening"].get("step_s") or K.RK4_STEP_LEO_S)
    fm_cfg = sc["force_model"]
    objs = {o["role"]: o for o in sc["objects"]}
    tgt_o, ch_o = objs["primary"], objs["secondary"]
    lim = {**K.RPO_LIMITS_DEFAULT, **{k: v for k, v in (sc.get("rpo_limits") or {}).items() if k != "comm_loss"}}
    comm = {**K.RPO_COMM_LOSS_DEFAULT, **((sc.get("rpo_limits") or {}).get("comm_loss") or {})}
    ctl = {"retarget_s": K.RPO_RETARGET_S, "deadband_m": K.RPO_DEADBAND_M, "max_burn_ms": K.RPO_MAX_BURN_MS,
           **(rpo.get("control") or {})}
    ab = rpo.get("abort") or {}
    ab_cfg = {"update_s": float(ab.get("update_s", K.RPO_ABORT_UPDATE_S)),
              "max_dv_ms": float(ab.get("max_dv_ms", K.RPO_ABORT_MAX_DV_MS)),
              "margin": float(ab.get("margin", K.RPO_ABORT_MARGIN)),
              "mag_frac_sigma": float((ab.get("execution_error") or {}).get("mag_frac_sigma", K.RPO_EXEC_MAG_FRAC)),
              "point_deg_sigma": float((ab.get("execution_error") or {}).get("point_deg_sigma", K.RPO_EXEC_POINT_DEG))}

    # 初始狀態（τ = 0）
    t_lo = 0.0
    for o in sc["objects"]:
        if o["ephemeris"]["type"] == "state":
            te = (UtcTime.parse(o["ephemeris"]["epoch"]).mjd - start.mjd) * K.DAY_S + \
                 (UtcTime.parse(o["ephemeris"]["epoch"]).sec - start.sec)
            if te > 0:
                return {"status": "invalid_scenario",
                        "validation": {"ok": False, "codes": ["E_STATE_EPOCH_AFTER_START"], "messages": []}}
            t_lo = min(t_lo, te)
    fm_t = e2e.object_force_model(tgt_o, fm_cfg)
    fm_c = e2e.object_force_model(ch_o, fm_cfg)

    segs = rpo["sequence"]
    # 段落步數（fly_around 以 τ=0 目標星平均角速度計算週期）
    ert0 = EarthRotationTable(start, t_lo - 1.0, 1.0, eop)

    def state_at0(o, fm):
        r, v, t0 = e2e.initial_state_gcrf(o, start, eop)
        if t0 < 0:
            r, v = NumericalEphemeris(propagate(r, v, t0, 0.0, h, fm, ert0)).rv(0.0)
        return np.asarray(r, float), np.asarray(v, float)
    rt0, vt0 = state_at0(tgt_o, fm_t)
    if ch_o["ephemeris"]["type"] == "rtn_offset":
        rc0, vc0 = rtn_offset_to_gcrf(rt0, vt0, ch_o["ephemeris"]["r_m"], ch_o["ephemeris"]["v_ms"])
    else:
        rc0, vc0 = state_at0(ch_o, fm_c)
    n0 = mean_motion(rt0, vt0)
    period0 = 2 * math.pi / n0
    nsteps = [_steps(s["duration_s"], h) if s["type"] != "fly_around" else _steps(s["revolutions"] * period0, h)
              for s in segs]
    n_seq = sum(nsteps)
    h_steps = int(math.ceil(comm["safe_orbits"] * period0 / h - 1e-9))
    trig_steps = int(math.floor(comm["trigger_s"] / h + 0.5))
    fault = rpo.get("fault")
    k_f = int(math.floor(fault["comm_loss_start_s"] / h + 0.5)) if fault else None
    n_tot = max(n_seq + h_steps, (k_f + trig_steps + h_steps) if k_f is not None else 0) + 1
    ert = EarthRotationTable(start, t_lo - 1.0, n_tot * h + 2 * h + 1.0, eop)
    tr = propagate(rt0, vt0, 0.0, n_tot * h, h, fm_t, ert)

    info = {"scenario_id": sc["id"], "start": start.iso(), "step_s": h,
            "objects": {"target": {"name": tgt_o["name"], "norad_id": tgt_o.get("norad_id")},
                        "chaser": {"name": ch_o["name"], "norad_id": ch_o.get("norad_id")}},
            "limits": {**lim, "comm_loss": comm}}

    # HCW 設計
    rho0, rhod0 = relative_rtn(rt0, vt0, rc0, vc0)
    try:
        d_burns, d_segs, d_samples, _ = _simulate(_HcwPlant(rho0, rhod0, n0, h), segs, nsteps, h, ctl)
    except DesignError as ex:
        return {**info, "status": "invalid_design", "reason": ex.code, "segment": ex.segment, "detail": ex.detail,
                "stage": "hcw_design"}
    d_viol, d_min, _ = _monitor(d_samples, segs, lim, h)

    # 非線性閉迴路重播
    upd = _steps(ab_cfg["update_s"], h)
    seg_starts = [sum(nsteps[:i]) for i in range(len(nsteps))]
    abort_nodes = sorted(set(range(0, n_seq + 1, upd)) | set(seg_starts))   # 每個控制段起點＋週期更新
    rec_nodes = set(abort_nodes) | ({k_f} if k_f is not None and k_f <= n_seq else set())
    try:
        burns, seg_out, samples, rec = _simulate(_NlPlant(tr, rc0, vc0, h, fm_c, ert), segs, nsteps, h, ctl, rec_nodes)
    except DesignError as ex:
        return {**info, "status": "invalid_design", "reason": ex.code, "segment": ex.segment, "detail": ex.detail,
                "stage": "nonlinear_replay"}
    viol, min_d, max_c = _monitor(samples, segs, lim, h)
    ko = lim["keep_out_m"]

    def verify(k, rc, vc, dv, record=False):
        rt, vt = tr.r[k], tr.v[k]
        C = rtn_matrix(rt, vt)
        mins, path = [], None
        for label, dvp in perturbations(dv, ab_cfg["mag_frac_sigma"], ab_cfg["point_deg_sigma"]):
            r, v = rc.copy(), vc + C.T @ dvp / 1000.0
            md = float(np.linalg.norm(r - rt)) * 1000.0
            pts = [] if (record and label == "nominal") else None
            stride = max(1, h_steps // 300)
            for j in range(1, h_steps + 1):
                r, v = _rk4(r, v, (k + j - 1) * h, h, fm_c, ert)
                md = min(md, float(np.linalg.norm(r - tr.r[k + j])) * 1000.0)
                if pts is not None and j % stride == 0:
                    pts.append([round(float(x), 3) for x in relative_rtn(tr.r[k + j], tr.v[k + j], r, v)[0]])
            mins.append(md)
            if pts is not None:
                path = pts
        return mins, path

    entries = []
    for k in abort_nodes:
        rc, vc = rec[k]
        rho, rhod = relative_rtn(tr.r[k], tr.v[k], rc, vc)
        a_rel = drag_rel_rtn(fm_t, fm_c, tr.r[k], tr.v[k], rc, vc, ert.at(k * h))
        s = abort_search(rho, rhod, mean_motion(tr.r[k], tr.v[k]), ko, comm["safe_orbits"] * period0,
                         ab_cfg["margin"], ab_cfg["max_dv_ms"], a_rel, ab_cfg["mag_frac_sigma"], ab_cfg["point_deg_sigma"])
        s["a_rel_rtn_ms2"] = [float(x) for x in a_rel]
        mins, _ = verify(k, rc, vc, np.array(s["dv_rtn_ms"]))
        entries.append({"t_s": k * h, "range_m": float(np.linalg.norm(rho)), **s, "nl_min_dist_m": mins,
                        "nl_worst_m": min(mins), "safe": s["hcw_safe"] and min(mins) >= ko})
    all_safe = all(e["safe"] for e in entries)

    fault_out = None
    if fault:
        if k_f > n_seq:
            fault_out = {"status": "not_applicable", "comm_loss_start_s": fault["comm_loss_start_s"],
                         "note": "通訊中斷時刻晚於控制序列結束"}
        else:
            ent = [e for e in entries if e["t_s"] <= k_f * h + 1e-9][-1]
            rc, vc = rec[k_f]
            coast_min = float(np.linalg.norm(rc - tr.r[k_f])) * 1000.0
            coast_path = []
            for j in range(1, trig_steps + 1):
                rc, vc = _rk4(rc, vc, (k_f + j - 1) * h, h, fm_c, ert)
                coast_min = min(coast_min, float(np.linalg.norm(rc - tr.r[k_f + j])) * 1000.0)
                if j % max(1, trig_steps // 60) == 0:
                    coast_path.append([round(float(x), 3) for x in relative_rtn(tr.r[k_f + j], tr.v[k_f + j], rc, vc)[0]])
            k_tr = k_f + trig_steps
            mins, path = verify(k_tr, rc, vc, np.array(ent["dv_rtn_ms"]), record=True)
            fault_out = {"status": "evaluated", "comm_loss_start_s": k_f * h, "trigger_t_s": k_tr * h,
                         "stored_entry_t_s": ent["t_s"], "dv_rtn_ms": ent["dv_rtn_ms"], "dv_ms": ent["dv_ms"],
                         "coast_min_dist_m": coast_min, "nl_min_dist_m": mins, "nl_worst_m": min(mins),
                         "safe": coast_min >= ko and min(mins) >= ko,
                         "coast_path_rtn_m": coast_path, "abort_path_rtn_m": path}

    # HCW 適用前提
    max_range = max(float(np.linalg.norm(s[2])) for s in samples)
    ecc = _ecc(rt0, vt0)
    frac = max_range / 1000.0 / float(np.linalg.norm(rt0))
    appl = {"target_eccentricity": ecc, "max_range_m": max_range, "range_frac": frac,
            "hcw_applicable": ecc < K.RPO_HCW_ECC_MAX and frac < K.RPO_HCW_RANGE_FRAC_MAX,
            "thresholds": {"eccentricity": K.RPO_HCW_ECC_MAX, "range_frac": K.RPO_HCW_RANGE_FRAC_MAX}}

    reasons = []
    if viol:
        reasons.append("nominal_violations")
    if not all_safe:
        reasons.append("abort_unsafe")
    if fault_out and fault_out["status"] == "evaluated" and not fault_out["safe"]:
        reasons.append("fault_abort_unsafe")
    total_dv = float(sum(b["dv_ms"] for b in burns))
    mass, isp = ch_o.get("mass_kg"), (ch_o.get("propulsion") or {}).get("isp_s")
    notes = ["執行模型：理想脈衝（有限推力未模擬）", "導航：完美導航（控制律讀取真實相對狀態）",
             "非線性重播為模型間交叉驗證，不等同實際軌道真值"]
    if "fov_deg" in lim:
        notes.append("fov_deg 未模擬（v1 假設感測器持續指向目標）")
    if not appl["hcw_applicable"]:
        notes.append("超出 HCW 適用前提（目標星偏心率或相對距離過大），設計脈衝誤差可能偏大")
    return {
        **info, "status": "fail" if reasons else "pass", "reasons": reasons,
        "applicability": appl,
        "design": {"model": "HCW", "n_rad_s": n0, "burns": d_burns, "segments": d_segs,
                   "total_dv_ms": float(sum(b["dv_ms"] for b in d_burns)), "min_dist_m": d_min,
                   "violations": d_viol, "timeline": _timeline(d_samples, h)},
        "replay": {"model": {"integrator": "RK4", "step_s": h, "target_force_model": fm_t.describe(),
                             "chaser_force_model": fm_c.describe()},
                   "burns": burns, "segments": seg_out, "total_dv_ms": total_dv,
                   "propellant_kg": (float(mass) * (1.0 - math.exp(-total_dv / (float(isp) * K.MAN_G0_MS2)))
                                     if mass and isp else None),
                   "min_dist_m": min_d, "max_closing_ms": max_c, "violations": viol,
                   "timeline": _timeline(samples, h)},
        "abort": {"strategy": comm["strategy"], "trigger_s": comm["trigger_s"], "safe_orbits": comm["safe_orbits"],
                  "update_s": upd * h, "margin": ab_cfg["margin"], "max_dv_ms": ab_cfg["max_dv_ms"],
                  "execution_error": {"mag_frac_sigma": ab_cfg["mag_frac_sigma"],
                                      "point_deg_sigma": ab_cfg["point_deg_sigma"], "cases": "nominal, ±3σ 大小, 兩方向 3σ 指向"},
                  "entries": entries, "all_safe": all_safe},
        "fault": fault_out,
        "checks": {"nominal_violations": len(viol), "abort_entries": len(entries),
                   "abort_entries_safe": sum(1 for e in entries if e["safe"]),
                   "fault_abort_safe": fault_out["safe"] if fault_out and fault_out["status"] == "evaluated" else None},
        "notes": notes,
        "provenance": {"code_version": CODE_VERSION, "constants_version": K.CONSTANTS_VERSION, "eop": eop.version(),
                       "frames": {"propagation": "GCRF", "relative": "target RTN (x=R, y=T, z=N), rotating-frame velocity"}},
        "timing": {"elapsed_s": time.perf_counter() - t_wall},
    }
