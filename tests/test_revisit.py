"""重訪 / 覆蓋分析（physics.revisit 與 /api/story/revisit）測試。

分兩層：
  1. 純幾何單元測試——不依賴 DB，用一顆合成 TLE 驗證仰角門檻的單調性與空窗定義。
  2. API 冒煙測試——依賴打包 DB，驗證 OneWeb 對台灣站的輸出結構與物理合理性。
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


# ── 1. 純幾何：不需 DB ──────────────────────────────────────────────────────

# ISS 級近地軌道之合成 TLE（僅供幾何測試，非即時軌道）
_L1 = "1 25544U 98067A   24001.50000000  .00016717  00000-0  10270-3 0  9993"
_L2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49514064 10000"


@pytest.fixture(scope="module")
def synth_idx():
    return {99999: {"name": "TEST-SAT", "line1": _L1, "line2": _L2}}


def test_runs_helper():
    import numpy as np

    from scenario04.physics.revisit import _runs
    assert _runs(np.array([False, False])) == []
    assert _runs(np.array([True, True, False, True])) == [(0, 1), (3, 3)]
    assert _runs(np.array([True])) == [(0, 0)]


def test_mask_monotonicity(synth_idx):
    """仰角門檻升高時，過頂次數與覆蓋率只能下降（單調性）——這是最基本的物理健全性。"""
    from scenario04.physics.revisit import compute_revisit
    r = compute_revisit([99999], synth_idx, 25.03, 121.57, hours=24, step_sec=60,
                        masks=(0, 10, 20, 40))
    assert not r.get("error"), r.get("error")
    cov = [r["by_mask"][str(m)]["coverage_pct"] for m in (0, 10, 20, 40)]
    npass = [r["by_mask"][str(m)]["n_passes"] for m in (0, 10, 20, 40)]
    assert cov == sorted(cov, reverse=True), cov
    assert npass == sorted(npass, reverse=True), npass
    assert r["n_sats_propagated"] == 1


def test_coverage_and_gaps_consistent(synth_idx):
    """單顆衛星時：覆蓋率必 <100%，且內部空窗數與過頂次數相差不超過 1。"""
    from scenario04.physics.revisit import compute_revisit
    r = compute_revisit([99999], synth_idx, 25.03, 121.57, hours=24, step_sec=60, masks=(10,))
    b = r["by_mask"]["10"]
    assert 0 < b["coverage_pct"] < 100
    assert abs(b["n_gaps"] - b["n_passes"]) <= 1
    assert b["max_gap_min"] > 0
    assert len(b["timeline"]) == r["window"]["timeline_bins"]
    assert b["sat_revisit_median_min"] is not None


def test_no_tle_fails_closed():
    """無 TLE 時回錯誤而非拋例外或回空殼結果（fail-closed）。"""
    from scenario04.physics.revisit import compute_revisit
    r = compute_revisit([1], {1: {"name": "NO-TLE"}}, 25.0, 121.0, hours=6, step_sec=60)
    assert r.get("error")


# ── 2. API：需打包 DB ───────────────────────────────────────────────────────

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


def test_api_revisit_oneweb_taipei(client):
    d = client.get("/api/story/revisit?group=oneweb&site=taipei").get_json()
    assert "error" not in d, d.get("error")
    assert d["group"] == "oneweb" and d["site_name"] == "台北"
    assert d["n_sats_propagated"] > 100          # OneWeb 應有數百顆
    assert {s["key"] for s in d["sites"]} >= {"taipei", "eluanbi"}
    b = d["by_mask"][str(int(d["masks"][0]))]
    # 低仰角門檻下，654 顆極軌星系對台灣應為連續覆蓋
    assert b["coverage_pct"] > 99.0
    assert b["max_simultaneous"] >= 5
    # 三種重訪定義都要有值，且單星重訪必然遠大於星系級平均過頂間隔
    assert b["sat_revisit_median_min"] > b["mean_revisit_min"]


def test_api_revisit_bad_group(client):
    resp = client.get("/api/story/revisit?group=not-a-group")
    assert resp.status_code == 400


def test_story_section_registered(client):
    """故事 JSON 內的 revisit 區塊要通過 schema，且 story.js 有對應渲染器。"""
    d = client.get("/api/story/sea-land-space-sky").get_json()
    rv = [s for s in d["sections"] if s.get("type") == "revisit"]
    assert len(rv) == 1 and rv[0]["group"] == "oneweb"
    js = client.get("/static/js/story.js").get_data(as_text=True)
    assert "initRevisit" in js and "revisit: initRevisit" in js
