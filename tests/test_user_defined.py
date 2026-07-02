"""使用者自訂資料（TLE / 衛星目錄 / NORAD 監測清單）測試。"""
import pytest
from sgp4.api import Satrec

from scenario04.ingestion import user_defined
from scenario04.ingestion.index import _merge_user_defined
from scenario04.ingestion.user_defined import (
    _parse_tle_text,
    load_tracking_list,
    load_user_catalogue,
    load_user_tles,
)

L1 = "1 25544U 98067A   26182.50000000  .00016717  00000-0  10270-3 0  9007"
L2 = "2 25544  51.6400 208.9163 0006317  69.9862  25.2906 15.49560532 10005"


class TestTleParser:
    def test_two_line(self):
        out = _parse_tle_text(f"{L1}\n{L2}\n", "t.tle")
        assert 25544 in out
        assert out[25544]["name"] == ""
        assert out[25544]["line1"] == L1

    def test_three_line_with_name(self):
        out = _parse_tle_text(f"國際太空站 ISS\n{L1}\n{L2}\n", "t.tle")
        assert out[25544]["name"] == "國際太空站 ISS"

    def test_three_line_3le_prefix(self):
        out = _parse_tle_text(f"0 ISS (ZARYA)\n{L1}\n{L2}\n", "t.tle")
        assert out[25544]["name"] == "ISS (ZARYA)"

    def test_comments_and_blank_lines(self):
        text = f"# 註解\n\n{L1}\n{L2}\n# 尾註解\n"
        out = _parse_tle_text(text, "t.tle")
        assert len(out) == 1

    def test_mismatched_norad_skipped(self):
        bad_l2 = "2 99999" + L2[7:]
        out = _parse_tle_text(f"{L1}\n{bad_l2}\n", "t.tle")
        assert out == {}

    def test_orphan_line2_skipped(self):
        out = _parse_tle_text(f"{L2}\n", "t.tle")
        assert out == {}


class TestSampleFiles:
    """驗證隨附範例檔（user_tle01.tle / user_catalogue01.csv / tracking01.csv）。"""

    def test_sample_tles(self):
        tles = load_user_tles()
        assert {99001, 25544, 42920} <= set(tles)
        # 混用格式：99001/25544 為 3 行（有名稱），42920 為 2 行（無名稱）
        assert "測試衛星" in tles[99001]["name"]
        assert tles[25544]["name"] == "ISS (ZARYA)"
        assert tles[42920]["name"] == ""

    def test_sample_tle_propagates(self):
        """合成的 99001 TLE（改自福衛五號）必須可被 SGP4 解析並傳播。"""
        tles = load_user_tles()
        sat = Satrec.twoline2rv(tles[99001]["line1"], tles[99001]["line2"])
        assert sat.satnum == 99001
        err, r, _ = sat.sgp4(sat.jdsatepoch, sat.jdsatepochF)
        assert err == 0
        assert 6800 < sum(x * x for x in r) ** 0.5 < 7200   # LEO 軌道半徑 (km)

    def test_sample_catalogue(self):
        cat = load_user_catalogue()
        assert {99001, 25544, 42920} <= set(cat)
        assert cat[42920]["name_zh"] == "福爾摩沙衛星五號"
        assert cat[99001]["country"] == "台灣"

    def test_sample_tracking(self):
        items = load_tracking_list()
        ids = [i["norad_id"] for i in items]
        assert set(ids) == {99001, 25544, 42920}
        by_id = {i["norad_id"]: i for i in items}
        assert by_id[99001]["priority"] == "high"       # 高 → high
        assert by_id[25544]["priority"] == "medium"     # 中 → medium
        assert all(i["enabled"] for i in items)
        assert all(i["color"].startswith("#") for i in items)
        # priority 排序：high 在前
        assert items[0]["priority"] == "high"


class TestTrackingCsvParsing:
    def test_disabled_and_autocolor(self, tmp_path, monkeypatch):
        f = tmp_path / "t.csv"
        f.write_text(
            "norad_id,alias,priority,color,enabled,notes\n"
            "111,甲,低,,N,停用\n"
            "222,乙,high,,Y,\n",
            encoding="utf-8-sig")
        monkeypatch.setattr(user_defined.settings, "USER_TRACKING_DIR", tmp_path)
        items = load_tracking_list()
        by_id = {i["norad_id"]: i for i in items}
        assert by_id[111]["enabled"] is False
        assert by_id[222]["enabled"] is True
        assert by_id[111]["color"]   # 自動配色不為空
        assert by_id[222]["priority"] == "high"


class TestIndexMerge:
    def test_merge_adds_user_sat_with_catalogue_override(self):
        idx = {}
        _merge_user_defined(idx)
        assert 99001 in idx
        e = idx[99001]
        assert e["user_defined"] is True
        assert e["name"] == "測試衛星一號"       # catalogue name_zh 覆寫
        assert e["country"] == "台灣"
        assert e["purpose"] == "技術驗證"
        assert e["era"] == "< 1 年"              # launch_date 2026-01-15
        assert e["line1"].startswith("1 99001")

    def test_merge_overrides_existing_tle(self):
        idx = {25544: {
            "name": "OLD", "line1": "x", "line2": "y",
            "country": "美國", "purpose": "有效載荷", "era": "> 10 年",
            "constellation": None,
        }}
        _merge_user_defined(idx)
        e = idx[25544]
        assert e["line1"].startswith("1 25544")   # user TLE 覆蓋
        assert e["name"] == "國際太空站"           # catalogue name_zh 覆寫
        assert e["user_defined"] is True


class TestUserDataApi:
    @pytest.fixture()
    def client(self, monkeypatch):
        from scenario04 import create_app
        from scenario04.api import user_data as ud_api

        # 以範例 user TLE 建 fake index，避免測試時重建完整 DB 索引
        idx = {}
        _merge_user_defined(idx)
        monkeypatch.setattr(ud_api, "get_sat_index", lambda: idx)
        app = create_app()
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_tracking_list(self, client):
        d = client.get("/api/tracking/list").get_json()
        assert d["count"] == 3
        assert d["enabled"] == 3
        aliases = {i["alias"] for i in d["items"]}
        assert "福衛五號" in aliases

    def test_tracking_positions(self, client):
        d = client.get("/api/tracking/positions").get_json()
        assert d["count"] == 3
        assert d["ok_count"] == 3
        by_id = {s["norad_id"]: s for s in d["satellites"]}
        assert by_id[99001]["user_defined"] is True
        assert "lat" in by_id[99001]

    def test_tracking_positions_ids_param(self, client):
        d = client.get("/api/tracking/positions?ids=99001,123456").get_json()
        assert d["count"] == 2
        by_id = {s["norad_id"]: s for s in d["satellites"]}
        assert by_id[99001]["ok"] is True
        assert by_id[123456]["ok"] is False

    def test_user_tles_endpoint(self, client):
        d = client.get("/api/user/tles").get_json()
        assert d["count"] == 3
        by_id = {s["norad_id"]: s for s in d["satellites"]}
        assert by_id[99001]["source_file"] == "user_tle01.tle"

    def test_user_catalogue_endpoint(self, client):
        d = client.get("/api/user/catalogue").get_json()
        assert d["count"] == 3

    def test_globe_page_has_track_button(self, client):
        html = client.get("/").get_data(as_text=True)
        assert "toggleTrackPanel" in html
        assert "NORAD 監測" in html
