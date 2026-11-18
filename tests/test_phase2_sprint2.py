"""第二階段 Sprint 2：避碰決策（L6 驗收案例、線性預篩對非線性驗證、STM、執行誤差、Lab API）。"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from scenario04.phase2 import maneuver
from scenario04.phase2 import constants as K
from scenario04.phase2.eop import TESTDATA, load_snapshot
from scenario04.phase2.forces import ForceModel
from scenario04.phase2.frames import EarthRotationTable
from scenario04.phase2.integrator import propagate
from scenario04.phase2.ephemeris import NumericalEphemeris
from scenario04.phase2.timescale import UtcTime


def _load(name):
    return json.loads((TESTDATA / name).read_text(encoding="utf-8"))


def _apply(doc, ops):
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


@pytest.fixture(scope="module")
def eop():
    return load_snapshot()


@pytest.fixture(scope="module")
def base_report(eop):
    return maneuver.run(_load("maneuver_leo.json"), eop)


CASES = _load("maneuver_cases.json")["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_l6_maneuver_cases(case, eop, base_report):
    """L6：可行案例找到方案、限制衝突回報正確原因、網格內無解回報 no_solution_found。"""
    rep = base_report if not case["ops"] else maneuver.run(_apply(_load("maneuver_leo.json"), case["ops"]), eop)
    exp = case["expected"]
    assert rep["status"] == exp["status"]
    assert rep.get("reason") == exp["reason"]
    if exp["best"] is None:
        assert rep.get("best") is None
    else:
        b = rep["best"]
        assert abs(b["burn_tau_s"] - exp["best"]["burn_tau_s"]) < 1e-6
        assert b["direction"] == exp["best"]["direction"]
        assert abs(b["dv_ms"] - exp["best"]["dv_ms"]) < 1e-12
        assert b["passed"] == exp["best"]["passed"]
        assert rep["searched"]["verified"] == exp["verified"]
        assert rep["searched"]["passed"] == exp["passed"]
    if rep["status"] == "feasible_solution_found":
        b = rep["best"]
        crit = rep["criteria"]
        assert b["event"]["miss_km"] >= crit["min_miss_km"]
        if crit["pc_target_used"]:
            assert b["pc_after"] <= crit["pc_target"]
        assert b["dv_ms"] <= rep["feasibility"]["dv_search_ms"][1] + 1e-12
        lo, hi = (UtcTime.parse(x) for x in rep["feasibility"]["window_utc"])
        bt = UtcTime.parse(b["burn_utc"])
        assert (bt.mjd - lo.mjd) * 86400 + bt.sec - lo.sec >= -1e-6
        assert (hi.mjd - bt.mjd) * 86400 + hi.sec - bt.sec >= -1e-6


def test_base_regression(base_report):
    exp = _load("maneuver_leo.expected.json")
    rep = base_report
    assert rep["status"] == exp["status"]
    assert len(rep["candidates"]) == len(exp["candidates"])
    for a, b in zip(rep["candidates"], exp["candidates"]):
        assert a["burn_utc"] == b["burn_utc"] and a["direction"] == b["direction"]
        assert abs(a["event"]["miss_km"] - b["event"]["miss_km"]) < 1e-9
        assert abs(a["event"]["tca_tau_s"] - b["event"]["tca_tau_s"]) < 1e-6
        pa, pb = a["pc_after"], b["pc_after"]
        assert abs(pa - pb) <= 1e-12 + 1e-9 * abs(pb)
    assert rep["reference"]["event"]["miss_km"] == pytest.approx(exp["reference"]["event"]["miss_km"], abs=1e-9)


def test_linear_prediction_matches_nonlinear(base_report):
    """線性 STM 預測的機動後距離與非線性驗證相差 < 2%（傳播 ≤ 5 h、Δv < 0.5 m/s）。"""
    for c in base_report["candidates"]:
        lin, act = c["linear"]["miss_km"], c["event"]["miss_km"]
        assert abs(lin - act) / act < 0.02


def test_reference_model_and_physics(base_report):
    rep = base_report
    ref = rep["reference"]["event"]
    tgt = rep["target"]
    # 狀態向量主星：參考軌跡（自 τ_h 重新積分）與基準篩選的事件幾乎一致（內插誤差級）
    assert abs(ref["miss_km"] - tgt["miss_km"]) < 1e-5
    assert abs(ref["tca_tau_s"] - tgt["tca_tau_s"]) < 1e-3
    for c in rep["candidates"]:
        # 沿跡向 Δv：Δa ≈ 2a²vΔv/μ，−T 降低半長軸
        assert (c["delta_a_km"] < 0) == (c["direction"] == "-T")
        assert c["burn_rel_tca_s"] <= -1800.0 + 1e-6
        assert c["propellant_kg"] == pytest.approx(260.0 * (1 - math.exp(-c["dv_ms"] / (220.0 * K.MAN_G0_MS2))))
        assert c["execution_error_included"] is True
    assert rep["secondary_screening"]["n_objects"] == 4
    assert "未涵蓋完整目錄" in rep["secondary_screening"]["coverage_note"]


def test_stm_matches_finite_difference(eop):
    """_Stm 的 Φ_rv 與沿跡向 Δv 有限差分的位置響應一致（二體＋J2–J4，2 小時）。"""
    start = UtcTime.parse("2026-11-20T00:00:00Z")
    ert = EarthRotationTable(start, -1.0, 7300.0, eop)
    fm = ForceModel(zonal=True, drag=False)
    r0 = np.array([215.3357059388151, 4740.941021012885, 4969.580669570353])
    v0 = np.array([-6.9546790172395765, -2.089713045506579, 2.2894798706190342])
    tr = propagate(r0, v0, 0.0, 7200.0, 10.0, fm, ert)
    stm = maneuver._Stm(NumericalEphemeris(tr), 0.0, 20.0, ert, True)
    Phi = stm.at(7200.0)
    u = v0 / np.linalg.norm(v0)
    dv = 1e-5
    rp = propagate(r0, v0 + dv * u, 0.0, 7200.0, 10.0, fm, ert).r[-1]
    rm = propagate(r0, v0 - dv * u, 0.0, 7200.0, 10.0, fm, ert).r[-1]
    fd = (rp - rm) / (2 * dv)
    lin = Phi[:3, 3:] @ u
    assert np.linalg.norm(lin - fd) / np.linalg.norm(fd) < 1e-4


def test_execution_error_covariance():
    u = np.array([0.0, 1.0, 0.0])
    Q = maneuver._exec_error_cov(u, 1e-3, {"included": True, "mag_frac_sigma": 0.02, "point_deg_sigma": 1.0})
    assert Q[1, 1] == pytest.approx((0.02 * 1e-3) ** 2)
    assert Q[0, 0] == pytest.approx((1e-3 * math.radians(1.0)) ** 2)
    assert Q[2, 2] == pytest.approx(Q[0, 0])
    assert np.allclose(Q, Q.T)
    assert not maneuver._exec_error_cov(u, 1e-3, {"included": False}).any()
    assert not maneuver._exec_error_cov(u, 1e-3, None).any()


def test_pc_linear_approximation():
    C2 = np.array([[0.04, 0.0], [0.0, 0.01]])
    p = maneuver._pc_lin(np.array([0.0, 0.0]), C2, 0.01)
    assert p == pytest.approx(0.01 ** 2 / (2 * math.sqrt(0.04 * 0.01)))
    assert maneuver._pc_lin(np.array([1.0, 0.0]), C2, 0.01) < p
    assert maneuver._pc_lin(np.array([0.0, 0.0]), None, 0.01) is None


def test_lab_maneuver_api():
    from scenario04 import create_app
    app = create_app()
    c = app.test_client()
    r = c.get("/api/lab/maneuver/demo")
    assert r.status_code == 200
    demos = r.get_json()["scenarios"]
    assert [d["id"] for d in demos] == [x["name"] for x in CASES]
    sc = next(d["scenario"] for d in demos if d["id"] == "dv_budget_below_min")
    r = c.post("/api/lab/maneuver", json={"scenario": sc, "screening_norad_ids": []})
    j = r.get_json()
    assert r.status_code == 200 and j["status"] == "infeasible_constraints"
    assert j["reason"] == "dv_budget_below_min_dv" and j["screening_unresolved"] == []
    r = c.post("/api/lab/maneuver", json={"nope": 1})
    assert r.status_code == 400
    page = c.get("/lab")
    assert page.status_code == 200 and b"lab_maneuver.js" in page.data
