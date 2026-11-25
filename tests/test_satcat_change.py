"""SATCAT 每日異動：管線（tools/update_satcat.py）的分類／Space-Track 轉換／快照比對／合併，與 API 查詢（scenario04/api/changes.py）。"""
import sys
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "tools"))
import update_satcat as u  # noqa: E402

from scenario04.api import changes as ch  # noqa: E402


def test_classify():
    assert u.classify("catalog", "", "OBJECT A") == "new"
    assert u.classify("name", "OBJECT J", "HULIANWANG DIGUI 249") == "named"
    assert u.classify("name", "TBA - TO BE ASSIGNED", "SJ-24") == "named"
    assert u.classify("name", "OBJECT A", "OBJECT B") == "renamed"
    assert u.classify("name", "STARLINK-1", "STARLINK-1 [DEB]") == "renamed"
    assert u.classify("decay", "", "2026-11-22") == "decayed"
    assert u.classify("decay", "2026-11-21", "2026-11-22") == "decay_fix"
    assert u.classify("object_type", "UNKNOWN", "PAYLOAD") == "type"
    assert u.classify("owner", "TBD", "IT") == "other"


ST_RAW = [
    {"NORAD_CAT_ID": "101512", "CURRENT_NAME": "SZ-24", "PREVIOUS_NAME": None, "CURRENT_INTLDES": "2026-266A", "PREVIOUS_INTLDES": None,
     "CURRENT_COUNTRY": "PRC", "PREVIOUS_COUNTRY": None, "CURRENT_LAUNCH": "2026-11-25", "PREVIOUS_LAUNCH": None,
     "CURRENT_DECAY": None, "PREVIOUS_DECAY": None, "CHANGE_MADE": "2026-11-25 18:01:52"},
    {"NORAD_CAT_ID": "100928", "CURRENT_NAME": "IRIDE-MS1-EAG2-18", "PREVIOUS_NAME": "OBJECT AX", "CURRENT_INTLDES": "2026-227AX",
     "PREVIOUS_INTLDES": "2026-227AX", "CURRENT_COUNTRY": "IT", "PREVIOUS_COUNTRY": "TBD", "CURRENT_LAUNCH": "2026-10-01",
     "PREVIOUS_LAUNCH": "2026-10-01", "CURRENT_DECAY": None, "PREVIOUS_DECAY": None, "CHANGE_MADE": "2026-11-19 18:13:19"},
    {"NORAD_CAT_ID": "62617", "CURRENT_NAME": "SCOT-1 (SCOT)", "PREVIOUS_NAME": "SCOT-1 (SCOT)", "CURRENT_INTLDES": "2025-009J",
     "PREVIOUS_INTLDES": "2025-009J", "CURRENT_COUNTRY": "SING", "PREVIOUS_COUNTRY": "SING", "CURRENT_LAUNCH": "2025-01-14",
     "PREVIOUS_LAUNCH": "2025-01-14", "CURRENT_DECAY": "2026-11-22", "PREVIOUS_DECAY": None, "CHANGE_MADE": "2026-11-25 18:01:52"},
    {"NORAD_CAT_ID": "1", "CURRENT_NAME": "X", "PREVIOUS_NAME": "X", "CURRENT_INTLDES": "1", "PREVIOUS_INTLDES": "1", "CHANGE_MADE": "2026-11-25 00:00:00"},
]


def test_from_spacetrack():
    rows = u.from_spacetrack(ST_RAW)
    k = [(r["norad_id"], r["kind"], r["field"]) for r in rows]
    assert k == [(101512, "new", "catalog"), (100928, "named", "name"), (100928, "other", "owner"), (62617, "decayed", "decay")]
    assert rows[1]["previous"] == "OBJECT AX" and rows[1]["current"] == "IRIDE-MS1-EAG2-18" and rows[1]["source"] == "spacetrack"


def _sc(n, name, typ="PAYLOAD", decay=""):
    return {"norad_id": n, "name": name, "intl": f"2026-001{chr(64 + n)}", "object_type": typ, "owner": "PRC", "launch": "2026-01-01", "decay": decay}


def test_diff_snapshots_and_merge():
    old = [_sc(1, "OBJECT A", "UNKNOWN"), _sc(2, "OBJECT B")]
    new = [_sc(1, "YAOGAN-40 04A", "PAYLOAD"), _sc(2, "OBJECT B", decay="2026-11-20"), _sc(3, "OBJECT C")]
    d = u.diff_snapshots(old, new, "2026-11-25T13:30:45Z", "spacetrack")
    k = sorted((r["norad_id"], r["kind"], r["field"]) for r in d)
    assert k == [(1, "named", "name"), (1, "type", "object_type"), (2, "decayed", "decay"), (3, "new", "catalog")]
    assert all(r["change_made"] == "2026-11-25 13:30:45" and r["source"] == "diff:spacetrack" for r in d)
    # Space-Track 已有的（同 NORAD／欄位／新值）以 Space-Track 為準；類型變更只有快照比對有
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for r in d:
        r["change_made"] = now
    st = [dict(d[0], source="spacetrack", change_made=now)] if d[0]["field"] == "name" else []
    m = u.merge_changes(st, d)
    assert len(m) == 4 and sum(1 for r in m if r["source"] == "spacetrack") == len(st)
    old_row = dict(d[-1], change_made="2000-01-01 00:00:00")
    assert old_row not in u.merge_changes([], [old_row])


def test_api_query():
    rows = [
        {"norad_id": 100928, "change_made": "2026-11-19 18:13:19", "kind": "named", "field": "name", "previous": "OBJECT AX",
         "current": "IRIDE-MS1-EAG2-18", "name": "IRIDE-MS1-EAG2-18", "intl": "2026-227AX", "source": "spacetrack"},
        {"norad_id": 62617, "change_made": "2026-11-25 18:01:52", "kind": "decayed", "field": "decay", "previous": "",
         "current": "2026-11-22", "name": "SCOT-1 (SCOT)", "intl": "2025-009J", "source": "spacetrack"},
        {"norad_id": 5, "change_made": "2026-09-01 00:00:00", "kind": "new", "field": "catalog", "previous": "", "current": "OBJECT A",
         "name": "OBJECT A", "intl": "2026-150A", "source": "spacetrack"},
    ]
    idx = {100928: {"name": "OBJECT AX"}, 62617: {"name": "SCOT-1"}}
    now = datetime(2026, 11, 26, tzinfo=timezone.utc)
    r = ch.query(rows, idx, 30, None, None, "", now=now)
    assert r["total"] == 2 and r["counts"]["named"] == 1 and r["counts"]["decayed"] == 1 and r["counts"]["new"] == 0
    named = next(x for x in r["results"] if x["kind"] == "named")
    assert named["catalog_stale"] and named["catalog_name"] == "OBJECT AX"
    assert ch.query(rows, idx, 30, {"decayed"}, None, "", now=now)["total"] == 1
    assert ch.query(rows, idx, 30, None, {100928}, "", now=now)["total"] == 1
    assert ch.query(rows, idx, 30, None, None, "iride", now=now)["total"] == 1
    assert ch.query(rows, idx, 90, None, None, "", now=now)["total"] == 3
    assert ch.query(rows, idx, 3, None, None, "", now=now)["total"] == 1
