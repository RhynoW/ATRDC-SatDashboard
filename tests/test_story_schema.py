"""故事 JSON schema 驗證：config/stories/*.json 全數須符合 schema.json。"""
import json
from pathlib import Path

import jsonschema
import pytest

STORIES = Path(__file__).resolve().parents[1] / "scenario04" / "config" / "stories"
SCHEMA = json.loads((STORIES / "schema.json").read_text(encoding="utf-8"))
STORY_FILES = sorted(p for p in STORIES.glob("*.json") if p.name != "schema.json")


@pytest.mark.parametrize("path", STORY_FILES, ids=[p.stem for p in STORY_FILES])
def test_story_matches_schema(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.validate(d, SCHEMA)


def test_story_ids_unique_and_match_filename():
    ids = [json.loads(p.read_text(encoding="utf-8"))["id"] for p in STORY_FILES]
    assert len(ids) == len(set(ids))


def test_anchors_referenced_exist():
    """table.row_anchors 與 toc.items[].anchor 必須指向同一故事內存在的 section anchor。"""
    for p in STORY_FILES:
        d = json.loads(p.read_text(encoding="utf-8"))
        anchors = {s.get("anchor") for s in d["sections"] if s.get("anchor")}
        for s in d["sections"]:
            for a in s.get("row_anchors", []):
                assert not a or a in anchors, (p.name, a)
            for it in s.get("items", []):
                assert it["anchor"] in anchors, (p.name, it["anchor"])


def test_schema_rejects_bad_story():
    bad = {"id": "Bad ID", "title": "x", "sections": [{"type": "sat", "title": "no norads"}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)
