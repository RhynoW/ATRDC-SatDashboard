"""StoryMap（/story）冒煙測試：頁面、故事 JSON、群組 TLE 覆蓋與各 API 端點。

依賴打包 DB（run.py 之 DB 優先序：scenario-advanced01/DB → scenario04/DB slim）。
"""
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]


def _pick_db() -> Path | None:
    for d in (APP_DIR / "DB", APP_DIR / "scenario04" / "DB"):
        if d.is_dir():
            c = sorted(d.glob("*slim*.duckdb"), key=lambda p: p.stat().st_mtime, reverse=True)
            if c:
                return c[0]
    return None


@pytest.fixture(scope="module")
def client():
    if _pick_db() is None:
        pytest.skip("無打包 DB")
    from scenario04 import create_app
    from scenario04.ingestion.index import invalidate_index
    invalidate_index()
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_story_pages(client):
    for path in ("/story", "/story/integrated-showcase"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "js/story.js" in resp.get_data(as_text=True)
    assert client.get("/static/js/story.js").status_code == 200


def test_story_list_and_json(client):
    lst = client.get("/api/story/list").get_json()
    ids = {s["id"] for s in lst}
    assert {"integrated-showcase", "japan-launches-2026", "starlink-lifecycle",
            "cluster-samba-tango"} <= ids
    d = client.get("/api/story/integrated-showcase").get_json()
    assert d["id"] == "integrated-showcase" and d["sections"]
    assert client.get("/api/story/no-such-story").status_code == 404


def test_story_groups_have_tle(client):
    g = client.get("/api/story/groups").get_json()
    assert set(g) == {"gps", "beidou", "starlink", "oneweb", "prc_isr", "prc_comm"}
    # 打包 DB 應涵蓋各群組（門檻取保守值）
    assert g["gps"]["n"] >= 30 and g["beidou"]["n"] >= 30
    assert g["starlink"]["n"] >= 5000 and g["oneweb"]["n"] >= 500
    assert g["prc_isr"]["n"] >= 400 and g["prc_comm"]["n"] >= 300


def test_story_positions_group(client):
    d = client.get("/api/story/positions?mode=group&val=gps").get_json()
    assert d["count"] >= 30 and len(d["sats"]) == d["count"]
    norad, lat, lon, alt = d["sats"][0]
    assert -90 <= lat <= 90 and -180 <= lon <= 180 and 15000 < alt < 25000
    assert client.get("/api/story/positions?mode=group&val=nope").status_code == 400


def test_story_positions_japan_ids(client):
    """japan-launches-2026 明列之 NORAD（含 6 位數 100270）應皆有 TLE 可定位。"""
    ids = [69502, 69503, 69504, 69505, 69506, 69507, 100270]
    d = client.get("/api/story/positions?mode=ids&val=" + ",".join(map(str, ids))).get_json()
    assert {s[0] for s in d["sats"]} == set(ids)
    assert client.get("/api/story/positions?mode=constellation&val=QZSS").get_json()["count"] >= 5


def test_story_starlink_lifecycle_sats(client):
    """starlink-lifecycle 三階段代表星（含 6 位數 100294）應有 TLE 與軌道歷史。"""
    ids = [100294, 48881, 53506]
    d = client.get("/api/story/positions?mode=ids&val=" + ",".join(map(str, ids))).get_json()
    assert {s[0] for s in d["sats"]} >= {100294, 48881}   # 53506 再入後可能消失
    h = client.get("/api/orbit/history?norad=48881&start=2026-03-31").get_json()
    assert isinstance(h, dict) and not h.get("error")


def test_story_cluster_history(client):
    """cluster-samba-tango：Samba/Tango 自 2024-01-01 起的軌道歷史應在打包 DB 中。"""
    for n in (26410, 26464):
        h = client.get(f"/api/orbit/history?norad={n}&start=2024-01-01").get_json()
        assert not h.get("error") and h.get("n_days", 0) >= 200, n


def test_story_group_stats_and_isr(client):
    s = client.get("/api/story/group_stats?group=beidou").get_json()
    assert s["n"] >= 30 and s["regimes"] and s["alt_hist"] and s["sample"]
    r = client.get("/api/story/isr_resolution?group=prc_isr").get_json()
    assert r["sensor"] and r["series"] and r["unknown"] < 100
    assert client.get("/api/story/group_stats?group=x").status_code == 400


def test_story_maneuvers(client):
    resp = client.get("/api/story/maneuvers")
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["year"] == 2026 and set(d["groups"]) >= {"gps", "starlink"}


def test_story_track(client):
    g = client.get("/api/story/positions?mode=group&val=prc_isr").get_json()
    leo = next(s for s in g["sats"] if s[3] < 2000)
    d = client.get(f"/api/story/track?norad={leo[0]}").get_json()
    assert d["norad"] == leo[0] and "name" in d
    assert client.get("/api/story/track?norad=abc").status_code == 400


def test_story_provenance(client):
    d = client.get("/api/story/provenance").get_json()
    assert d["tle_epoch_max"] and d["propagator"].startswith("SGP4") and "TEME" in d["frame"]
    assert "Chan" in d["pc_model"] and "Δa" in d["maneuver_method"]
