"""tools/export_story_md.py：匯出內容、連結編碼、schema 驗證、每種區塊型別皆有處理。"""
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "tools"))
import export_story_md as ex  # noqa: E402

STORY_IDS = [json.loads(p.read_text(encoding="utf-8"))["id"]
             for p in sorted(ex.STORIES.glob("*.json")) if p.name != "schema.json"]


@pytest.mark.parametrize("sid", STORY_IDS)
def test_export_all_stories(sid):
    md = ex.export(sid, with_prov=False)
    st = ex.load_story(sid)
    assert md.startswith("# " + st["title"])
    assert f"/story/{sid}" in md
    assert st.get("updated", "") in md          # 更新日（欄名依語言本地化）
    for sec in st["sections"]:
        assert "## " + sec["title"] in md


def test_url_encoding():
    u = ex.url("/api/story/positions", mode="country", val="日本")
    q = parse_qs(urlparse(u).query)
    assert q["mode"] == ["country"] and q["val"] == ["日本"]
    assert "日本" not in u                      # 已 percent-encode
    assert ex.url("/orbit", norad=26410, start=None).endswith("/orbit?norad=26410")


def test_every_block_type_has_extra():
    fixtures = {
        "table": {"type": "table", "title": "t", "columns": ["a"], "rows": [["1|2"]]},
        "toc": {"type": "toc", "title": "t", "items": [{"label": "L", "anchor": "x", "icon": "i"}]},
        "sat": {"type": "sat", "title": "t", "norads": [25544], "start": "2026-01-01"},
        "positions": {"type": "positions", "title": "t", "mode": "ids", "ids": [1, 2]},
        "embed": {"type": "embed", "title": "t", "url": "/rpo"},
        "groupstats": {"type": "groupstats", "title": "t", "group": "gps"},
        "isrres": {"type": "isrres", "title": "t", "group": "prc_isr"},
        "radar": {"type": "radar", "title": "t", "group": "prc_isr"},
        "skyplot": {"type": "skyplot", "title": "t", "group": "prc_isr"},
        "maneuvers": {"type": "maneuvers", "title": "t"},
        "cdm": {"type": "cdm", "title": "t", "threshold_km": 10},
    }
    for kind, sec in fixtures.items():
        out = ex.sec_extra(sec, "demo")
        assert out, kind
        if kind == "table":
            assert "1\\|2" in out                # 表格內 | 需跳脫
        elif kind == "toc":
            assert "**L**" in out
        else:
            assert ex.BASE_URL in out, kind
            if kind == "sat":
                assert "norad=25544" in out and "start=2026-01-01" in out
            if kind == "positions":
                assert "val=1%2C2" in out
    assert ex.sec_extra({"type": "text", "title": "t"}, "demo") == ""


def test_load_story_validates_schema(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"id": "bad-story", "title": "x",
                               "sections": [{"type": "embed", "title": "no url"}]}), encoding="utf-8")
    monkeypatch.setattr(ex, "STORIES", tmp_path)
    monkeypatch.setattr(ex, "SCHEMA", APP / "scenario04" / "config" / "stories" / "schema.json")
    import jsonschema
    with pytest.raises(jsonschema.ValidationError):
        ex.load_story("bad-story")
    with pytest.raises(SystemExit):
        ex.load_story("nope")


def test_provenance_section_present():
    md = ex.export(STORY_IDS[0], with_prov=True)
    assert "### 資料口徑" in md and "傳播模型" in md
