"""RPO 案例變體（variant）與錯配 elset 剔除測試。"""
import duckdb

from scenario04.physics import rpo

_L1 = "1 25544U 98067A   24001.50000000  .00016717  00000-0  10270-3 0  9993"
_L2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49514064 10000"


def test_preset_for_variant_and_fallback():
    base = rpo.preset_for(69673, 67689)
    v2 = rpo.preset_for(69673, 67689, "cycle2")
    assert base["title"] != v2["title"] and "第二次" in v2["title"]
    assert rpo.preset_for(69673, 67689, "nope") is base
    assert rpo.valid_variant(69673, 67689, "cycle2") == "cycle2"
    assert rpo.valid_variant(69673, 67689, "nope") is None
    assert rpo.valid_variant(58573, 59884, "cycle2") is None


def test_cache_file_name_includes_variant():
    assert rpo._cache_file(1, 2).name == "rpo_scene_1_2.json"
    assert rpo._cache_file(1, 2, "cycle2").name == "rpo_scene_1_2_cycle2.json"


def test_load_recs_excludes_epoch_ranges():
    con = duckdb.connect(":memory:")
    con.execute("create table raw_tle_archive(norad_id integer, epoch_utc timestamp, line1 varchar, line2 varchar)")
    for day in (5, 6, 7, 8, 9, 10):
        con.execute("insert into raw_tle_archive values (?, ?, ?, ?)", [99, f"2026-09-{day:02d} 12:00:00", _L1, _L2])
    recs, ep, _name, df = rpo._load_recs(con, 99)
    assert len(df) == 6
    recs, ep, _name, df = rpo._load_recs(con, 99, exclude=[("2026-09-07T00:00:00Z", "2026-09-09T18:00:00Z")])
    assert [t.day for t in df["epoch_utc"]] == [5, 6, 10]
    assert rpo._load_recs(con, 99, exclude=[("2026-09-01T00:00:00Z", "2026-09-30T00:00:00Z")])[0] is None
