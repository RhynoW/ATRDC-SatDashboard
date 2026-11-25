"""建議名稱（scenario04/api/suggest.py）：拼音轉中文、證據等級判定（official／inferred／group_inferred／conflict）。"""
from scenario04.api import suggest as sg


def test_to_zh():
    cases = {
        "Yaogan 30 hao 01 zu 01 xing": "遙感三十號01組01星",
        "YG51": "遙感五十一號",
        "YG53/YG56": "遙感五十三號／遙感五十六號",
        "Tanluzhe": "探路者",
        "Hai Yang 2 hao 05 xing": "海洋二號05星",
        "Shiyan 28 hao B 01 xing": "試驗二十八號B星01星",
        "Jiguang Xingzuo 01 xing": "Jiguang Xingzuo 01星",
        "Jilin-1 Gaofen 05B": "吉林一號高分05B",
        "Soyuz Rideshare 17": None,
    }
    for a, b in cases.items():
        assert sg.to_zh(a) == b, a


L1 = "1 90001U 26254A   26320.50000000  .00000000  00000-0  00000-0 0  9990"


def _run(nid, sc, gsat=None, launch=None, idx_name="OBJECT A", intl="2026-254A"):
    idx = {nid: {"name": idx_name, "line1": L1.replace("26254A  ", intl[2:4] + intl[5:].ljust(6)), "line2": ""}}
    g = {"sat": gsat or {}, "launch": launch or {}, "meta": {"sat_updated": "2026 Nov 25", "launch_updated": "2026 Nov 25"}}
    tags = {}
    for n, r in sc.items():
        tags.setdefault(r["intl"][:8], []).append(n)
    return sg.suggest_one(nid, idx, sc, g, tags, {"fetched_at": "2026-11-25T00:00:00Z"})


def _sc(n, name, intl, typ="PAYLOAD"):
    return {n: {"name": name, "intl": intl, "object_type": typ, "owner": "PRC", "launch": "2026-11-11", "decay": ""}}


LA = {"2026-254": {"tag": "2026-254", "date": "2026 Nov 11 1141", "lv": "Chang Zheng 2D", "flight": "Tanluzhe", "mission": "Tanluzhe", "agency": "SAST"}}


def test_group_inferred_does_not_assign_individual():
    sc = {}
    for i, c in enumerate("ABCD"):
        sc.update(_sc(90001 + i, f"OBJECT {c}", f"2026-254{c}"))
    r = _run(90001, sc, launch=LA)
    assert r["name_status"] == "group_inferred" and r["suggest"] == "探路者 群組衛星（推論）"
    assert r["layers"]["individual"]["status"] == "unconfirmed" and r["layers"]["launch_group"]["confidence"] == "high"
    assert r["layers"]["mission_type"] == "unknown" and r["confidence"] == "low"  # 有「A/B/C 未對應」警示 → 降為 low
    assert "[group_inferred/low]" in r["note"] and "2026-254A" in r["note"]


def test_single_payload_launch_is_inferred():
    r = _run(90001, _sc(90001, "OBJECT A", "2026-254A"), launch=LA)
    assert r["name_status"] == "inferred" and r["suggest"] == "探路者（推論）" and r["confidence"] == "medium"


def test_official_and_non_payload_warning():
    r = _run(90001, _sc(90001, "CZ-6A DEB", "2026-254A", "DEBRIS"), launch=LA)
    assert r["name_status"] == "official" and r["confidence"] == "high" and r["suggest"] == "CZ-6A DEB"
    assert any("非酬載" in w for w in r["warnings"])


def test_gcat_individual_and_conflict():
    gs = {90001: {"launch_tag": "2026-254", "piece": "2026-254A", "type": "P", "name": "Yaogan 30 hao 01 zu 01 xing", "plname": ""}}
    r = _run(90001, _sc(90001, "OBJECT A", "2026-254A"), gsat=gs, launch=LA)
    assert r["name_status"] == "inferred" and r["suggest"] == "遙感三十號01組01星（推論）" and r["raw_name"] == "Yaogan 30 hao 01 zu 01 xing"
    gs2 = {90001: dict(gs[90001], name="ETSAT-003")}
    r = _run(90001, _sc(90001, "ET-001C", "2026-254A"), gsat=gs2, launch=LA)
    assert r["name_status"] == "conflict" and r["confidence"] == "medium"
    gs3 = {90001: dict(gs[90001], type="R2")}
    r = _run(90001, _sc(90001, "OBJECT A", "2026-254A"), gsat=gs3, launch=LA)
    assert any("類型不一致" in w for w in r["warnings"]) and r["confidence"] == "low"
