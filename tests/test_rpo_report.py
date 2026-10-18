"""RPO 靜態影像報告：觀測／預測切分、研判、跳變旗標與 PNG 產出。"""
from datetime import datetime, timedelta, timezone

import duckdb
import numpy as np
import pandas as pd
import pytest

from scenario04.physics import rpo_report as rr

_L1 = "1 25544U 98067A   24001.50000000  .00016717  00000-0  10270-3 0  9993"
_L2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49514064 10000"


def _make_db(tmp_path):
    """兩顆同軌面衛星（次星相位落後 0.02° ≈ 2.4 km），同 epoch 之 elset 各 6 筆。"""
    path = tmp_path / "t.duckdb"
    con = duckdb.connect(str(path))
    con.execute("""create table raw_tle_archive(norad_id integer, epoch_utc timestamp,
        eccentricity double, inclination_deg double, raan_deg double, argp_deg double,
        mean_anomaly_deg double, mean_motion double, bstar double)""")
    t0 = datetime(2026, 10, 1, 0, 0)
    for k in range(6):
        for nid, ma in ((1, 0.0), (2, 359.98)):
            con.execute("insert into raw_tle_archive values (?, ?, 0.0005, 97.85, 70.0, 0, ?, 15.078, 0)",
                        [nid, t0 + timedelta(days=k), ma])
    con.close()
    return path


def test_compute_report_splits_observed_and_forecast(tmp_path):
    d = rr.compute_report_data(1, 2, db=_make_db(tmp_path), catalog={},
                               lookback_days=10, forecast_days=3, step_s=60)
    # 最後共同 elset = 兩者最後 epoch 之較早者（10-06 00:00）
    assert d["obs_end"] == datetime(2026, 10, 6, tzinfo=timezone.utc)
    assert d["is_obs"].any() and (~d["is_obs"]).any()
    assert d["times"][~d["is_obs"]].min() > d["times"][d["is_obs"]].max()
    # 相位差 0.02° ≈ 2.4 km、同軌道 → 預測期全程維持接近 → 研判伴飛
    assert d["tca_obs"]["d_km"] < rr.HOLD_KM
    assert d["verdict"] == "hold"


def _jumps(k_mid, k_back):
    t = datetime(2026, 10, 10, tzinfo=timezone.utc)
    return [{"t": t, "dalt_km": -47.8, "suspect": False, "k": 5},
            {"t": t + timedelta(days=3), "dalt_km": -2.3, "suspect": False, "k": k_mid},
            {"t": t + timedelta(days=4), "dalt_km": 2.29, "suspect": False, "k": k_back}]


def test_flag_reversals_single_elset_outlier_is_suspect():
    js = _jumps(12, 13)              # 中間高度只有 1 筆 elset → 離群
    rr._flag_reversals(js)
    assert [j["suspect"] for j in js] == [False, True, True]


def test_flag_reversals_supported_level_is_real_maneuver():
    js = _jumps(12, 16)              # 中間高度有 4 筆 elset 支持 → 真實相位調整（COSMOS 2612 案）
    rr._flag_reversals(js)
    assert not any(j["suspect"] for j in js)


def test_nmc_metrics_detects_2to1_ellipse():
    """CW 解析 NMC：R = A cos(nt)、I = −2A sin(nt) + 中心偏置 −2 km → 比值 2、非漂移。"""
    period = 95.0
    times = pd.date_range("2026-10-17", periods=int(5 * period * 2), freq="30s")
    tm = (times - times[0]).total_seconds().to_numpy() / 60.0
    ph = 2 * np.pi * tm / period
    ric = np.stack([0.4 * np.cos(ph), -2.0 - 0.8 * np.sin(ph), 0.5 * np.sin(ph)], axis=1)
    m = rr._nmc_metrics(times, ric, times[0], period)
    assert m["is_nmc"] and not m["drifting"]
    assert abs(m["ratio"] - 2.0) < 0.05
    assert abs(m["i_center"] + 2.0) < 0.05 and abs(m["drift_rev_km"]) < 0.01


def test_nmc_metrics_drift_does_not_inflate_amplitude():
    """中心每圈漂移 1 km：沿軌振幅須扣除圈內漂移，比值仍約 2，並判為 drifting。"""
    period = 95.0
    times = pd.date_range("2026-10-17", periods=int(5 * period * 2), freq="30s")
    tm = (times - times[0]).total_seconds().to_numpy() / 60.0
    ph = 2 * np.pi * tm / period
    ric = np.stack([0.4 * np.cos(ph), -0.8 * np.sin(ph) + tm / period, 0 * ph], axis=1)
    m = rr._nmc_metrics(times, ric, times[0], period)
    assert abs(m["ratio"] - 2.0) < 0.1 and m["drifting"]
    assert abs(m["drift_rev_km"] - 1.0) < 0.05


def test_pure_intrack_offset_is_not_nmc(tmp_path):
    d = rr.compute_report_data(1, 2, db=_make_db(tmp_path), catalog={},
                               lookback_days=10, forecast_days=3, step_s=60)
    assert d["nmc"] is None or not d["nmc"]["is_nmc"]   # 同軌、純相位差 → 無徑向振盪


@pytest.mark.parametrize("name,prefix", [("ICEYE-X36", "ICEYE"), ("STARLINK-1234", "STARLINK"),
                                         ("COSMOS 2612", "COSMOS"), (None, None), ("1234", None)])
def test_constellation_prefix(name, prefix):
    assert rr._constellation_prefix(name) == prefix


def test_render_png_with_catalog(tmp_path):
    catalog = {1: {"name": "TEST-A", "line1": _L1, "line2": _L2},
               2: {"name": "OTHER 2", "line1": _L1, "line2": _L2}}
    png = rr.build_rpo_report(1, 2, db=_make_db(tmp_path), catalog=catalog, lang="en",
                              lookback_days=10, forecast_days=2, step_s=120)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 20_000
