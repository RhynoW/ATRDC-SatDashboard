"""離軌 Starlink 分段校準再入預測：區間加寬、快取服務、API 合併、太空天氣路徑。"""
import json

import pytest

from scenario04 import create_app
from scenario04.services import deorbit_forecast as dfm


def test_widen_window_uses_wider_of_two_rules():
    tn = 1_800_000_000.0
    # B 範圍僅 ±0.5 h，剩餘 20 h → ±25% = ±5 h 應勝出
    res = {"reentry_unix": tn, "early_unix": tn - 1800, "late_unix": tn + 1800, "hours_from_last_tle": 20.0}
    out = dfm.widen_window(res)
    assert out["early_unix"] == pytest.approx(tn - 5 * 3600)
    assert out["late_unix"] == pytest.approx(tn + 5 * 3600)
    assert out["window_early_utc"].endswith("Z") and "window_rule" in out
    # B 範圍較寬（-10 h / +12 h）時保留 B 範圍
    res2 = dict(res, early_unix=tn - 36000, late_unix=tn + 43200)
    out2 = dfm.widen_window(res2)
    assert out2["early_unix"] == tn - 36000 and out2["late_unix"] == tn + 43200
    # 原物件不被修改
    assert res["early_unix"] == tn - 1800


def test_widen_window_beyond_horizon_passthrough():
    res = {"reentry_unix": None, "beyond_horizon": True, "hours_from_last_tle": None}
    assert dfm.widen_window(res) is res


def test_compact_keeps_list_fields_only():
    res = {"reentry_utc": "2026-09-30T01:00:00Z", "window_early_utc": "a", "window_late_utc": "b",
           "hours_from_last_tle": 12.0, "beyond_horizon": False, "latest_segment_flag": "jump",
           "segments": [{"t_start": "s", "t_end": "e", "B": 0.02, "thrust_flag": None, "resid_m": 3.0}],
           "space_weather": {"source": "x", "persisted_from": "2026-09-27"},
           "timing_s": {"total": 1.2}, "stats": {"big": 1}}
    c = dfm._compact(res)
    assert c["segments"] == [{"t": "e", "B": 0.02, "flag": None}]
    assert c["space_weather_persisted_from"] == "2026-09-27" and c["timing_s"] == 1.2
    assert "stats" not in c
    assert dfm._compact({"error": "boom"}) == {"error": "boom"}


def test_service_reads_cache_file_and_age(tmp_path, monkeypatch):
    cache = tmp_path / "deorbit_forecast.json"
    monkeypatch.setattr(dfm, "CACHE_FILE", cache)
    svc = dfm.DeorbitForecastService()
    assert svc.get() is None and svc.age_s() is None
    dfm._write_atomic({"generated_at": "2026-09-28T00:00:00+00:00", "n": 0, "forecasts": {}}, cache)
    assert json.loads(cache.read_text(encoding="utf-8"))["n"] == 0
    assert svc.get()["n"] == 0
    assert svc.age_s() > 0


@pytest.fixture()
def client(monkeypatch):
    fake_list = {"items": [{"norad_id": 11111, "name": "STARLINK-X", "alt_km": 200.0},
                           {"norad_id": 22222, "name": "STARLINK-Y", "alt_km": 300.0}],
                 "total_matching": 2, "shown": 2, "generated_at": "2026-09-28T00:00:00+00:00"}
    fake_fc = {"generated_at": "2026-09-28T00:00:00+00:00", "n": 1, "method": "m", "window_rule": "w",
               "forecasts": {"11111": {"reentry_utc": "2026-09-29T00:00:00Z"}}}
    import scenario04.api.starlink_census as api
    monkeypatch.setattr(api, "list_deorbiting_starlinks", lambda limit=100: json.loads(json.dumps(fake_list)))
    monkeypatch.setattr(dfm.deorbit_forecast_service, "get", lambda: fake_fc)
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_deorbiting_api_merges_seg(client):
    d = client.get("/api/starlink/deorbiting").get_json()
    seg = {it["norad_id"]: it.get("seg") for it in d["items"]}
    assert seg[11111]["reentry_utc"] == "2026-09-29T00:00:00Z"
    assert seg[22222] is None
    assert d["forecast"]["n"] == 1 and d["forecast"]["method"] == "m"


def test_deorbit_forecast_endpoint(client, monkeypatch):
    r = client.get("/api/starlink/deorbit_forecast")
    assert r.status_code == 200 and r.get_json()["forecasts"]["11111"]
    monkeypatch.setattr(dfm.deorbit_forecast_service, "get", lambda: None)
    r2 = client.get("/api/starlink/deorbit_forecast")
    assert r2.status_code == 202 and r2.get_json()["status"] == "computing"


def test_deorbit_page_has_seg_column(client):
    # 離軌名單已併入 /starlink 分頁；舊網址 302 轉址
    r = client.get("/starlink-deorbit")
    assert r.status_code == 302 and r.headers["Location"].endswith("/starlink?tab=deorbit")
    html = client.get("/starlink").get_data(as_text=True)
    assert 'id="panel-deorbit"' in html and "starlink_tabs.js" in html
    import pathlib
    js = (pathlib.Path(__file__).resolve().parents[1] / "scenario04/web/static/js/starlink_tabs.js").read_text(encoding="utf-8")
    assert "th_seg" in js and "segCell" in js and "sparkB" in js
    assert "低估" not in js  # 線性粗估是高估


def test_old_starlink_pages_redirect_to_tabs(client):
    for old, tab in (("/starlink-census", "census"), ("/starlink-v3", "v3"), ("/starlink-deorbit", "deorbit")):
        r = client.get(old)
        assert r.status_code == 302 and r.headers["Location"].endswith(f"/starlink?tab={tab}")


def test_space_weather_default_path_prefers_env_then_db(tmp_path, monkeypatch):
    from scenario04.physics.segmented_ma import SpaceWeather
    f = tmp_path / "SW-All.csv"
    f.write_text("DATE,F10.7_OBS\n", encoding="utf-8")
    monkeypatch.setenv("SW_ALL_PATH", str(f))
    assert str(SpaceWeather.default_path()) == str(f)
    monkeypatch.delenv("SW_ALL_PATH")
    p = SpaceWeather.default_path()
    assert p is not None and str(p).endswith("SW-All.csv")


def test_refresh_runs_subprocess_and_reloads(tmp_path, monkeypatch):
    import subprocess
    cache = tmp_path / "deorbit_forecast.json"
    monkeypatch.setattr(dfm, "CACHE_FILE", cache)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        dfm._write_atomic({"generated_at": "2026-09-29T00:00:00+00:00", "n": 3, "elapsed_s": 1.0,
                           "forecasts": {}}, cache)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(dfm.subprocess, "run", fake_run)
    svc = dfm.DeorbitForecastService()
    assert svc.refresh()["n"] == 3
    assert calls[0][1:] == ["-m", "scenario04.services.deorbit_forecast"]

    monkeypatch.setattr(dfm.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"))
    assert svc.refresh()["error"] == "rc=1"
    assert svc.get()["n"] == 3          # 失敗時保留舊快取
