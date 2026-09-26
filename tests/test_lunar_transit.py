"""衛星凌月預報（physics.lunar_transit 與 /api/taipei_lunar）測試。

  1. 幾何單元：弧距離與稠密取樣一致。
  2. 端對端：以合成 ISS 級 TLE，將偵測門檻放寬後，預報事件必須與 1 秒稠密暴力掃描的
     局部最小角距一致（時刻 ±2 s、角距 ±0.02°），且暴力掃描找到的每個近距事件都不可漏報。
  3. API：參數夾限與快取。
"""
import math
from datetime import datetime, timezone

import numpy as np
import pytest
from sgp4.api import Satrec

from scenario04.physics import lunar_transit as L

_L1 = "1 25544U 98067A   24001.50000000  .00016717  00000-0  10270-3 0  9993"
_L2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49514064 10000"


def _azel_unit(az, el):
    return L._unit_from_azel(np.array(az, float), np.array(el, float))


def test_arc_distance_matches_dense_sampling():
    a = _azel_unit(0.0, 30.0)
    b = _azel_unit(90.0, 30.0)
    u = np.stack([a, b])[None]                       # (1,2,3)
    for moon_az, moon_el in [(45, 40), (45, 25), (10, 35), (120, 30), (-30, 20)]:
        m = np.stack([_azel_unit(moon_az, moon_el)] * 2)
        d, k = L.min_arc_distance_deg(u, m, min_el_deg=4.0)
        # 稠密取樣：沿 a→b 之大圓弧（球面線性內插）
        omega = math.acos(float(a @ b))
        ts = np.linspace(0, 1, 20001)[:, None]
        pts = (np.sin((1 - ts) * omega) * a + np.sin(ts * omega) * b) / math.sin(omega)
        dense = np.degrees(np.arccos(np.clip(pts @ m[0], -1, 1))).min()
        assert d[0] == pytest.approx(dense, abs=5e-3), (moon_az, moon_el)


def test_arc_distance_ignores_below_horizon_arcs():
    a = _azel_unit(0.0, -20.0)
    b = _azel_unit(90.0, -20.0)
    u = np.stack([a, b])[None]
    m = np.stack([_azel_unit(45, -20)] * 2)
    d, _ = L.min_arc_distance_deg(u, m, min_el_deg=4.0)
    assert np.isinf(d[0])


@pytest.fixture()
def one_sat(monkeypatch):
    idx = {90000: {"name": "STARLINK-TEST", "line1": _L1, "line2": _L2}}
    monkeypatch.setattr(L, "get_index_for_time", lambda ts: idx)
    return idx


def test_finder_agrees_with_bruteforce(one_sat, monkeypatch):
    monkeypatch.setattr(L, "_COARSE_DEG", 12.0)
    ts = datetime(2024, 1, 7, 0, tzinfo=timezone.utc)      # 此日合成 ISS 與月球在共同可見時段有 ~5° 真實局部最小距離
    res = L.find_lunar_transits(ts, hours=24, near_margin_deg=20.0, min_el_deg=5.0)
    assert res["moon_up_runs"], "24 小時內月球應有升起時段"
    assert res["n_scanned"] == 1

    # 暴力掃描：1 秒間隔
    moon = L._Moon(ts, np.arange(0, 86401, 30.0))
    s = np.arange(0, 86400.0, 1.0)
    sat = Satrec.twoline2rv(_L1, _L2)
    u, _ = L._sat_unit_at(sat, moon.jd0, moon.fr0, s)
    m = moon.unit_at(s)
    sep = L._sep_deg(u, m)
    el_sat = np.degrees(np.arcsin(np.clip(u[:, 2], -1, 1)))
    el_moon = np.degrees(np.arcsin(np.clip(m[:, 2], -1, 1)))
    minima = [i for i in range(1, len(s) - 1)
              if sep[i] <= sep[i - 1] and sep[i] < sep[i + 1] and sep[i] < 10.0
              and el_sat[i] >= 5.0 and el_moon[i] >= 5.0]                 # 真實局部最小（非遮罩邊界）
    assert minima, "合成 ISS 在放寬門檻下應至少有一個近距事件"

    ev_t = [(datetime.fromisoformat(e["t_utc"]) - ts).total_seconds() for e in res["events"]]
    for i in minima:
        j = int(np.argmin([abs(t - s[i]) for t in ev_t]))
        assert abs(ev_t[j] - s[i]) <= 2.0, f"漏報：暴力掃描最小值 t={s[i]} sep={sep[i]:.3f}"
        assert res["events"][j]["sep_deg"] == pytest.approx(sep[i], abs=0.02)

    for e in res["events"]:
        assert e["kind"] in ("transit", "near")
        assert (e["kind"] == "transit") == (e["sep_deg"] <= e["moon_r_deg"])
        assert 0.24 < e["moon_r_deg"] < 0.29
        assert e["sat_el"] >= 5.0 and e["moon_el"] >= 4.0
        assert e["track"] and e["track"][0][0] < 0 < e["track"][-1][0]
        assert -180 <= e["sun_angle_deg"] <= 180 and 0.0 <= e["moon_frac"] <= 1.0
        # 軌跡上最小畫面距離應與 sep_deg 一致
        if e["sep_deg"] < 1.0:
            dmin = min(math.hypot(p[1], p[2]) for p in e["track"])
            assert dmin == pytest.approx(e["sep_deg"], abs=0.12 + 0.05 * e["omega_dps"])


def test_transit_flag_uses_topocentric_radius(one_sat, monkeypatch):
    monkeypatch.setattr(L, "_COARSE_DEG", 12.0)
    res = L.find_lunar_transits(datetime(2024, 1, 7, 0, tzinfo=timezone.utc), hours=24,
                                near_margin_deg=20.0)
    assert res["events"]
    for e in res["events"]:
        if e["kind"] == "transit":
            assert e["chord_s"] > 0
        else:
            assert e["chord_s"] == 0


def test_api_clamps_and_caches(monkeypatch):
    from scenario04 import create_app
    from scenario04.api import passes as api
    calls = []

    def stub(ts, **kw):
        calls.append(kw)
        return {"timestamp": ts.isoformat(), "events": [], "hours": kw["hours"]}
    monkeypatch.setattr(L, "find_lunar_transits", stub)
    api._LUNAR_CACHE.clear()
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get("/api/taipei_lunar?hours=999&near=9&cats=STARLINK,US_EO")
        assert r.status_code == 200
        kw = calls[-1]
        assert kw["hours"] == 48.0 and kw["near_margin_deg"] == 1.0 and kw["cats"] == ["STARLINK", "US_EO"]
        n = len(calls)
        c.get("/api/taipei_lunar?hours=999&near=9&cats=US_EO,STARLINK")
        assert len(calls) == n, "相同參數（類別順序無關）應命中快取"
