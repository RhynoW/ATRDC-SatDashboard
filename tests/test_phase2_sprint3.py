"""第二階段 Sprint 3：RPO 控制模擬（L7 驗收案例、HCW、abort_to_hold、rtn_offset、Lab API）。"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from scenario04.phase2 import rpo
from scenario04.phase2 import constants as K
from scenario04.phase2.eop import TESTDATA, load_snapshot
from scenario04.phase2.kepler import Elements, state as kstate
from scenario04.phase2.rtn import relative_rtn, rtn_offset_to_gcrf


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


CASES = _load("rpo_cases.json")["cases"]
_CACHE: dict = {}


def _run(case):
    if case["name"] not in _CACHE:
        _CACHE[case["name"]] = rpo.run(_apply(_load("rpo_vbar.json"), case["ops"]), load_snapshot())
    return _CACHE[case["name"]]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_l7_rpo_cases(case):
    """L7：nominal 非線性重播違規 0 次；故障注入觸發撤離且不侵入安全區；違規與設計不可行案例被正確辨識。"""
    rep, exp = _run(case), case["expected"]
    assert rep["status"] == exp["status"]
    assert rep.get("reasons") == exp["reasons"]
    assert rep.get("reason") == exp["reason"]
    if "replay" in rep:
        assert sorted({v["type"] for v in rep["replay"]["violations"]}) == exp["violation_types"]
        assert len(rep["replay"]["burns"]) == exp["n_burns"]
        assert rep["replay"]["total_dv_ms"] == pytest.approx(exp["total_dv_ms"], rel=1e-9)
        assert rep["checks"] == exp["checks"]
    if rep["status"] == "pass":
        assert rep["replay"]["violations"] == []
        assert rep["replay"]["min_dist_m"] >= rep["limits"]["keep_out_m"]
        assert all(e["nl_worst_m"] >= rep["limits"]["keep_out_m"] for e in rep["abort"]["entries"])


def test_fault_injection_details():
    rep = _run(next(c for c in CASES if c["name"] == "fault_comm_loss"))
    f = rep["fault"]
    assert f["status"] == "evaluated" and f["safe"]
    assert f["trigger_t_s"] == pytest.approx(f["comm_loss_start_s"] + rep["limits"]["comm_loss"]["trigger_s"])
    assert f["stored_entry_t_s"] <= f["comm_loss_start_s"]
    assert len(f["nl_min_dist_m"]) == 5 and min(f["nl_min_dist_m"]) >= rep["limits"]["keep_out_m"]
    assert f["coast_min_dist_m"] >= rep["limits"]["keep_out_m"]
    assert f["abort_path_rtn_m"]


def test_base_regression_and_structure():
    rep, exp = _run(CASES[0]), _load("rpo_vbar.expected.json")
    assert [b["kind"] for b in rep["replay"]["burns"]] == [b["kind"] for b in exp["replay"]["burns"]]
    for a, b in zip(rep["replay"]["burns"], exp["replay"]["burns"]):
        assert a["t_s"] == b["t_s"]
        assert np.allclose(a["dv_rtn_ms"], b["dv_rtn_ms"], rtol=1e-9, atol=1e-12)
    assert [e["t_s"] for e in rep["abort"]["entries"]] == [e["t_s"] for e in exp["abort"]["entries"]]
    for a, b in zip(rep["abort"]["entries"], exp["abort"]["entries"]):
        assert a["dv_rtn_ms"] == pytest.approx(b["dv_rtn_ms"], abs=1e-12)
        assert a["nl_worst_m"] == pytest.approx(b["nl_worst_m"], rel=1e-9)
    # 每個控制段起點都有預存撤離脈衝
    starts = {s["start_s"] for s in rep["replay"]["segments"]}
    assert starts <= {e["t_s"] for e in rep["abort"]["entries"]}
    # HCW 設計：轉移到達誤差為 0（線性模型內精確），非線性重播有模型誤差
    for s in rep["design"]["segments"]:
        if s["type"] == "transfer":
            assert s["arrival_error_m"] < 1e-6
    assert max(s.get("arrival_error_m", 0) for s in rep["replay"]["segments"]) > 1.0
    assert rep["applicability"]["hcw_applicable"]


def test_hcw_stm_properties():
    n = math.sqrt(K.GM_KM3_S2 / 6778.137 ** 3)
    P1, P2 = rpo.hcw_stm(n, 700.0), rpo.hcw_stm(n, 1300.0)
    assert np.allclose(P2 @ P1, rpo.hcw_stm(n, 2000.0), atol=1e-9)
    # V-bar 點為平衡點；2:1 自然橢圓一圈後回到原點
    x = np.array([0.0, -1000.0, 0.0, 0.0, 0.0, 0.0])
    assert np.allclose(rpo.hcw_stm(n, 3000.0) @ x, x)
    y0 = -1000.0
    x = np.array([0.0, y0, 0.0, n * y0 / 2, 0.0, 0.0])
    assert np.allclose(rpo.hcw_stm(n, 2 * math.pi / n) @ x, x, atol=1e-6)
    # 雙脈衝瞄準到達目標點
    v0 = rpo.hcw_target([0, -2000, 0], [0, -1000, 0], n, 2400.0, 0)
    assert np.allclose((rpo.hcw_stm(n, 2400.0) @ np.r_[[0, -2000, 0], v0])[:3], [0, -1000, 0], atol=1e-6)


def test_hcw_forced_matches_numeric():
    n = math.sqrt(K.GM_KM3_S2 / 6778.137 ** 3)
    a = np.array([1e-6, -3e-6, 2e-6])
    h, x = 1.0, np.zeros(6)
    A = np.zeros((6, 6))
    A[:3, 3:] = np.eye(3)
    A[3, 0], A[3, 4], A[4, 3], A[5, 2] = 3 * n * n, 2 * n, -2 * n, -n * n
    f = lambda s: A @ s + np.r_[0, 0, 0, a]  # noqa: E731
    for _ in range(3000):
        k1 = f(x); k2 = f(x + 0.5 * h * k1); k3 = f(x + 0.5 * h * k2); k4 = f(x + h * k3)
        x = x + h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
    assert np.allclose(rpo.hcw_forced(n, 3000.0, a), x[:3], rtol=1e-8, atol=1e-8)


def test_rtn_offset_roundtrip():
    r, v, _ = kstate(Elements(6778.137, 0.001, math.radians(51.6), 0.3, 0.2, 0.1), 0.0)
    rc, vc = rtn_offset_to_gcrf(r, v, [10.0, -500.0, 30.0], [0.1, -0.2, 0.05])
    rho, rhod = relative_rtn(r, v, rc, vc)
    assert np.allclose(rho, [10.0, -500.0, 30.0], atol=1e-9)
    assert np.allclose(rhod, [0.1, -0.2, 0.05], atol=1e-9)


def test_abort_perturbations():
    cases = rpo.perturbations([0.0, 0.1, 0.0], 0.05, 2.0)
    assert [c[0] for c in cases] == ["nominal", "mag_plus", "mag_minus", "point_1", "point_2"]
    mags = [float(np.linalg.norm(c[1])) for c in cases]
    assert mags[1] == pytest.approx(0.115) and mags[2] == pytest.approx(0.085)
    assert mags[3] == pytest.approx(0.1) and mags[4] == pytest.approx(0.1)
    ang = math.degrees(math.acos(np.dot(cases[3][1], [0, 0.1, 0]) / 0.01))
    assert ang == pytest.approx(6.0)


def test_e2e_accepts_rtn_offset():
    from scenario04.phase2 import e2e
    sc = _load("rpo_vbar.json")
    rep = e2e.run(sc, load_snapshot())
    assert rep["status"] == "ok"
    assert rep["series"]["d_km"][0] == pytest.approx(5.0, abs=1e-6)


def test_lab_rpo_api():
    from scenario04 import create_app
    c = create_app().test_client()
    r = c.get("/api/lab/rpo/demo")
    assert r.status_code == 200
    demos = r.get_json()["scenarios"]
    assert [d["id"] for d in demos] == [x["name"] for x in CASES]
    sc = next(d["scenario"] for d in demos if d["id"] == "transfer_dv_exceeds")
    j = c.post("/api/lab/rpo", json={"scenario": sc}).get_json()
    assert j["status"] == "invalid_design" and j["reason"] == "transfer_dv_exceeds"
    assert c.post("/api/lab/rpo", json={"nope": 1}).status_code == 400
    page = c.get("/lab")
    assert b"lab_rpo.js" in page.data
