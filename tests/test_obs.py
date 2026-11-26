"""觀測資料模型（scenario04/api/obs.py）：時間解析、Alpha-5、幾何（與 skyfield 驗證過的公式）、交叉驗證判定。"""
from datetime import datetime, timezone

from scenario04.api import obs as ob

L1 = "1 25544U 98067A   26329.50000000  .00016717  00000-0  10270-3 0  9994"
L2 = "2 25544  51.6400 208.9163 0006317  69.9862 290.2104 15.50000000000017"


def test_parse_time_and_rounding():
    assert ob.fmt_time(ob.parse_time("2026-11-25T11:02:30.5Z")) == "2026-11-25 11:02:30.500"
    assert ob.parse_time("2026-11-25 11:02") == datetime(2026, 11, 25, 11, 2, tzinfo=timezone.utc)
    for bad in ("2026-02-30 00:00:00", "2026-11-25 24:00:00", "2026/11/25 11:02:30", ""):
        assert ob.parse_time(bad) is None, bad
    assert ob.rnd(2.3456, 2) == 2.35 and ob.rnd(-2.5, 0) == -3.0


def test_alpha5():
    assert ob.tle_norad("1 A1419U 26260J") == 101419
    assert ob.tle_norad("1 Z9999U") == 339999
    assert ob.tle_norad("1 25544U") == 25544
    assert ob.tle_norad("1 I1234U") is None


def test_precession_identity_at_j2000():
    t = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
    v = ob.precess_to_j2000((1.0, 2.0, 3.0), t)
    assert all(abs(a - b) < 1e-12 for a, b in zip(v, (1.0, 2.0, 3.0)))
    # 26 年歲差約 0.36°
    t2 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    u = ob.precess_to_j2000((1.0, 0.0, 0.0), t2)
    assert 0.3 < ob.sep_deg((1.0, 0.0, 0.0), u) < 0.4


def _ctx():
    return ob.Ctx({25544: {"name": "ISS (ZARYA)", "line1": L1, "line2": L2}, 25545: {"name": "OBJECT B", "line1": L1.replace("25544", "25545"), "line2": L2.replace("25544", "25545")}},
                  {25544: {"name": "ISS (ZARYA)", "intl": "1998-067A"}, 25545: {"name": "OBJECT B", "intl": "1998-067B"}})


def test_check_all_layers():
    ctx = _ctx()
    t = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    stn = ob.station_ecef(25.033, 121.5654, 10)
    lk = ob.look(ctx.prop(25544).pos(t), t, stn)
    data = {
        "pois": [{"name": "台北站", "lat": 25.033, "lon": 121.5654, "alt_m": 10}],
        "observations": [
            {"id": "a", "norad": 25544, "station": "台北站", "time": "2026-11-25 12:00:00", "ra_deg": lk["ra"] + 0.1, "dec_deg": lk["dec"]},
            {"id": "b", "norad": 25544, "station": "台北站", "time": "2026-11-25 12:00:00", "az_deg": lk["az"], "el_deg": lk["el"] - 5 if lk["el"] > -80 else lk["el"] + 5},
            {"id": "c", "norad": 25544, "station": "不存在", "time": "2026-11-25 12:00:00", "ra_deg": 1, "dec_deg": 1},
        ],
        "orbits": [{"id": "o", "line1": L1, "line2": L2, "review": "accepted"}],
        "claims": [{"id": "x", "norad": 25545, "name": "某衛星", "source_type": "amateur", "review": "accepted"},
                   {"id": "y", "norad": 25545, "name": "另一個名字", "source_type": "news", "review": "unreviewed"}],
    }
    r = ob.check_all(data, ctx.idx, ctx.sc)
    o = {c["id"]: c for c in r["observations"]}
    assert o["a"]["status"] == "consistent" and abs(o["a"]["sep_deg"] - 0.1 * abs(__import__("math").cos(__import__("math").radians(lk["dec"])))) < 0.01
    assert o["b"]["status"] == "inconsistent" and o["b"]["frame"] == "azel"
    assert o["c"]["errors"] == ["找不到觀測站（地面 POI）"]
    assert r["orbits"][0]["status"] == "consistent" and r["orbits"][0]["dmax_km"] == 0.0
    s = {x["norad"]: x for x in r["sats"]}
    assert s[25544]["identity_status"] == "confirmed" and s[25544]["orbit_status"] == "corroborated" and s[25544]["observation_status"] == "mixed"
    assert s[25545]["identity_status"] == "disputed" and s[25545]["mission_status"] == "unknown" and s[25545]["capability_status"] == "unknown"
    assert s[25545]["layers"]["launch_group"] == {"value": "1998-067", "confidence": "high"}
    # 否決另一個主張 → 只剩已接受（中可信度）→ probable
    data["claims"][1]["review"] = "rejected"
    s = {x["norad"]: x for x in ob.check_all(data, ctx.idx, ctx.sc)["sats"]}
    assert s[25545]["identity_status"] == "probable" and s[25545]["layers"]["individual"]["value"] == "某衛星"
