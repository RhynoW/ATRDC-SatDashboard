"""第二階段 Sprint 1：自適應近接搜尋完整性、SGP4 星曆、協方差傳播、CSV、批次、Lab API。"""
import json
import math

import numpy as np
import pytest

from scenario04.phase2 import e2e, kepler
from scenario04.phase2.batch import run_batch
from scenario04.phase2.covprop import CovarianceTrack
from scenario04.phase2.csvio import events_to_csv, parse_secondaries
from scenario04.phase2.eop import TESTDATA, load_snapshot
from scenario04.phase2.ephemeris import NumericalEphemeris, RelativeState, Sgp4Ephemeris
from scenario04.phase2.forces import ForceModel
from scenario04.phase2.frames import EarthRotationTable
from scenario04.phase2.integrator import propagate
from scenario04.phase2.tca import find_minima, find_minima_adaptive
from scenario04.phase2.timescale import UtcTime

TLES = json.loads((TESTDATA / "tle_fixture.json").read_text(encoding="utf-8"))["tles"]
START = UtcTime.parse("2026-11-18T00:00:00Z")


def _tle_rel(a, b, dur):
    ert = EarthRotationTable(START, -1.0, dur + 1.0, load_snapshot())
    p = Sgp4Ephemeris(TLES[a]["line1"], TLES[a]["line2"], START, ert, (0.0, dur))
    s = Sgp4Ephemeris(TLES[b]["line1"], TLES[b]["line2"], START, ert, (0.0, dur))
    return RelativeState(p, s)


def _brute(rel, dur, thr):
    """每秒掃描的暴力參考（L0 完整性基準）。"""
    res = find_minima(rel, 0.0, dur, 1.0, thr)
    return [a for a in res["events"] if a.flag == "interior"]


@pytest.mark.parametrize("pair,thr", [(("25544", "48274"), 2000.0), (("59103", "68762"), 1000.0)])
def test_adaptive_matches_brute_force_on_real_tles(pair, thr):
    dur = 6 * 3600.0
    rel = _tle_rel(*pair, dur)
    ref = _brute(rel, dur, thr)
    got = [a for a in find_minima_adaptive(rel.rv, rel.rva, 0.0, dur, 60.0, thr)["events"] if a.flag == "interior"]
    assert len(ref) == len(got) and len(ref) > 0
    for a, b in zip(ref, got):
        assert abs(a.tau - b.tau) < 1e-3 and abs(a.miss_km - b.miss_km) < 1e-6


def test_adaptive_with_huge_coarse_step_still_complete():
    """粗掃 900 s（遠大於兩次接近的間隔）時，細分仍須找回門檻內全部極小值。"""
    e1 = kepler.Elements(6878.137, 0.0005, math.radians(51.6), 0.5, 0.0, 0.0)
    e2 = kepler.Elements(6878.537, 0.0005, math.radians(97.4), 0.5, 0.0, 0.001)
    rv = lambda t: tuple(b - a for a, b in zip(kepler.state(e1, t)[:2], kepler.state(e2, t)[:2]))  # noqa: E731
    rva = lambda t: tuple(b - a for a, b in zip(kepler.state(e1, t), kepler.state(e2, t)))  # noqa: E731
    dur, thr = 86400.0, 3000.0
    ref = [a for a in find_minima(rva, 0.0, dur, 1.0, thr)["events"] if a.flag == "interior"]
    got = [a for a in find_minima_adaptive(rv, rva, 0.0, dur, 900.0, thr)["events"] if a.flag == "interior"]
    assert [round(a.tau, 3) for a in ref] == [round(a.tau, 3) for a in got]


def test_low_relative_velocity_flag_on_docked_pair():
    dur = 3 * 3600.0
    rel = _tle_rel("69673", "67689", dur)
    res = find_minima_adaptive(rel.rv, rel.rva, 0.0, dur, 60.0, 100.0)
    assert res["events"] and all(a.flag in ("low_relative_velocity", "window_edge_start", "window_edge_end", "degenerate")
                                 for a in res["events"])


def test_sgp4_ephemeris_acceleration_is_consistent():
    dur = 3600.0
    ert = EarthRotationTable(START, -1.0, dur + 1.0, load_snapshot())
    e = Sgp4Ephemeris(TLES["25544"]["line1"], TLES["25544"]["line2"], START, ert, (0.0, dur))
    r, v, a = e.rva(1000.0)
    a2b = -398600.4418 * r / np.linalg.norm(r) ** 3
    assert np.linalg.norm(a - a2b) / np.linalg.norm(a2b) < 3e-3      # J2 量級差異


def test_covariance_track_matches_finite_difference_stm():
    """協方差傳播 = Φ P0 Φᵀ，Φ 以擾動初始狀態的數值傳播中央差分求得（二體＋J2–J4）。"""
    eop = load_snapshot()
    dur = 3 * 3600.0
    ert = EarthRotationTable(START, -1.0, dur + 1.0, eop)
    el = kepler.Elements(6878.137, 0.001, 0.9, 0.3, 0.2, 0.1)
    x0 = np.concatenate(kepler.state(el, 0.0)[:2])
    fm = ForceModel(zonal=True)
    nom = NumericalEphemeris(propagate(x0[:3], x0[3:], 0.0, dur, 10.0, fm, ert))
    P0 = np.diag([0.05 ** 2, 0.2 ** 2, 0.05 ** 2, 1e-4 ** 2, 1e-4 ** 2, 1e-4 ** 2])
    trk = CovarianceTrack(nom, P0, 0.0, dur, 20.0, ert, zonal=True)
    deltas = [1e-3, 1e-3, 1e-3, 1e-6, 1e-6, 1e-6]
    Phi = np.zeros((6, 6))
    for k in range(6):
        dx = np.zeros(6)
        dx[k] = deltas[k]
        xp = propagate((x0 + dx)[:3], (x0 + dx)[3:], 0.0, dur, 10.0, fm, ert)
        xm = propagate((x0 - dx)[:3], (x0 - dx)[3:], 0.0, dur, 10.0, fm, ert)
        Phi[:3, k] = (xp.r[-1] - xm.r[-1]) / (2 * deltas[k])
        Phi[3:, k] = (xp.v[-1] - xm.v[-1]) / (2 * deltas[k])
    ref = Phi @ P0 @ Phi.T
    got = trk.at(dur)
    assert np.abs(got[:3, :3] - ref[:3, :3]).max() < 1e-4 * np.abs(ref[:3, :3]).max()
    mid = trk.at(5000.0)           # 節點之間（RK4 子步）
    assert np.all(np.linalg.eigvalsh(mid) > 0)


def test_csv_parse_matches_expected():
    text = (TESTDATA / "batch_secondaries.csv").read_text(encoding="utf-8")
    exp = json.loads((TESTDATA / "batch_secondaries.expected.json").read_text(encoding="utf-8"))
    res = parse_secondaries(text)
    assert res["rows"] == exp["rows"]
    assert [[e["line"], e["code"]] for e in res["errors"]] == exp["error_codes"]
    assert parse_secondaries("")["errors"][0]["code"] == "CSV_EMPTY"
    assert parse_secondaries("name\nfoo\n")["errors"][0]["code"] == "CSV_NO_ID_COLUMN"


def _lookup(n):
    t = TLES.get(str(n))
    return (t["name"], t["line1"], t["line2"]) if t else None


def test_batch_runs_and_exports_csv():
    rows = parse_secondaries((TESTDATA / "batch_secondaries.csv").read_text(encoding="utf-8"))["rows"]
    rep = run_batch({"primary": {"norad_id": 25544}, "secondaries": rows, "start": "2026-11-18T00:00:00Z",
                     "duration_s": 86400.0, "threshold_km": 100.0}, load_snapshot(), _lookup)
    assert rep["status"] == "ok"
    ok = [p for p in rep["pairs"] if p["status"] == "ok"]
    assert len(ok) == 4
    css = next(p for p in ok if p["secondary_norad"] == 48274)
    assert css["n_events"] == 1 and abs(css["events"][0]["miss_km"] - 45.91) < 0.05
    csv_text = events_to_csv(rep["events"])
    assert csv_text.splitlines()[0].startswith("primary_norad,secondary_norad")
    assert len(csv_text.splitlines()) == len(rep["events"]) + 1
    bad = run_batch({"primary": {"norad_id": 25544}, "secondaries": [], "start": "2026-11-18T00:00:00Z",
                     "duration_s": 8 * 86400.0, "threshold_km": 10.0}, load_snapshot(), _lookup)
    assert bad["status"] == "error"


def test_e2e_propagated_and_tle_scenarios():
    for name, mode in (("e2e_leo_propagated.json", "propagated"), ("e2e_tle_iss_css.json", "demo")):
        sc = json.loads((TESTDATA / name).read_text(encoding="utf-8"))
        rep = e2e.run(sc, load_snapshot())
        assert rep["status"] == "ok", rep.get("validation")
        assert rep["events"] and all(e["pc"]["covariance_mode"] == mode for e in rep["events"])
        if mode == "propagated":
            m = min(rep["events"], key=lambda e: e["miss_km"])["applicability"]["metrics"]
            assert {"cov_det_rel_change", "cov_axis_rel_change", "cov_rot_deg"} <= set(m)


def test_lab_api_smoke():
    from scenario04 import create_app
    c = create_app().test_client()
    assert c.get("/lab").status_code == 200
    demo = c.get("/api/lab/demo").get_json()
    assert any(d["id"] == "e2e-leo-synthetic-01" for d in demo["scenarios"])
    sc = json.loads((TESTDATA / "e2e_leo_synthetic.json").read_text(encoding="utf-8"))
    r = c.post("/api/lab/scenario", json=sc).get_json()
    assert r["status"] == "ok" and r["events"]
    bad = c.post("/api/lab/scenario", json={"schema": "x"}).get_json()
    assert bad["status"] == "invalid_scenario"
    tle = TLES["48274"]
    r = c.post("/api/lab/batch", json={"primary": {"line1": TLES["25544"]["line1"], "line2": TLES["25544"]["line2"], "name": "ISS"},
                                       "secondaries_csv": f"line1,line2\n\"{tle['line1']}\",\"{tle['line2']}\"\n",
                                       "start": "2026-11-18T00:00:00Z", "duration_s": 86400, "threshold_km": 100}).get_json()
    assert r["status"] == "ok" and r["pairs"][0]["n_events"] == 1
    r = c.post("/api/lab/batch.csv", json={"events": r["events"]})
    assert r.status_code == 200 and r.data.decode("utf-8-sig").startswith("primary_norad")
