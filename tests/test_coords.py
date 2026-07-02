"""座標轉換單元測試（Phase 1.4）：純函式，無需 Flask / DB。"""
import numpy as np
import pytest
from datetime import datetime, timezone

from sgp4.api import jday

from scenario04.physics.coords import (
    R_EARTH_KM,
    eci_to_elaz,
    eci_to_llh_batch,
    gmst_iau2006,
    gmst_rad,
    observer_ecef,
)


def test_observer_ecef_equator():
    """赤道 (0,0,0)：x = R_EARTH，y = z = 0。"""
    obs = observer_ecef(0.0, 0.0, 0.0)
    assert obs["x0"] == pytest.approx(R_EARTH_KM, abs=1e-6)
    assert obs["y0"] == pytest.approx(0.0, abs=1e-9)
    assert obs["z0"] == pytest.approx(0.0, abs=1e-9)


def test_observer_ecef_pole():
    """北極：z = 半短軸 b ≈ 6356.752 km。"""
    obs = observer_ecef(90.0, 0.0, 0.0)
    assert obs["x0"] == pytest.approx(0.0, abs=1e-6)
    assert obs["z0"] == pytest.approx(6356.7523142, abs=1e-3)


def test_eci_to_llh_equatorial_point():
    """赤道面上、模長 = 地球半徑 + 500 km 的 ECI 向量 → lat≈0、alt≈500。"""
    t = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    r = np.array([[R_EARTH_KM + 500.0, 0.0, 0.0]])
    llh = eci_to_llh_batch(r, t)
    assert llh[0, 0] == pytest.approx(0.0, abs=1e-6)      # lat
    assert -180.0 <= llh[0, 1] <= 180.0                    # lon
    assert llh[0, 2] == pytest.approx(500.0, abs=0.5)      # alt


def test_eci_to_elaz_overhead():
    """位於觀測者正上方 500 km 的衛星：仰角接近 90 度。"""
    lat, lon = 25.0330, 121.5654
    obs = observer_ecef(lat, lon, 0.0)
    t = datetime(2026, 7, 1, 4, 0, 0, tzinfo=timezone.utc)
    jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second)
    gmst = gmst_rad(jd, fr)

    # 沿 ECEF 徑向外推 500 km，再旋轉回 ECI
    p = np.array([obs["x0"], obs["y0"], obs["z0"]])
    p_up = p * (1.0 + 500.0 / np.linalg.norm(p))
    cg, sg = np.cos(gmst), np.sin(gmst)
    r_eci = np.array([[cg * p_up[0] - sg * p_up[1],
                       sg * p_up[0] + cg * p_up[1],
                       p_up[2]]])

    el, az, rng = eci_to_elaz(r_eci, jd, fr, obs)
    # 大地緯度 vs 地心緯度差（~0.19° @25N）使徑向非嚴格天頂
    assert el[0] > 88.0
    assert rng[0] == pytest.approx(500.0, rel=0.01)


def test_eci_to_elaz_below_horizon():
    """位於地球另一側的衛星：仰角必為負。"""
    lat, lon = 25.0330, 121.5654
    obs = observer_ecef(lat, lon, 0.0)
    t = datetime(2026, 7, 1, 4, 0, 0, tzinfo=timezone.utc)
    jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second)
    gmst = gmst_rad(jd, fr)

    p = np.array([obs["x0"], obs["y0"], obs["z0"]])
    p_far = -p * (1.0 + 500.0 / np.linalg.norm(p))   # 對蹠方向
    cg, sg = np.cos(gmst), np.sin(gmst)
    r_eci = np.array([[cg * p_far[0] - sg * p_far[1],
                       sg * p_far[0] + cg * p_far[1],
                       p_far[2]]])

    el, _, _ = eci_to_elaz(r_eci, jd, fr, obs)
    assert el[0] < 0.0


def test_gmst_variants_agree():
    """IAU2006 與簡化版 GMST 在同一時刻差異應在 0.01 度以內。"""
    jd, fr = jday(2026, 7, 1, 12, 0, 0)
    g1 = gmst_iau2006(jd, fr)
    g2 = float(gmst_rad(jd, fr))
    diff_deg = abs(np.rad2deg(g1 - g2))
    assert min(diff_deg, 360.0 - diff_deg) < 0.01
