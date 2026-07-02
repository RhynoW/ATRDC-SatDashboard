"""使用者自訂 GeoJSON 圖層 + 台灣行政界替換測試。"""
import json

import pytest

from scenario04 import create_app
from scenario04.api.layers import _feature_contains, strip_taiwan_features
from scenario04.config import settings


def _poly_feature(coords):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [coords]}}


# 粗糙台灣輪廓（近似 Natural Earth 110m）
_COARSE_TAIWAN = _poly_feature(
    [[121.78, 24.39], [120.99, 21.97], [120.11, 23.06], [121.13, 25.08], [121.78, 24.39]])
# 日本本州（不含台灣探測點）
_HONSHU = _poly_feature(
    [[140.0, 35.0], [141.0, 40.0], [136.0, 37.0], [140.0, 35.0]])
# 超大範圍多邊形（包含台灣點，但 span 超過護欄 → 不得剔除）
_HUGE = _poly_feature(
    [[100.0, 0.0], [150.0, 0.0], [150.0, 50.0], [100.0, 50.0], [100.0, 0.0]])


class TestStripTaiwan:
    def test_contains_probe(self):
        assert _feature_contains(_COARSE_TAIWAN, 121.0, 23.7)
        assert not _feature_contains(_HONSHU, 121.0, 23.7)

    def test_strip_removes_only_taiwan(self):
        raw = {"type": "FeatureCollection",
               "features": [_HONSHU, _COARSE_TAIWAN, _HUGE]}
        removed = strip_taiwan_features(raw)
        assert removed == 1
        assert len(raw["features"]) == 2
        assert _COARSE_TAIWAN not in raw["features"]

    def test_span_guard_protects_large_features(self):
        raw = {"type": "FeatureCollection", "features": [_HUGE]}
        assert strip_taiwan_features(raw) == 0


@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestUserGeojsonApi:
    def test_list_contains_taiwan_file(self, client):
        d = client.get("/api/layers/user_geojson").get_json()
        names = {f["name"] for f in d}
        assert "Taiwan-admin-ploygon.geojson" in names
        tw = next(f for f in d if f["name"] == "Taiwan-admin-ploygon.geojson")
        assert tw["features"] == 22
        assert "error" not in tw

    def test_fetch_taiwan_file(self, client):
        r = client.get("/api/layers/user_geojson/Taiwan-admin-ploygon.geojson")
        assert r.status_code == 200
        geo = json.loads(r.get_data(as_text=True))
        assert geo["type"] == "FeatureCollection"
        assert len(geo["features"]) == 22

    def test_missing_file_404(self, client):
        assert client.get("/api/layers/user_geojson/nope.geojson").status_code == 404

    def test_invalid_names_rejected(self, client):
        assert client.get("/api/layers/user_geojson/..%5Cconfig%5Cssn_stations.geojson").status_code in (400, 404)
        assert client.get("/api/layers/user_geojson/evil.txt").status_code == 400

    def test_borders_taiwan_stripped(self, client):
        """精細台灣檔存在 → 全球國界不再有包含台灣探測點的小範圍 feature。"""
        assert settings.TAIWAN_ADMIN_FILE.exists()
        r = client.get("/api/layers/borders")
        assert r.status_code == 200
        geo = json.loads(r.get_data(as_text=True))
        assert geo["features"], "borders 應有資料（data/borders.geojson）"
        assert not any(_feature_contains(f, 121.0, 23.7) for f in geo["features"])
