"""Flask app 冒煙測試（Phase 1.4）：頁面與不依賴 DB 的端點。"""
import pytest

from scenario04 import create_app


@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "css/globe.css" in html
    assert "js/globe.js" in html
    assert "cesiumContainer" in html


def test_taipei_page(client):
    resp = client.get("/taipei")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "css/taipei.css" in html
    assert "js/taipei.js" in html
    assert "window.CESIUM_ION_TOKEN" in html
    # token 已由模板注入，不應殘留原始 placeholder
    assert "CESIUM_TOKEN_PLACEHOLDER" not in html


def test_static_assets(client):
    for path in ("/static/css/globe.css", "/static/js/globe.js",
                 "/static/css/taipei.css", "/static/js/taipei.js"):
        resp = client.get(path)
        assert resp.status_code == 200, path


def test_taipei_js_uses_injected_token(client):
    js = client.get("/static/js/taipei.js").get_data(as_text=True)
    assert "window.CESIUM_ION_TOKEN" in js
    assert "CESIUM_TOKEN_PLACEHOLDER" not in js


def test_api_textures(client):
    resp = client.get("/api/textures")
    assert resp.status_code == 200
    data = resp.get_json()
    keys = {item["key"] for item in data}
    assert {"default", "2k", "topo", "natural_earth"} <= keys


def test_ssn_stations_layer(client):
    resp = client.get("/api/layers/ssn_stations")
    assert resp.status_code == 200
    geo = resp.get_json()
    assert geo["type"] == "FeatureCollection"
    assert len(geo["features"]) == 29
    names = {f["properties"]["name"] for f in geo["features"]}
    assert "GEODSS Socorro" in names


def test_admin_reload_cats(client):
    resp = client.post("/api/admin/reload_cats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["ssn_stations"] == 29
    assert data["classification_rules"]["country_map"] >= 20


def test_positions_requires_params(client):
    resp = client.get("/api/positions")
    assert resp.status_code == 400
