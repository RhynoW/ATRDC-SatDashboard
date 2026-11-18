"""第二階段 Sprint 0：框架、時間、力模型、積分、TCA、Pc、適用性篩選、Schema、端到端。

對應 v0.3 分層驗收矩陣：L0 求根精度、L1 框架轉換、L4 Pc 算法（自洽部分）、L5 適用性篩選、L9 可重現性。
"""
import copy
import json
import math

import erfa
import numpy as np
import pytest

from scenario04.phase2 import constants as K
from scenario04.phase2 import e2e, frames as F, kepler, rtn
from scenario04.phase2.eop import TESTDATA, EopValues, load_snapshot
from scenario04.phase2.forces import (ExponentialAtmosphere, ForceModel, accel_drag, accel_zonal_itrf,
                                      zonal_potential)
from scenario04.phase2.integrator import hermite5, propagate
from scenario04.phase2.pc import gauss_legendre, pc_chan, pc_foster, project
from scenario04.phase2.schema import validate
from scenario04.phase2.screening import screen
from scenario04.phase2.tca import brent_root, find_minima
from scenario04.phase2.timescale import UtcTime

AS = K.ARCSEC_TO_RAD


class FixedEop:
    def __init__(self, v):
        self.v = v

    def at(self, _mjd):
        return self.v


VALLADO_EOP = EopValues(xp_rad=-0.140682 * AS, yp_rad=0.333309 * AS, ut1_utc_s=-0.4399619, lod_s=0.0015563,
                        dx_rad=-0.000205 * AS, dy_rad=-0.000136 * AS, dat_s=32.0, predicted=False)
VALLADO_T = UtcTime.parse("2004-04-06T07:51:28.386009Z")
R_TEME = np.array([5094.18016210, 6127.64465050, 6380.34453270])
V_TEME = np.array([-4.746131487, 0.785818041, 5.531931288])


# ── 時間 ──────────────────────────────────────────────────────────────────────

def test_eop_dat_matches_sofa_leap_table():
    eop = load_snapshot()
    for iso in ("2024-01-01T00:00:00Z", "2025-06-30T12:00:00Z", "2026-11-20T00:00:00Z"):
        t = UtcTime.parse(iso)
        dt = erfa.dat(*[int(x) for x in iso[:10].split("-")], t.sec / 86400.0)
        assert eop.at(t.mjd_float()).dat_s == dt


def test_tt_matches_sofa():
    eop = load_snapshot()
    t = UtcTime.parse("2026-11-20T06:30:15.25Z")
    tt1, tt2 = t.tt_jd(eop.at(t.mjd_float()).dat_s)
    a1, a2 = erfa.utctai(K.MJD0 + t.mjd, t.sec / 86400.0)
    b1, b2 = erfa.taitt(a1, a2)
    assert abs(((tt1 - b1) + (tt2 - b2)) * 86400.0) < 1e-6


def test_utctime_roundtrip_and_plus():
    t = UtcTime.parse("2026-11-20T23:59:50.5Z")
    u = t.plus(20.0)
    assert u.iso() == "2026-11-21T00:00:10.500000Z"


# ── 框架（L1）───────────────────────────────────────────────────────────────

def test_vallado_teme_itrf_gcrf():
    er = F.earth_rotation(VALLADO_T, FixedEop(VALLADO_EOP))
    ri, vi = F.teme_to_itrf(R_TEME, V_TEME, er)
    assert np.abs(ri - [-1033.4793830, 7901.2952754, 6380.3565958]).max() < 2e-5          # 2 cm
    assert np.abs(vi - [-3.225636520, -2.872451450, 5.531924446]).max() < 1e-8           # 0.01 mm/s
    rg, vg = F.itrf_to_gcrf(ri, vi, er)
    assert np.abs(rg - [5102.508958, 6123.011401, 6378.136928]).max() < 2e-5
    assert np.abs(vg - [-4.743220157, 0.790536497, 5.533755727]).max() < 1e-8


def test_frame_roundtrips():
    er = F.earth_rotation(VALLADO_T, FixedEop(VALLADO_EOP))
    for fwd, inv in ((F.gcrf_to_itrf, F.itrf_to_gcrf), (F.teme_to_gcrf, F.gcrf_to_teme),
                     (F.teme_to_itrf, F.itrf_to_teme)):
        r2, v2 = inv(*fwd(R_TEME, V_TEME, er), er)
        assert np.abs(r2 - R_TEME).max() < 1e-9 and np.abs(v2 - V_TEME).max() < 1e-12


def test_c2t_matches_sofa_c2t06a():
    ev = EopValues(VALLADO_EOP.xp_rad, VALLADO_EOP.yp_rad, VALLADO_EOP.ut1_utc_s, VALLADO_EOP.lod_s,
                   0.0, 0.0, 32.0, False)
    er = F.earth_rotation(VALLADO_T, FixedEop(ev))
    tt = VALLADO_T.tt_jd(32.0)
    ut = VALLADO_T.ut1_jd(ev.ut1_utc_s)
    M = erfa.c2t06a(tt[0], tt[1], ut[0], ut[1], ev.xp_rad, ev.yp_rad)
    assert np.abs(M - er.m_gcrf_to_itrf()).max() < 1e-11


def test_rotation_table_interpolation_error():
    eop = load_snapshot()
    start = UtcTime.parse("2026-11-20T00:00:00Z")
    tab = F.EarthRotationTable(start, 0.0, 86400.0, eop)
    worst = 0.0
    for tau in np.linspace(0.0, 86400.0, 37) + 123.4:
        a = tab.at(tau).m_gcrf_to_itrf()
        b = F.earth_rotation(start.plus(tau), eop).m_gcrf_to_itrf()
        worst = max(worst, float(np.abs(a - b).max()))
    assert worst < 1e-9          # 7000 km × 1e-9 = 7 mm


def test_covariance_transform_consistency():
    er = F.earth_rotation(VALLADO_T, FixedEop(VALLADO_EOP))
    rng = np.random.default_rng(1)
    A = rng.normal(size=(6, 6))
    P = A @ A.T + 6 * np.eye(6)
    P2 = F.transform_covariance(F.transform_covariance(P, F.gcrf_to_itrf, er), F.itrf_to_gcrf, er)
    assert np.abs(P2 - P).max() < 1e-9 * np.abs(P).max()


# ── RTN 速度語意 ─────────────────────────────────────────────────────────────

def test_rtn_conventions_roundtrip_and_rotating_derivative():
    el = kepler.Elements(6878.137, 0.001, 0.9, 0.3, 0.2, 0.1)
    r, v, _ = kepler.state(el, 0.0)
    P = np.diag([0.01, 0.25, 0.01, 1e-8, 2e-8, 1e-8])
    for conv in rtn.CONVENTIONS:
        back = rtn.cov_rtn_to_gcrf(rtn.cov_gcrf_to_rtn(P, r, v, conv), r, v, conv)
        assert np.abs(back - P).max() < 1e-15
    # 旋轉座標導數：鄰近軌道 δr(t) 在 RTN 的時間導數 ≈ rotating_frame_derivative 給的速度分量
    el2 = kepler.Elements(6878.637, 0.001, 0.9002, 0.3, 0.2, 0.1001)
    def drtn(t):
        r1, v1, _ = kepler.state(el, t)
        r2, _, _ = kepler.state(el2, t)
        return rtn.rtn_matrix(r1, v1) @ (r2 - r1)
    h = 0.5
    num = (drtn(h) - drtn(-h)) / (2 * h)
    r1, v1, _ = kepler.state(el, 0.0)
    r2, v2, _ = kepler.state(el2, 0.0)
    J = rtn.jacobian_gcrf_to_rtn(r1, v1, "rotating_frame_derivative")
    ana = (J @ np.concatenate([r2 - r1, v2 - v1]))[3:]
    assert np.abs(num - ana).max() < 2e-5 * max(1e-3, np.abs(num).max())


# ── 力模型 ───────────────────────────────────────────────────────────────────

def test_zonal_accel_is_gradient_of_potential():
    for r in ([6878.0, 120.0, 900.0], [4000.0, -3000.0, 4500.0], [100.0, 200.0, 7000.0]):
        r = np.array(r)
        h = 1e-3
        g = np.array([(zonal_potential(r + h * e) - zonal_potential(r - h * e)) / (2 * h) for e in np.eye(3)])
        a = accel_zonal_itrf(r)
        assert np.abs(a - g).max() < 1e-9 * np.abs(a).max() + 1e-15


def test_drag_uses_rotating_atmosphere_once():
    er = F.earth_rotation(VALLADO_T, FixedEop(VALLADO_EOP))
    r = np.array([6778.0, 0.0, 300.0])
    v = np.array([0.0, 7.6, 1.0])
    a = accel_drag(r, v, er, 0.022, ExponentialAtmosphere())
    _ri, vi = F.gcrf_to_itrf(r, v, er)
    v_rel = er.m_gcrf_to_itrf().T @ vi            # 地固速度轉回 GCRF＝v − ω×r
    assert np.allclose(v - np.cross(er.omega_gcrf(), r), v_rel, atol=1e-12)
    cosang = np.dot(a, v_rel) / (np.linalg.norm(a) * np.linalg.norm(v_rel))
    assert cosang < -0.999999999


# ── 積分與內插 ───────────────────────────────────────────────────────────────

def test_hermite5_reproduces_quintic():
    c = np.array([[1.0, -2.0, 0.5], [0.3, 0.1, -0.2], [2.0, 0.0, 1.0], [-1.0, 1.0, 0.0], [0.5, 0.2, 0.1], [0.1, -0.3, 0.2]])
    p = lambda t: sum(c[k] * t ** k for k in range(6))
    dp = lambda t: sum(k * c[k] * t ** (k - 1) for k in range(1, 6))
    ddp = lambda t: sum(k * (k - 1) * c[k] * t ** (k - 2) for k in range(2, 6))
    h = 2.0
    for s in (0.0, 0.3, 0.77, 1.0):
        x, dx, ddx = hermite5(p(0), dp(0), ddp(0), p(h), dp(h), ddp(h), h, s)
        assert np.allclose([x, dx, ddx], [p(s * h), dp(s * h), ddp(s * h)], atol=1e-12)


def test_rk4_two_body_matches_kepler_one_day():
    el = kepler.Elements(6878.137, 0.001, 0.9, 0.3, 0.2, 0.1)
    r0, v0, _ = kepler.state(el, 0.0)
    tr = propagate(r0, v0, 0.0, 86400.0, 10.0, ForceModel(zonal=False), None)
    r1, _, _ = kepler.state(el, 86400.0)
    assert np.linalg.norm(tr.r[-1] - r1) < 1e-3       # 1 m／天
    rm, _, _ = tr.state(43205.0)
    assert np.linalg.norm(rm - kepler.state(el, 43205.0)[0]) < 1e-3


# ── TCA（L0）────────────────────────────────────────────────────────────────

def _analytic_tca(e1, e2, t_lo, t_hi):
    def g(t):
        r1, v1, _ = kepler.state(e1, t)
        r2, v2, _ = kepler.state(e2, t)
        return float(np.dot(r2 - r1, v2 - v1))
    ts = np.arange(t_lo, t_hi, 5.0)
    gs = [g(t) for t in ts]
    roots = []
    for k in range(len(ts) - 1):
        if gs[k] < 0 <= gs[k + 1]:
            roots.append(brent_root(g, ts[k], ts[k + 1], gs[k], gs[k + 1], xtol=1e-10))
    return roots


@pytest.mark.parametrize("case", ["crossing", "head_on"])
def test_tca_pipeline_matches_analytic(case):
    e1 = kepler.Elements(6878.137, 0.0005, math.radians(51.6), 0.5, 0.0, 0.0)
    if case == "crossing":
        e2 = kepler.Elements(6878.537, 0.0005, math.radians(97.4), 0.5, 0.0, 0.001)
    else:   # 逆行：相對速度約 2v
        e2 = kepler.Elements(6878.337, 0.0005, math.radians(180 - 51.6), 0.5 + math.pi, math.pi, -0.0002)
    t_hi = 3 * 3600.0
    roots = _analytic_tca(e1, e2, 0.0, t_hi)
    exp = min(roots, key=lambda t: np.linalg.norm(kepler.state(e2, t)[0] - kepler.state(e1, t)[0]))
    fm = ForceModel(zonal=False)
    p = propagate(*kepler.state(e1, 0.0)[:2], 0.0, t_hi, 10.0, fm, None)
    s = propagate(*kepler.state(e2, 0.0)[:2], 0.0, t_hi, 10.0, fm, None)
    rel = lambda t: tuple(b - a for a, b in zip(p.state(t), s.state(t)))
    res = find_minima(rel, 0.0, t_hi, 10.0)
    got = min(res["all_minima"], key=lambda a: a.miss_km)
    d_exp = np.linalg.norm(kepler.state(e2, exp)[0] - kepler.state(e1, exp)[0])
    v_exp = np.linalg.norm(kepler.state(e2, exp)[1] - kepler.state(e1, exp)[1])
    assert abs(got.tau - exp) < 1e-3                  # L0：TCA ≤ 1 ms
    assert abs(got.miss_km - d_exp) < 1e-3            # L0：距離 ≤ 1 m
    assert abs(np.linalg.norm(got.v_rel) - v_exp) < 1e-6
    assert got.dgdt > 0 and got.flag == "interior"
    if case == "head_on":
        assert v_exp > 14.0


def test_window_edge_flag():
    e1 = kepler.Elements(6878.137, 0.0, 0.9, 0.0, 0.0, 0.0)
    e2 = kepler.Elements(6878.137, 0.0, 0.9, 0.0, 0.0, 0.01)     # 共軌、前方 → 距離幾乎不變
    rel = lambda t: tuple(b - a for a, b in zip(kepler.state(e1, t), kepler.state(e2, t)))
    res = find_minima(rel, 0.0, 600.0, 10.0)
    assert all(a.flag != "interior" or a.dgdt > 0 for a in res["all_minima"])


# ── Pc（L4 自洽）────────────────────────────────────────────────────────────

@pytest.mark.parametrize("miss,C,R", [
    ((0.2, 0.0), [[0.05, -0.03], [-0.03, 1.0]], 0.01),
    ((0.0, 0.0), [[0.01, 0.0], [0.0, 0.04]], 0.02),
    ((1.0, 0.5), [[0.3, 0.1], [0.1, 0.2]], 0.5),
])
def test_foster_matches_dblquad(miss, C, R):
    from scipy import integrate
    C = np.array(C)
    Ci = np.linalg.inv(C)
    nrm = 1 / (2 * np.pi * np.sqrt(np.linalg.det(C)))
    f = lambda y, x: nrm * np.exp(-0.5 * np.array([x, y]) @ Ci @ np.array([x, y]))
    ref, _ = integrate.dblquad(f, miss[0] - R, miss[0] + R,
                               lambda x: miss[1] - math.sqrt(max(R * R - (x - miss[0]) ** 2, 0)),
                               lambda x: miss[1] + math.sqrt(max(R * R - (x - miss[0]) ** 2, 0)),
                               epsabs=1e-16, epsrel=1e-12)
    assert abs(pc_foster(np.array(miss), C, R) / ref - 1) < 1e-9


def test_chan_close_to_foster_small_hbr():
    C = np.array([[0.05, -0.03], [-0.03, 1.0]])
    for d in (0.0, 0.1, 0.3):
        f = pc_foster(np.array([d, 0.0]), C, 0.01)
        c = pc_chan(np.array([d, 0.0]), C, 0.01)
        assert abs(c / f - 1) < 1e-3


def test_gauss_legendre_integrates_polynomials():
    x, w = gauss_legendre(32)
    assert abs(w.sum() - 2.0) < 1e-14
    assert abs((w * x ** 10).sum() - 2.0 / 11) < 1e-14


# ── 適用性篩選（L5）─────────────────────────────────────────────────────────

def _kepler_rel(e1, e2):
    return lambda t: tuple(b - a for a, b in zip(kepler.state(e1, t), kepler.state(e2, t)))


def test_screen_states():
    e1 = kepler.Elements(6878.137, 0.0005, math.radians(51.6), 0.5, 0.0, 0.0)
    e2 = kepler.Elements(6878.537, 0.0005, math.radians(97.4), 0.5, 0.0, 0.001)
    rel = _kepler_rel(e1, e2)
    roots = _analytic_tca(e1, e2, 0.0, 3 * 3600.0)
    t0 = min(roots, key=lambda t: np.linalg.norm(rel(t)[0]))
    C3 = lambda t: np.diag([0.04, 0.25, 0.04])
    P = 2 * math.pi * math.sqrt(e1.a_km ** 3 / K.GM_KM3_S2)
    ok = screen(rel, t0, C3, "demo", 0.01, P, roots, (0.0, 3 * 3600.0))
    assert ok["status"] == "passes_initial_screen"
    assert screen(rel, t0, None, "demo", 0.01, P, roots, (0.0, 3 * 3600.0))["status"] == "undetermined"
    bad = lambda t: np.diag([0.04, -0.25, 0.04])
    assert screen(rel, t0, bad, "demo", 0.01, P, roots, (0.0, 3 * 3600.0))["status"] == "undetermined"
    # 慢速共面（GEO 型）：相對速度很小 → 交會時長過長
    g1 = kepler.Elements(42164.0, 0.0002, 0.001, 0.0, 0.0, 0.0)
    g2 = kepler.Elements(42164.5, 0.0003, 0.0012, 0.0, 0.0, 0.00002)
    relg = _kepler_rel(g1, g2)
    rg = _analytic_tca(g1, g2, 0.0, 2 * 86400.0)
    tg = min(rg, key=lambda t: np.linalg.norm(relg(t)[0]))
    Pg = 2 * math.pi * math.sqrt(g1.a_km ** 3 / K.GM_KM3_S2)
    res = screen(relg, tg, lambda t: np.diag([1.0, 4.0, 1.0]), "demo", 0.01, Pg, rg, (0.0, 2 * 86400.0))
    assert res["status"] in ("fails_initial_screen", "undetermined")
    assert res["status"] != "passes_initial_screen"


# ── Schema ───────────────────────────────────────────────────────────────────

def _apply(doc, ops):
    d = copy.deepcopy(doc)
    for op in ops:
        *head, last = op["path"]
        tgt = d
        for k in head:
            tgt = tgt[k]
        if op["op"] == "set":
            tgt[last] = op["value"]
        else:
            del tgt[last]
    return d


def test_schema_cases():
    spec = json.loads((TESTDATA / "schema_cases.json").read_text(encoding="utf-8"))
    base = json.loads((TESTDATA / spec["base"]).read_text(encoding="utf-8"))
    for case in spec["cases"]:
        got = validate(_apply(base, case["ops"]))["codes"]
        assert got == sorted(case["expected_codes"]), case["name"]


# ── 端到端與可重現性（L9）──────────────────────────────────────────────────

def test_e2e_leo_synthetic_regression():
    sc = json.loads((TESTDATA / "e2e_leo_synthetic.json").read_text(encoding="utf-8"))
    rep = e2e.run(sc, load_snapshot())
    assert rep["status"] == "ok"
    ev = min(rep["events"], key=lambda e: e["miss_km"])
    assert ev["applicability"]["status"] == "passes_initial_screen"
    exp = json.loads((TESTDATA / "e2e_leo_synthetic.expected.json").read_text(encoding="utf-8"))
    ee = min(exp["events"], key=lambda e: e["miss_km"])
    assert abs(ev["tca_tau_s"] - ee["tca_tau_s"]) < 1e-6
    assert abs(ev["miss_km"] - ee["miss_km"]) < 1e-9
    assert abs(ev["pc"]["foster"] / ee["pc"]["foster"] - 1) < 1e-9
    rep2 = e2e.run(sc, load_snapshot())
    assert json.dumps(rep, sort_keys=True) == json.dumps(rep2, sort_keys=True)
