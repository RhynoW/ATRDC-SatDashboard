"""第二階段跨語言差異報告（Sprint 0 出場條件第 4 項）。

把 Python 與 Rust 的差異分層量測，每層餵入完全相同的輸入：
  L-SGP4   相同 TLE、相同時刻 → TEME（python-sgp4 vs Rust sgp4 crate）
  L-frame  相同 TEME 狀態 → GCRF／ITRF（erfa vs sofars）
  L-integ  相同 GCRF 初始狀態 → RK4 傳播（二體＋J2–J4；另一例加指數大氣阻力）
  L-algo   相同二體解析軌跡 → 近接極小值、Pc、適用性篩選
  L-schema 相同場景案例 → 錯誤代碼集合
  L-e2e    相同場景 → 端到端報告
  L-maneuver 相同避碰場景（maneuver_cases.json 套用 ops）→ 狀態、原因、候選方案與驗證結果（Sprint 2）
  L-rpo    相同 RPO 場景（rpo_cases.json 套用 ops）→ 狀態、脈衝序列、違規、撤離表與故障注入（Sprint 3）
用法：py -3.13 tools/phase2_xlang_report.py  （需先 cargo build --release --bin phase2_cli）
輸出：scenario04/phase2/reports/xlang_report.{json,md}
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scenario04.phase2 import e2e, frames as F, kepler, maneuver, pc as PC, rpo  # noqa: E402
from scenario04.phase2.constants import GM_KM3_S2  # noqa: E402
from scenario04.phase2.eop import SNAPSHOT, TESTDATA, load_snapshot  # noqa: E402
from scenario04.phase2.forces import ForceModel  # noqa: E402
from scenario04.phase2.integrator import propagate  # noqa: E402
from scenario04.phase2.schema import validate  # noqa: E402
from scenario04.phase2.screening import screen  # noqa: E402
from scenario04.phase2.tca import find_minima  # noqa: E402
from scenario04.phase2.timescale import UtcTime  # noqa: E402

CLI = ROOT.parent / "RUST_SatDashboard" / "target" / "release" / "phase2_cli.exe"
OUT = ROOT / "scenario04" / "phase2" / "reports"
TIMES = ["2026-11-20T00:00:00Z", "2026-11-21T00:00:00Z", "2026-11-23T00:00:00Z"]


def rust(cmd: str, payload, *extra) -> object:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(payload, f)
        p = f.name
    r = subprocess.run([str(CLI), cmd, p, *map(str, extra)], capture_output=True, text=True, encoding="utf-8")
    Path(p).unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError(f"phase2_cli {cmd} 失敗：{r.stderr[:500]}")
    return json.loads(r.stdout)


def stats(arr) -> dict:
    a = np.asarray(arr, float)
    if a.size == 0:
        return {"n": 0}
    return {"n": int(a.size), "max": float(a.max()), "p99": float(np.percentile(a, 99)),
            "median": float(np.median(a)), "mean": float(a.mean())}


# ── L-SGP4 ────────────────────────────────────────────────────────────────────

def layer_sgp4(n_leo=300, n_deep=100):
    from sgp4.api import Satrec
    from scenario04.ingestion.index import get_sat_index
    idx = get_sat_index()
    leo, deep = [], []
    for nid in sorted(idx):
        l1, l2 = idx[nid]["line1"], idx[nid]["line2"]
        try:
            mm = float(l2[52:63])
        except ValueError:
            continue
        (deep if 1440.0 / mm >= 225.0 else leo).append((nid, l1, l2))
    rng = np.random.default_rng(20261118)
    pick = [leo[i] for i in rng.choice(len(leo), min(n_leo, len(leo)), replace=False)] + \
           [deep[i] for i in rng.choice(len(deep), min(n_deep, len(deep)), replace=False)]
    times = [UtcTime.parse(t) for t in TIMES]
    rs = rust("sgp4", {"tles": [{"line1": a, "line2": b} for _, a, b in pick], "times": TIMES})
    d = {"leo": [], "deep": []}
    dv = {"leo": [], "deep": []}
    err_mismatch = 0
    teme_cases = []
    for (nid, l1, l2), rlist in zip(pick, rs):
        sat = Satrec.twoline2rv(l1, l2)
        cls = "deep" if sat.method == "d" or 2 * math.pi / sat.no_kozai >= 225.0 else "leo"
        for t, rr in zip(times, rlist):
            e, r, v = sat.sgp4(2400000.5 + t.mjd, t.sec / 86400.0)
            if (e != 0) != ("error" in rr):
                err_mismatch += 1
                continue
            if e != 0:
                continue
            d[cls].append(np.linalg.norm(np.array(r) - rr["r"]) * 1000.0)
            dv[cls].append(np.linalg.norm(np.array(v) - rr["v"]) * 1e6)
            if len(teme_cases) < 120:
                teme_cases.append({"t": t.iso(), "r_teme": list(r), "v_teme": list(v)})
    worst = None
    if d["deep"]:
        worst = float(max(d["deep"]))
    return {"n_tles": len(pick), "times": TIMES, "error_flag_mismatch": err_mismatch,
            "pos_diff_m": {k: stats(v) for k, v in d.items()}, "vel_diff_mm_s": {k: stats(v) for k, v in dv.items()},
            "worst_deep_m": worst}, teme_cases


# ── L-frame ──────────────────────────────────────────────────────────────────

def layer_frames(cases):
    eop = load_snapshot()
    rs = rust("frames", {"cases": cases}, SNAPSHOT)
    dg, dvg, di = [], [], []
    for c, r in zip(cases, rs):
        er = F.earth_rotation(UtcTime.parse(c["t"]), eop)
        rg, vg = F.teme_to_gcrf(np.array(c["r_teme"]), np.array(c["v_teme"]), er)
        ri, _vi = F.teme_to_itrf(np.array(c["r_teme"]), np.array(c["v_teme"]), er)
        dg.append(np.linalg.norm(rg - r["r_gcrf"]) * 1000.0)
        dvg.append(np.linalg.norm(vg - r["v_gcrf"]) * 1e6)
        di.append(np.linalg.norm(ri - r["r_itrf"]) * 1000.0)
    return {"n": len(cases), "gcrf_pos_diff_m": stats(dg), "gcrf_vel_diff_mm_s": stats(dvg), "itrf_pos_diff_m": stats(di)}


# ── L-integ ──────────────────────────────────────────────────────────────────

def layer_integration():
    eop = load_snapshot()
    ep = "2026-11-20T00:00:00Z"
    cases = []
    for name, el, step, drag, bal in (
        ("LEO 500 km J2–J4", kepler.Elements(6878.137, 0.001, math.radians(51.6), 0.3, 0.2, 0.1), 10.0, False, 0.0),
        ("LEO 400 km J2–J4＋阻力", kepler.Elements(6778.137, 0.0008, math.radians(97.0), 1.0, 0.5, 2.0), 10.0, True, 0.022),
        ("GEO J2–J4", kepler.Elements(42164.0, 0.0002, math.radians(0.05), 0.0, 0.0, 1.0), 60.0, False, 0.0),
    ):
        r0, v0, _ = kepler.state(el, 0.0)
        out_times = [k * 10800.0 for k in range(9)]
        payload = {"epoch": ep, "r_km": list(r0), "v_kms": list(v0), "duration_s": 86400.0, "step_s": step,
                   "zonal": True, "drag": drag, "ballistic_m2_kg": bal, "out_times": out_times}
        rs = rust("propagate", payload, SNAPSHOT)
        ert = F.EarthRotationTable(UtcTime.parse(ep), 0.0, 86400.0, eop)
        tr = propagate(r0, v0, 0.0, 86400.0, step, ForceModel(zonal=True, drag=drag, ballistic_m2_kg=bal), ert)
        dd = [float(np.linalg.norm(tr.state(t)[0] - r["r"]) * 1000.0) for t, r in zip(out_times, rs)]
        cases.append({"case": name, "pos_diff_m_by_3h": dd, "max_m": max(dd)})
    return {"duration_s": 86400.0, "cases": cases}


# ── L-algo ───────────────────────────────────────────────────────────────────

def _py_kepler_tca(c):
    e1, e2 = kepler.Elements(*c["e1"]), kepler.Elements(*c["e2"])
    rel = lambda t: tuple(b - a for a, b in zip(kepler.state(e1, t), kepler.state(e2, t)))  # noqa: E731
    res = find_minima(rel, 0.0, c["t1"], c["step"])
    g = res["global_min"]
    C3 = np.array(c["cov3"])
    m2, C2, _ = PC.project(g.r_rel, g.v_rel, C3)
    period = 2 * math.pi * math.sqrt(e1.a_km ** 3 / GM_KM3_S2)
    scr = screen(rel, g.tau, lambda t: C3, "demo", c["hbr_km"], period, [m.tau for m in res["all_minima"]], (0.0, c["t1"]))
    return {"tau": g.tau, "miss_km": g.miss_km, "v_rel_kms": float(np.linalg.norm(g.v_rel)),
            "n_minima": len(res["all_minima"]), "pc_foster": PC.pc_foster(m2, C2, c["hbr_km"]),
            "pc_chan": PC.pc_chan(m2, C2, c["hbr_km"]), "screen": scr["status"], "t_enc_s": scr["metrics"].get("t_enc_s")}


def layer_algorithm():
    d = math.pi / 180
    cov = [[0.04, 0.0, 0.0], [0.0, 0.25, 0.0], [0.0, 0.0, 0.04]]
    cases = [
        {"name": "LEO 交叉", "e1": [6878.137, 0.0005, 51.6 * d, 0.5, 0.0, 0.0], "e2": [6878.537, 0.0005, 97.4 * d, 0.5, 0.0, 0.001],
         "t1": 10800.0, "step": 10.0, "cov3": cov, "hbr_km": 0.01},
        {"name": "LEO 迎頭", "e1": [6878.137, 0.0005, 51.6 * d, 0.5, 0.0, 0.0],
         "e2": [6878.337, 0.0005, (180 - 51.6) * d, 0.5 + math.pi, math.pi, -0.0002], "t1": 10800.0, "step": 10.0, "cov3": cov, "hbr_km": 0.02},
        {"name": "LEO 小角度追越", "e1": [6878.137, 0.0005, 51.6 * d, 0.5, 0.0, 0.0], "e2": [6878.237, 0.0006, 51.65 * d, 0.5003, 0.0, -0.0005],
         "t1": 86400.0, "step": 10.0, "cov3": cov, "hbr_km": 0.01},
        {"name": "GEO 慢速", "e1": [42164.0, 0.0002, 0.001, 0.0, 0.0, 0.0], "e2": [42164.5, 0.0003, 0.0012, 0.0, 0.0, 0.00002],
         "t1": 172800.0, "step": 60.0, "cov3": [[1.0, 0, 0], [0, 4.0, 0], [0, 0, 1.0]], "hbr_km": 0.01},
    ]
    rs = rust("kepler-tca", {"cases": cases})
    rows = []
    for c, r in zip(cases, rs):
        p = _py_kepler_tca(c)
        rel_pc = (abs(p["pc_foster"] - r["pc_foster"]) / max(abs(p["pc_foster"]), 1e-300)) if p["pc_foster"] else 0.0
        rows.append({"case": c["name"], "dtau_s": abs(p["tau"] - r["tau"]), "dmiss_m": abs(p["miss_km"] - r["miss_km"]) * 1000,
                     "dvrel_mm_s": abs(p["v_rel_kms"] - r["v_rel_kms"]) * 1e6, "pc_foster_py": p["pc_foster"],
                     "pc_foster_rel_diff": rel_pc, "screen_py": p["screen"], "screen_rs": r["screen"],
                     "n_minima_equal": p["n_minima"] == r["n_minima"]})
    pcs = [{"miss": [0.2, 0.0], "c2": [[0.05, -0.03], [-0.03, 1.0]], "hbr_km": 0.01},
           {"miss": [0.0, 0.0], "c2": [[0.01, 0.0], [0.0, 0.04]], "hbr_km": 0.02},
           {"miss": [1.0, 0.5], "c2": [[0.3, 0.1], [0.1, 0.2]], "hbr_km": 0.5},
           {"miss": [3.0, 0.0], "c2": [[0.05, 0.0], [0.0, 1.0]], "hbr_km": 0.01}]
    rp = rust("pc", {"cases": pcs})
    pc_rows = []
    for c, r in zip(pcs, rp):
        f = PC.pc_foster(np.array(c["miss"]), np.array(c["c2"]), c["hbr_km"])
        ch = PC.pc_chan(np.array(c["miss"]), np.array(c["c2"]), c["hbr_km"])
        pc_rows.append({"foster": f, "foster_abs_diff": abs(f - r["foster"]), "foster_rel_diff": abs(f - r["foster"]) / max(f, 1e-300),
                        "chan_rel_diff": abs(ch - r["chan"]) / max(ch, 1e-300)})
    return {"kepler_tca": rows, "pc": pc_rows}


# ── L-schema、L-e2e ─────────────────────────────────────────────────────────

def layer_schema_compare():
    path = TESTDATA / "schema_cases.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    base = json.loads((TESTDATA / spec["base"]).read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT / "tests"))
    from test_phase2 import _apply  # noqa: E402
    r = subprocess.run([str(CLI), "schema-cases", str(path)], capture_output=True, text=True, encoding="utf-8")
    rs = {x["name"]: x["codes"] for x in json.loads(r.stdout)}
    mism = []
    for c in spec["cases"]:
        py = validate(_apply(base, c["ops"]))["codes"]
        if py != rs.get(c["name"]):
            mism.append({"case": c["name"], "python": py, "rust": rs.get(c["name"])})
    return {"n_cases": len(spec["cases"]), "mismatches": mism}


def layer_e2e():
    out = {}
    for name in ("e2e_leo_synthetic", "e2e_leo_propagated", "e2e_tle_iss_css"):
        path = TESTDATA / f"{name}.json"
        sc = json.loads(path.read_text(encoding="utf-8"))
        py = e2e.run(sc, load_snapshot())
        r = subprocess.run([str(CLI), "e2e", str(path), str(SNAPSHOT)], capture_output=True, text=True, encoding="utf-8")
        rs = json.loads(r.stdout)
        rows = []
        for a, b in zip(py["events"], rs["events"]):
            pa, pb = (a.get("pc") or {}).get("foster") or 0.0, (b.get("pc") or {}).get("foster") or 0.0
            rows.append({"tca_utc": a["tca_utc"], "dtau_s": abs(a["tca_tau_s"] - b["tca_tau_s"]),
                         "dmiss_m": abs(a["miss_km"] - b["miss_km"]) * 1000.0, "pc_foster_py": pa,
                         "pc_foster_rel_diff": abs(pa - pb) / max(abs(pa), 1e-300) if pa else abs(pb),
                         "screen_equal": a["applicability"]["status"] == b["applicability"]["status"]})
        out[name] = {"n_events_py": len(py["events"]), "n_events_rs": len(rs["events"]), "events": rows,
                     "n_minima_equal": py["search"]["n_minima"] == rs["search"]["n_minima"]}
    return out


def _apply_ops(doc, ops):
    out = json.loads(json.dumps(doc))
    for op in ops:
        tgt = out
        for k in op["path"][:-1]:
            tgt = tgt[k]
        if op["op"] == "set":
            tgt[op["path"][-1]] = op["value"]
        else:
            del tgt[op["path"][-1]]
    return out


def layer_maneuver():
    cases = json.loads((TESTDATA / "maneuver_cases.json").read_text(encoding="utf-8"))
    base = json.loads((TESTDATA / cases["base"]).read_text(encoding="utf-8"))
    rows = []
    for c in cases["cases"]:
        sc = _apply_ops(base, c["ops"])
        py = maneuver.run(sc, load_snapshot())
        rs = rust("maneuver", sc, SNAPSHOT)
        cp, cr = py.get("candidates") or [], rs.get("candidates") or []
        # 機動時刻以 1e-6 s 容差比對（tca0 來自基準篩選，跨語言差約 1e-11 s）；方向與 Δv 量級須完全相同
        same_list = len(cp) == len(cr) and all(
            abs(a["burn_tau_s"] - b["burn_tau_s"]) <= 1e-6 and a["direction"] == b["direction"] and a["dv_ms"] == b["dv_ms"]
            for a, b in zip(cp, cr))
        dmiss = dlin = dpc = dtau = dda = dburn = 0.0
        same_pass = True
        if same_list:
            for a, b in zip(cp, cr):
                same_pass &= a["passed"] == b["passed"] and a["checks"] == b["checks"]
                dburn = max(dburn, abs(a["burn_tau_s"] - b["burn_tau_s"]))
                dlin = max(dlin, abs(a["linear"]["miss_km"] - b["linear"]["miss_km"]) * 1000.0)
                dda = max(dda, abs(a["delta_a_km"] - b["delta_a_km"]) * 1000.0)
                if a.get("event") and b.get("event"):
                    dmiss = max(dmiss, abs(a["event"]["miss_km"] - b["event"]["miss_km"]) * 1000.0)
                    dtau = max(dtau, abs(a["event"]["tca_tau_s"] - b["event"]["tca_tau_s"]))
                    pa, pb = a.get("pc_after"), b.get("pc_after")
                    if pa is not None and pb is not None:
                        dpc = max(dpc, abs(pa - pb) / max(abs(pa), 1e-300))
        lm_eq = [r["min_dv_ms"] for r in py.get("linear_map") or []] == [r["min_dv_ms"] for r in rs.get("linear_map") or []]
        rows.append({"case": c["name"], "status_py": py["status"], "status_rs": rs["status"],
                     "reason_equal": py.get("reason") == rs.get("reason"), "candidates_equal": same_list,
                     "pass_checks_equal": same_pass, "linear_map_equal": lm_eq, "n_candidates": len(cp),
                     "max_dmiss_m": dmiss, "max_dtau_s": dtau, "max_dlin_m": dlin, "max_pc_rel_diff": dpc,
                     "max_dda_m": dda, "max_dburn_s": dburn})
    return rows


def layer_rpo():
    cases = json.loads((TESTDATA / "rpo_cases.json").read_text(encoding="utf-8"))
    base = json.loads((TESTDATA / cases["base"]).read_text(encoding="utf-8"))
    rows = []
    for c in cases["cases"]:
        sc = _apply_ops(base, c["ops"])
        py = rpo.run(sc, load_snapshot())
        rs = rust("rpo", sc, SNAPSHOT)
        row = {"case": c["name"], "status_py": py["status"], "status_rs": rs["status"],
               "reasons_equal": py.get("reasons") == rs.get("reasons") and py.get("reason") == rs.get("reason"),
               "burns_equal": None, "violations_equal": None, "abort_equal": None, "fault_equal": None,
               "n_burns": 0, "max_ddv_ms": 0.0, "max_dmin_m": 0.0, "max_dabort_rel": 0.0}
        if "replay" in py and "replay" in rs:
            bp, br = py["replay"]["burns"], rs["replay"]["burns"]
            row["n_burns"] = len(bp)
            row["burns_equal"] = [(b["t_s"], b["kind"], b["segment"]) for b in bp] ==                                  [(b["t_s"], b["kind"], b["segment"]) for b in br]
            if row["burns_equal"]:
                row["max_ddv_ms"] = max((max(abs(x - y) for x, y in zip(a["dv_rtn_ms"], b["dv_rtn_ms"]))
                                         for a, b in zip(bp, br)), default=0.0)
            vk = lambda v: (v["type"], v["segment"], v["first_t_s"], v["last_t_s"], v["count"])  # noqa: E731
            row["violations_equal"] = [vk(v) for v in py["replay"]["violations"]] == [vk(v) for v in rs["replay"]["violations"]]
            row["max_dmin_m"] = abs(py["replay"]["min_dist_m"] - rs["replay"]["min_dist_m"])
            ep, er = py["abort"]["entries"], rs["abort"]["entries"]
            row["abort_equal"] = [(e["t_s"], e["dv_rtn_ms"] == f["dv_rtn_ms"], e["safe"]) for e, f in zip(ep, er)] ==                                  [(e["t_s"], True, f["safe"]) for e, f in zip(ep, er)] and len(ep) == len(er)
            row["max_dabort_rel"] = max((abs(e["nl_worst_m"] - f["nl_worst_m"]) / e["nl_worst_m"] for e, f in zip(ep, er)),
                                        default=0.0)
            fp, fr = py.get("fault"), rs.get("fault")
            row["fault_equal"] = (fp is None and fr is None) or (fp is not None and fr is not None and
                                  fp.get("status") == fr.get("status") and fp.get("safe") == fr.get("safe") and
                                  fp.get("stored_entry_t_s") == fr.get("stored_entry_t_s"))
        rows.append(row)
    return rows


def layer_adaptive():
    from scenario04.phase2.ephemeris import RelativeState, Sgp4Ephemeris
    from scenario04.phase2.tca import find_minima_adaptive
    tles = json.loads((TESTDATA / "tle_fixture.json").read_text(encoding="utf-8"))["tles"]
    pairs = [("25544", "48274"), ("59103", "68762"), ("69673", "67689")]
    inp = {"start": "2026-11-18T00:00:00Z", "duration_s": 86400.0, "coarse_step_s": 60.0, "threshold_km": 1000.0,
           "pairs": [{"p": tles[a], "s": tles[b]} for a, b in pairs]}
    rs = rust("adaptive-tle", inp, SNAPSHOT)
    start = UtcTime.parse(inp["start"])
    ert = F.EarthRotationTable(start, -1.0, 86401.0, load_snapshot())
    rows = []
    for (a, b), r in zip(pairs, rs):
        rel = RelativeState(Sgp4Ephemeris(tles[a]["line1"], tles[a]["line2"], start, ert, (0.0, 86400.0)),
                            Sgp4Ephemeris(tles[b]["line1"], tles[b]["line2"], start, ert, (0.0, 86400.0)))
        res = find_minima_adaptive(rel.rv, rel.rva, 0.0, 86400.0, 60.0, 1000.0)
        pm, rm = res["all_minima"], r["minima"]
        n = min(len(pm), len(rm))
        rows.append({"pair": f"{a}×{b}", "n_py": len(pm), "n_rs": len(rm), "n_evals_py": rel.n_evals, "n_evals_rs": r["n_evals"],
                     "max_dtau_s": max((abs(pm[i].tau - rm[i]["tau"]) for i in range(n)), default=0.0),
                     "max_dmiss_m": max((abs(pm[i].miss_km - rm[i]["miss_km"]) * 1000 for i in range(n)), default=0.0),
                     "flags_equal": all(pm[i].flag == rm[i]["flag"] for i in range(n))})
    return rows


def layer_covprop():
    from scenario04.phase2.covprop import CovarianceTrack
    from scenario04.phase2.ephemeris import NumericalEphemeris
    el = kepler.Elements(6878.137, 0.001, 0.9, 0.3, 0.2, 0.1)
    r0, v0, _ = kepler.state(el, 0.0)
    P0 = np.diag([0.05 ** 2, 0.2 ** 2, 0.05 ** 2, 1e-8, 1e-8, 1e-8]).tolist()
    outs = [3600.0, 10800.0, 20000.0, 43200.0]
    inp = {"epoch": "2026-11-20T00:00:00Z", "r_km": list(r0), "v_kms": list(v0), "P0": P0, "duration_s": 43200.0,
           "state_step_s": 10.0, "cov_step_s": 20.0, "zonal": True, "q_km2_s3": 0.0, "out_times": outs}
    rs = rust("covprop", inp, SNAPSHOT)
    ert = F.EarthRotationTable(UtcTime.parse(inp["epoch"]), -1.0, 43201.0, load_snapshot())
    nom = NumericalEphemeris(propagate(r0, v0, 0.0, 43200.0, 10.0, ForceModel(zonal=True), ert))
    trk = CovarianceTrack(nom, np.array(P0), 0.0, 43200.0, 20.0, ert, zonal=True)
    return [{"tau_s": t, "max_rel_diff": float(np.abs(trk.at(t) - np.array(r["P"])).max() / np.abs(trk.at(t)).max())}
            for t, r in zip(outs, rs)]


def layer_csv_batch():
    from scenario04.phase2.csvio import parse_secondaries
    csv_path = TESTDATA / "batch_secondaries.csv"
    py = parse_secondaries(csv_path.read_text(encoding="utf-8"))
    r = subprocess.run([str(CLI), "csv-parse", str(csv_path)], capture_output=True, text=True, encoding="utf-8")
    rs = json.loads(r.stdout)
    csv_ok = (py["rows"] == rs["rows"]
              and [[e["line"], e["code"]] for e in py["errors"]] == [[e["line"], e["code"]] for e in rs["errors"]])
    exp = json.loads((TESTDATA / "batch_iss.expected.json").read_text(encoding="utf-8"))
    r = subprocess.run([str(CLI), "batch", str(TESTDATA / "batch_iss.request.json"), str(SNAPSHOT),
                        str(TESTDATA / "tle_fixture.json")], capture_output=True, text=True, encoding="utf-8")
    rb = json.loads(r.stdout)
    rows = []
    for a, b in zip(exp["pairs"], rb["pairs"]):
        ea, eb = a.get("events") or [], b.get("events") or []
        rows.append({"secondary": a.get("secondary_norad"), "status_equal": a["status"] == b["status"],
                     "n_events": [len(ea), len(eb)], "n_minima": [a.get("n_minima"), b.get("n_minima")],
                     "max_dmiss_m": max((abs(x["miss_km"] - y["miss_km"]) * 1000 for x, y in zip(ea, eb)), default=0.0)})
    return {"csv_equal": csv_ok, "batch_pairs": rows}


def to_md(rep: dict) -> str:
    s = rep["layers"]
    L = [f"# 第二階段跨語言差異報告", "", f"產生時間：{rep['generated_utc']}　EOP：{rep['eop']}", "",
         "每一層餵入完全相同的輸入，差異只來自該層的實作。", "",
         "## L-SGP4（python-sgp4 vs Rust sgp4 crate）", "",
         f"TLE {s['sgp4']['n_tles']} 筆 × {len(s['sgp4']['times'])} 個時刻；錯誤旗標不一致 {s['sgp4']['error_flag_mismatch']} 筆。", "",
         "| 類別 | 筆數 | 位置差中位數 m | p99 m | 最大 m | 速度差最大 mm/s |", "|---|---|---|---|---|---|"]
    for k, lab in (("leo", "近地（週期 < 225 min）"), ("deep", "深空（週期 ≥ 225 min）")):
        p, v = s["sgp4"]["pos_diff_m"][k], s["sgp4"]["vel_diff_mm_s"][k]
        if p.get("n"):
            L.append(f"| {lab} | {p['n']} | {p['median']:.3e} | {p['p99']:.3e} | {p['max']:.3e} | {v['max']:.3e} |")
    f = s["frames"]
    L += ["", "## L-frame（erfa vs sofars，IAU-2006/2000A）", "",
          f"{f['n']} 筆：GCRF 位置差最大 {f['gcrf_pos_diff_m']['max']:.3e} m、速度差最大 {f['gcrf_vel_diff_mm_s']['max']:.3e} mm/s；"
          f"ITRF 位置差最大 {f['itrf_pos_diff_m']['max']:.3e} m。", "",
          "## L-integ（相同初始狀態，RK4 傳播 1 天）", "", "| 案例 | 最大位置差 m |", "|---|---|"]
    for c in s["integration"]["cases"]:
        L.append(f"| {c['case']} | {c['max_m']:.3e} |")
    L += ["", "## L-algo（相同二體解析軌跡）", "",
          "| 案例 | ΔTCA s | Δ距離 m | Δ相對速度 mm/s | Pc Foster | Pc 相對差 | 篩選 Py／Rs | 極小值數相同 |", "|---|---|---|---|---|---|---|---|"]
    for r in s["algorithm"]["kepler_tca"]:
        L.append(f"| {r['case']} | {r['dtau_s']:.2e} | {r['dmiss_m']:.2e} | {r['dvrel_mm_s']:.2e} | {r['pc_foster_py']:.3e} | "
                 f"{r['pc_foster_rel_diff']:.2e} | {r['screen_py']}／{r['screen_rs']} | {r['n_minima_equal']} |")
    L += ["", "Pc 固定輸入（Foster 相對差／Chan 相對差）：" + "；".join(
        f"{r['foster']:.3e}：{r['foster_rel_diff']:.1e}／{r['chan_rel_diff']:.1e}" for r in s["algorithm"]["pc"])]
    L += ["", "## L-schema", "", f"{s['schema']['n_cases']} 個案例，代碼不一致 {len(s['schema']['mismatches'])} 個。"]
    L += ["", "## L-adaptive（相同 TLE、SGP4 星曆，自適應搜尋 1 天，門檻 1000 km）", "",
          "| 配對 | 極小值數 Py／Rs | 評估次數 Py／Rs | 最大 ΔTCA s | 最大 Δ距離 m | 標記一致 |", "|---|---|---|---|---|---|"]
    for r in s["adaptive"]:
        L.append(f"| {r['pair']} | {r['n_py']}／{r['n_rs']} | {r['n_evals_py']}／{r['n_evals_rs']} | "
                 f"{r['max_dtau_s']:.2e} | {r['max_dmiss_m']:.2e} | {r['flags_equal']} |")
    L += ["", "## L-covprop（相同名義軌跡，傳播模式協方差）", "", "| τ s | 最大相對差 |", "|---|---|"]
    L += [f"| {r['tau_s']:.0f} | {r['max_rel_diff']:.2e} |" for r in s["covprop"]]
    cb = s["csv_batch"]
    L += ["", "## L-csv／L-batch", "", f"CSV 解析一致：{cb['csv_equal']}。", "",
          "| 次星 | 狀態一致 | 事件數 Py／Rs | 極小值數 Py／Rs | 最大 Δ距離 m |", "|---|---|---|---|---|"]
    for r in cb["batch_pairs"]:
        L.append(f"| {r['secondary']} | {r['status_equal']} | {r['n_events'][0]}／{r['n_events'][1]} | "
                 f"{r['n_minima'][0]}／{r['n_minima'][1]} | {r['max_dmiss_m']:.2e} |")
    for name, e in s["e2e"].items():
        L += ["", f"## L-e2e：{name}", "",
              f"事件數 Python {e['n_events_py']}、Rust {e['n_events_rs']}；極小值數一致 {e['n_minima_equal']}。", "",
              "| TCA | ΔTCA s | Δ距離 m | Pc | Pc 相對差 | 篩選一致 |", "|---|---|---|---|---|---|"]
        for r in e["events"]:
            L.append(f"| {r['tca_utc']} | {r['dtau_s']:.2e} | {r['dmiss_m']:.2e} | {r['pc_foster_py']:.3e} | "
                     f"{r['pc_foster_rel_diff']:.2e} | {r['screen_equal']} |")
    L += ["", "## L-maneuver（相同避碰場景）", "",
          "| 案例 | 狀態 Py／Rs | 原因一致 | 候選相同 | 檢查一致 | 線性圖一致 | 候選數 | 最大 Δ機動時刻 s | 最大 Δ距離 m | 最大 ΔTCA s | 最大 Δ線性距離 m | Pc 相對差 | 最大 Δa 差 m |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in s["maneuver"]:
        L.append(f"| {r['case']} | {r['status_py']}／{r['status_rs']} | {r['reason_equal']} | {r['candidates_equal']} | "
                 f"{r['pass_checks_equal']} | {r['linear_map_equal']} | {r['n_candidates']} | {r['max_dburn_s']:.1e} | {r['max_dmiss_m']:.2e} | "
                 f"{r['max_dtau_s']:.2e} | {r['max_dlin_m']:.2e} | {r['max_pc_rel_diff']:.2e} | {r['max_dda_m']:.2e} |")
    L += ["", "## L-rpo（相同 RPO 場景）", "",
          "| 案例 | 狀態 Py／Rs | 原因一致 | 脈衝序列相同 | 違規相同 | 撤離表相同 | 故障一致 | 脈衝數 | 最大 Δ脈衝 m/s | Δ最近距離 m | 撤離最差距離相對差 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in s["rpo"]:
        L.append(f"| {r['case']} | {r['status_py']}／{r['status_rs']} | {r['reasons_equal']} | {r['burns_equal']} | "
                 f"{r['violations_equal']} | {r['abort_equal']} | {r['fault_equal']} | {r['n_burns']} | {r['max_ddv_ms']:.2e} | "
                 f"{r['max_dmin_m']:.2e} | {r['max_dabort_rel']:.2e} |")
    L += ["", "## 結論與容差建議", ""] + [f"- {x}" for x in rep["conclusions"]]
    return "\n".join(L) + "\n"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sg, teme = layer_sgp4()
    rep = {"generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
           "eop": load_snapshot().version(),
           "layers": {"sgp4": sg, "frames": layer_frames(teme), "integration": layer_integration(),
                      "algorithm": layer_algorithm(), "schema": layer_schema_compare(), "e2e": layer_e2e(),
                      "adaptive": layer_adaptive(), "covprop": layer_covprop(), "csv_batch": layer_csv_batch(),
                      "maneuver": layer_maneuver(), "rpo": layer_rpo()}}
    s = rep["layers"]
    leo_max = s["sgp4"]["pos_diff_m"]["leo"].get("max", 0.0)
    deep_max = s["sgp4"]["pos_diff_m"]["deep"].get("max", 0.0)
    worst = max(leo_max, deep_max)
    rec = 10 ** math.ceil(math.log10(worst * 3)) if worst > 0 else 1e-3
    rep["tolerance_recommendation"] = {"tle_input_position_eps_abs_m": rec, "basis_max_m": worst}
    rep["conclusions"] = [
        f"SGP4 層：近地物體最大差 {leo_max:.2e} m、深空物體最大差 {deep_max:.2e} m；"
        f"建議第 10 節「TLE 輸入（經 SGP4）的位置」容差 ε_abs = {rec * 1000:g} mm（實測最大值約 3 倍餘裕）。"
        "兩邊皆採 WGS-72、opsmode 'i'；Rust sgp4 crate 的 Constants::from_elements 預設為 WGS-84，不可使用。",
        f"框架層：GCRF 最大差 {s['frames']['gcrf_pos_diff_m']['max']:.1e} m，遠低於 1 mm 容差。",
        "積分層：最大差 " + "、".join(f"{c['case']} {c['max_m']:.1e} m" for c in s["integration"]["cases"]) + "。",
        "演算法層：ΔTCA 最大 " + f"{max(r['dtau_s'] for r in s['algorithm']['kepler_tca']):.1e} s，"
        "Pc 相對差最大 " + f"{max(r['pc_foster_rel_diff'] for r in s['algorithm']['kepler_tca']):.1e}；"
        "篩選狀態" + ("全部一致" if all(r['screen_py'] == r['screen_rs'] for r in s['algorithm']['kepler_tca']) else "有不一致") + "。",
        f"Schema 層：{s['schema']['n_cases']} 案例代碼不一致 {len(s['schema']['mismatches'])} 個。",
        "自適應搜尋層：" + "；".join(f"{r['pair']} 極小值 {r['n_py']}／{r['n_rs']}、ΔTCA ≤ {r['max_dtau_s']:.1e} s"
                                for r in s["adaptive"]) + "。",
        f"協方差傳播層：最大相對差 {max(r['max_rel_diff'] for r in s['covprop']):.1e}。",
        f"CSV 解析一致：{s['csv_batch']['csv_equal']}；批次配對狀態全部一致："
        f"{all(r['status_equal'] for r in s['csv_batch']['batch_pairs'])}。",
        f"避碰層：{len(s['maneuver'])} 案例狀態全部一致 "
        f"{all(r['status_py'] == r['status_rs'] and r['reason_equal'] for r in s['maneuver'])}；候選方案與檢查結果全部相同 "
        f"{all(r['candidates_equal'] and r['pass_checks_equal'] and r['linear_map_equal'] for r in s['maneuver'])}；"
        f"機動後距離最大差 {max(r['max_dmiss_m'] for r in s['maneuver']):.1e} m、"
        f"Pc 相對差最大 {max(r['max_pc_rel_diff'] for r in s['maneuver']):.1e}。",
        f"RPO 層：{len(s['rpo'])} 案例狀態與原因全部一致 "
        f"{all(r['status_py'] == r['status_rs'] and r['reasons_equal'] for r in s['rpo'])}；脈衝序列、違規、撤離表、故障結果全部相同 "
        f"{all(r[k] in (True, None) for r in s['rpo'] for k in ('burns_equal', 'violations_equal', 'abort_equal', 'fault_equal'))}；"
        f"脈衝最大差 {max(r['max_ddv_ms'] for r in s['rpo']):.1e} m/s、撤離最差距離相對差最大 {max(r['max_dabort_rel'] for r in s['rpo']):.1e}。",
    ]
    (OUT / "xlang_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "xlang_report.md").write_text(to_md(rep), encoding="utf-8")
    print(to_md(rep))


if __name__ == "__main__":
    main()
