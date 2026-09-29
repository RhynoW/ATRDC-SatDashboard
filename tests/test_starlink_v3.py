# -*- coding: utf-8 -*-
"""tests/test_starlink_v3.py — Starlink V3 偵測改版（2026-09-29）：國際編號精確比對 +
CelesTrak 補充檔暫定名單。回歸重點：SQL 過濾條件產生、暫定名單抓取/快取/排序/錯誤降級、
資料庫缺失時的優雅失敗。
"""
import pytest

import scenario04.physics.starlink_census as C


def test_v3_intl_sql_filter_matches_known_launch():
    sql = C._v3_intl_sql_filter("line1")
    assert "substr(line1, 10, 2) = '26'" in sql
    assert "substr(line1, 12, 3) = '225'" in sql


def test_v3_intl_sql_filter_empty_list_never_matches(monkeypatch):
    monkeypatch.setattr(C, "V3_INTL_LAUNCHES", [])
    assert C._v3_intl_sql_filter("line1") == "1=0"


def test_count_v3_candidates_no_db(monkeypatch):
    monkeypatch.setattr(C, "resolve_db", lambda: None)
    assert C.count_v3_candidates() == {"error": "資料庫不存在"}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _omm(name, oid, norad, epoch, mean_motion, incl=30.49):
    return {"OBJECT_NAME": name, "OBJECT_ID": oid, "NORAD_CAT_ID": norad, "EPOCH": epoch,
            "MEAN_MOTION": mean_motion, "INCLINATION": incl, "DATA_SOURCE": "SpaceX-E"}


@pytest.fixture(autouse=True)
def _clear_provisional_cache():
    C._V3_PROVISIONAL_CACHE.clear()
    yield
    C._V3_PROVISIONAL_CACHE.clear()


def test_provisional_roster_computes_altitude_and_sorts_cospar_order(monkeypatch):
    # 26 顆典型 Starlink 低軌平均運動；刻意打亂順序（含 A/B/Z/AA/AB）驗證 COSPAR 片段排序
    payload = [
        _omm("STARLINK-X", "2026-225AB", 799501673, "2026-09-29T04:49:42", 16.020),
        _omm("STARLINK-A", "2026-225A", 799501648, "2026-09-29T04:09:42", 16.021),
        _omm("STARLINK-Z", "2026-225Z", 799501671, "2026-09-29T04:48:42", 16.020),
        _omm("STARLINK-AA", "2026-225AA", 799501672, "2026-09-29T05:44:42", 16.020),
        _omm("STARLINK-B", "2026-225B", 799501649, "2026-09-29T04:29:42", 16.022),
    ]
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp(payload))
    r = C.fetch_v3_provisional_roster()
    assert r["count"] == 5 and "error" not in r
    assert [it["object_id"] for it in r["items"]] == \
        ["2026-225A", "2026-225B", "2026-225Z", "2026-225AA", "2026-225AB"]
    # 16.02 rev/day → ~268-269 km（近圓軌道，NRLMSISE 有效域內）
    for it in r["items"]:
        assert 265.0 < it["alt_km"] < 272.0
    assert r["items"][0]["provisional_norad_cat_id"] == 799501648
    assert "不會寫入本系統資料庫" in r["note"]


def test_provisional_roster_caches_within_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr("requests.get", lambda *a, **k: (calls.append(1), _FakeResp([]))[1])
    C.fetch_v3_provisional_roster()
    C.fetch_v3_provisional_roster()
    assert len(calls) == len(C.V3_INTL_LAUNCHES)   # 第二次命中快取，未再發請求


def test_provisional_roster_expired_cache_refetches(monkeypatch):
    calls = []
    monkeypatch.setattr("requests.get", lambda *a, **k: (calls.append(1), _FakeResp([]))[1])
    C.fetch_v3_provisional_roster()
    key = ",".join(f"{yr}-{num:03d}" for yr, num in C.V3_INTL_LAUNCHES)
    ts, data = C._V3_PROVISIONAL_CACHE[key]
    C._V3_PROVISIONAL_CACHE[key] = (ts - C.V3_PROVISIONAL_TTL_S - 1, data)
    C.fetch_v3_provisional_roster()
    assert len(calls) == 2 * len(C.V3_INTL_LAUNCHES)


def test_provisional_roster_network_failure_degrades_gracefully(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("timeout")
    monkeypatch.setattr("requests.get", _boom)
    r = C.fetch_v3_provisional_roster()
    assert r["count"] == 0 and r["items"] == [] and "error" in r


def test_provisional_roster_missing_mean_motion_alt_is_none(monkeypatch):
    payload = [{"OBJECT_NAME": "STARLINK-BAD", "OBJECT_ID": "2026-225A",
               "NORAD_CAT_ID": 1, "EPOCH": "2026-09-29T00:00:00", "INCLINATION": 30.0,
               "DATA_SOURCE": "SpaceX-E"}]   # 缺 MEAN_MOTION
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp(payload))
    r = C.fetch_v3_provisional_roster()
    assert r["items"][0]["alt_km"] is None
