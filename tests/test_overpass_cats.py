"""台北覆蓋頁衛星類別歸屬（overpass_cats.yaml／physics.coverage.get_overpass_candidates）測試。"""
from scenario04.physics.coverage import get_overpass_candidates


def _idx(*names):
    return {i + 1: {"name": n, "line1": "", "line2": ""} for i, n in enumerate(names)}


def _by_cat(*names):
    idx = _idx(*names)
    cand = get_overpass_candidates(idx)
    return {cat: sorted(idx[n]["name"] for n in ids) for cat, ids in cand.items()}


def test_tasa_covers_all_formosat_series_and_chinese_name():
    got = _by_cat("FORMOSAT 3A", "ROCSAT 2", "福爾摩沙衛星五號", "FORMOSAT-5",
                  "FORMOSAT7-1/COSMIC2-1", "FORMOSAT-7R/TRITON", "FORMOSAT-8A", "FORMOSAT-8B")
    assert len(got["TW_TASA"]) == 8


def test_tasa_excludes_lookalikes():
    got = _by_cat("TRITON 1", "COSMIC", "PACE", "LILIUM-3", "IRIS-F2")
    assert got["TW_TASA"] == []


def test_debris_and_rocket_bodies_not_counted():
    got = _by_cat("YAOGAN-30 D", "YAOGAN 30 DEB", "GAOFEN 3", "GAOFEN 3 DEB",
                  "WORLDVIEW 2", "WORLDVIEW 2 DEB", "STARLINK-1234")
    assert got["CN_MIL"] == ["YAOGAN-30 D"]
    assert got["CN_COMM"] == ["GAOFEN 3"]
    assert got["US_EO"] == ["WORLDVIEW 2"]
    assert got["STARLINK"] == ["STARLINK-1234"]


def test_blacksky_and_beijing3_included_but_lookalikes_not():
    got = _by_cat("GLOBAL-12", "GLOBALSTAR M079", "BEIJING 3B", "BEIJING 1 (TSINGHUA)")
    assert got["US_EO"] == ["GLOBAL-12"]
    assert got["CN_COMM"] == ["BEIJING 3B"]
