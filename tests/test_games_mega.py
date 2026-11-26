"""百萬顆長期風險：背景族群分類（scenario04/api/games.py；規則與 Rust src/games_mega.rs 相同）。"""
from scenario04.api import games as g


def test_classify_rules():
    c = "2018-11-26"
    assert g.mega_classify("PAYLOAD", "2024-01-01", 560.0, 540.0, c) == (14, 0)
    assert g.mega_classify("PAYLOAD", "2010-01-01", 560.0, 540.0, c) == (14, 1)
    assert g.mega_classify("PAYLOAD", "", 560.0, 540.0, c) == (14, 1)
    assert g.mega_classify("DEBRIS", "2024-01-01", 800.0, 800.0, c) == (24, 2)
    assert g.mega_classify("ROCKET BODY", "2024-01-01", 900.0, 880.0, c) == (27, 1)
    assert g.mega_classify("UNKNOWN", "", 200.0, 200.0, c) == (0, 1)
    assert g.mega_classify("PAYLOAD", "2024-01-01", 36000.0, 35000.0, c) is None
    assert g.mega_classify("PAYLOAD", "2024-01-01", 2000.0, 2000.0, c) is None
    assert g.mega_classify("PAYLOAD", "2024-01-01", None, 500.0, c) is None


def test_background_from_db():
    from scenario04.ingestion.db import resolve_db
    db = resolve_db()
    if db is None:
        import pytest
        pytest.skip("沒有資料庫")
    d = g.mega_background(db, "2018-11-26")
    assert len(d["active"]) == len(d["derelict"]) == len(d["debris"]) == 72
    assert d["total"] == sum(d["active"]) + sum(d["derelict"]) + sum(d["debris"])
    assert d["total"] > 10000
    # 550 km 附近（Starlink）運作中衛星最多的高度格之一
    assert max(range(72), key=lambda i: d["active"][i]) in range(10, 18)
