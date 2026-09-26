"""台北天球視角過頂（physics.skypass 與 /api/taipei_sky）測試。

  1. 物理／幾何單元測試——以一顆合成 TLE 驗證軌跡欄位、方位／仰角範圍、
     星下點與仰角的一致性、碎片濾除與地球同步標記，不依賴 DB。
  2. API 測試——以 stub 取代重運算，驗證參數夾限、快取與頁面資源。
"""
import math
from datetime import datetime, timezone

import pytest

# ISS 級近地軌道之合成 TLE（僅供幾何測試，非即時軌道；名稱借用 STARLINK 以命中類別關鍵字）
_L1 = "1 25544U 98067A   24001.50000000  .00016717  00000-0  10270-3 0  9993"
_L2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49514064 10000"
# 24 小時內軌道週期 ~24 h 之近地球同步軌道（傾角 0.05°、平均運動 1.0027 圈/日）
_GEO_L1 = "1 90001U 20001A   24001.50000000  .00000000  00000-0  00000-0 0  9990"
_GEO_L2 = "2 90001   0.0500  75.0000 0001000   0.0000   0.0000  1.00270000    10"


@pytest.fixture()
def patch_idx(monkeypatch):
    from scenario04.physics import skypass
    idx = {
        90000: {"name": "STARLINK-TEST", "line1": _L1, "line2": _L2},
        90002: {"name": "STARLINK-TEST DEB", "line1": _L1, "line2": _L2},
    }
    monkeypatch.setattr(skypass, "get_index_for_time", lambda ts: idx)
    return idx


def _gc_km(lat1, lon1, lat2, lon2):
    p1, p2, dl = map(math.radians, (lat1, lat2, lon2 - lon1))
    c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
    return 6371.0 * math.acos(max(-1.0, min(1.0, c)))


def test_track_fields_and_ranges(patch_idx):
    from scenario04.physics.skypass import predict_taipei_sky_passes
    d = predict_taipei_sky_passes(datetime(2024, 1, 1, 0, tzinfo=timezone.utc),
                                  hours=24, step_sec=30, mask_deg=5, min_el=5)
    assert d["passes"], "24 小時內應至少有一次 ISS 級過頂"
    for p in d["passes"]:
        assert p["norad_id"] == 90000 and p["cat"] == "STARLINK"
        prev_t = -1
        for t, az, el, lat, lon in p["track"]:
            assert t > prev_t
            prev_t = t
            assert 0.0 <= az < 360.0
            assert 5.0 <= el <= 90.0
            assert -52.5 <= lat <= 52.5 and -180.0 <= lon <= 180.0
        assert p["max_el_deg"] >= 5.0
        assert p["max_el_deg"] == pytest.approx(max(q[2] for q in p["track"]), abs=0.11)


def test_subpoint_geometry_matches_elevation(patch_idx):
    """最高仰角時刻星下點最靠近台北；高仰角 ↔ 地面距離近（仰角≥60° 應在 ~500 km 內）。"""
    from scenario04.physics.skypass import predict_taipei_sky_passes
    from scenario04.config import settings
    d = predict_taipei_sky_passes(datetime(2024, 1, 1, 0, tzinfo=timezone.utc),
                                  hours=24, step_sec=15, mask_deg=5, min_el=5)
    for p in d["passes"]:
        dist = [_gc_km(settings.TAIPEI_LAT, settings.TAIPEI_LON, q[3], q[4]) for q in p["track"]]
        k = max(range(len(p["track"])), key=lambda i: p["track"][i][2])
        assert dist[k] <= min(dist) + 60.0            # 最高點附近即最近點（取樣容差）
        assert max(dist) < 2500.0                      # 5° 仰角遮蔽下 ISS 高度之視界半徑
        if p["max_el_deg"] >= 60:
            assert dist[k] < 500.0


def test_debris_filtered_and_no_stationary_for_leo(patch_idx):
    from scenario04.physics.skypass import predict_taipei_sky_passes
    d = predict_taipei_sky_passes(datetime(2024, 1, 1, 0, tzinfo=timezone.utc),
                                  hours=24, step_sec=30, min_el=5)
    assert all(" DEB" not in p["name"] for p in d["passes"])
    assert not any(p["stationary"] for p in d["passes"])


def test_geostationary_flagged(monkeypatch):
    from scenario04.physics import skypass
    idx = {90003: {"name": "STARLINK-GEOTEST", "line1": _GEO_L1, "line2": _GEO_L2}}
    monkeypatch.setattr(skypass, "get_index_for_time", lambda ts: idx)
    d = skypass.predict_taipei_sky_passes(datetime(2024, 1, 1, 0, tzinfo=timezone.utc),
                                          hours=1, step_sec=60, min_el=0, mask_deg=0)
    assert len(d["passes"]) == 1, "合成同步星（赤道、東經 ~75° 附近）於台北整日可見"
    p = d["passes"][0]
    assert p["stationary"] is True and len(p["track"]) == 2
    assert p["clipped_start"] and p["clipped_end"]


def test_api_clamps_and_caches(monkeypatch):
    from scenario04 import create_app
    from scenario04.api import passes as api
    calls = []

    def stub(ts, **kw):
        calls.append(kw)
        return {"timestamp": ts.isoformat(), "passes": [], "categories": {}, "hours": kw["hours"]}
    monkeypatch.setattr(api, "predict_taipei_sky_passes", stub)
    api._SKY_CACHE.clear()
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get("/api/taipei_sky?hours=99&step_sec=1&min_el=200&max_per_cat=999")
        assert r.status_code == 200
        kw = calls[-1]
        assert kw["hours"] == 6.0 and kw["step_sec"] == 10.0 and kw["min_el"] == 85.0 and kw["max_per_cat"] == 60
        n = len(calls)
        c.get("/api/taipei_sky?hours=99&step_sec=1&min_el=200&max_per_cat=999")
        assert len(calls) == n, "相同參數與同一分鐘內應命中快取"


def test_taipei_page_has_sky_view_assets():
    from scenario04 import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        html = c.get("/taipei").get_data(as_text=True)
        assert 'id="sky-view"' in html and "loadMapOnce" in html
        for path in ("/static/js/taipei_sky.js", "/static/js/taipei_gif.js", "/static/js/sky_catalog.js",
                     "/static/vendor/astronomy/astronomy.browser.min.js"):
            assert c.get(path).status_code == 200, path
