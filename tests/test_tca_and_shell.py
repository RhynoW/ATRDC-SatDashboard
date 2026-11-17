"""TCA 1 秒精算與 Starlink 作業殼層下緣自動判定。"""
from datetime import datetime, timedelta, timezone

import numpy as np

from scenario04.physics import rpo
from scenario04.physics.starlink_analysis import MIN_OPERATIONAL_PERIGEE_KM, operational_perigee_floor


def _synthetic(shift=0.0, seed=1):
    rng = np.random.default_rng(seed)
    low = rng.normal(350 + shift, 4, 700)          # 低殼層
    main = rng.normal(475 + shift, 10, 9000)       # 主力殼層
    raising = rng.uniform(200, 340 + shift, 120)   # 爬升／衰減中，稀疏
    return np.concatenate([low, main, raising])


def test_shell_floor_auto_matches_current_db_shape():
    thr, how = operational_perigee_floor(_synthetic())
    assert how == "auto"
    assert 320 <= thr <= 340


def test_shell_floor_follows_shell_shift():
    base, _ = operational_perigee_floor(_synthetic())
    up, how = operational_perigee_floor(_synthetic(shift=40))
    assert how == "auto"
    assert 30 <= up - base <= 50


def test_shell_floor_ignores_small_raising_batches():
    pg = np.concatenate([_synthetic(), np.full(30, 285.0)])   # 一批爬升中衛星
    thr, _ = operational_perigee_floor(pg)
    assert thr >= 320


def test_shell_floor_fallback_when_sparse():
    thr, how = operational_perigee_floor([480.0] * 100)
    assert (thr, how) == (MIN_OPERATIONAL_PERIGEE_KM, "fallback")


class _Lin:
    """假傳播器：位置隨時間線性變化，兩物體最接近時刻已知。"""
    def __init__(self, t0, v):
        self.t0, self.v = t0, v


def test_refine_tca_finds_subsample_minimum(monkeypatch):
    t0 = datetime(2024, 6, 14, 10, 0, tzinfo=timezone.utc)
    tca = t0 + timedelta(seconds=467)                  # 10:07:47，不在 60 s 格點上

    def fake_prop(recs, ep, t):
        dt = (t - tca).total_seconds()
        return np.array([7000.0, 0.0, 0.0]) if recs == "P" else np.array([7000.0, 0.01 * dt, 1.2])

    monkeypatch.setattr(rpo, "_prop", fake_prop)
    lo, hi = t0 - timedelta(hours=1), t0 + timedelta(hours=1)
    fine = rpo._sample("P", None, "S", None, t0, t0 + timedelta(minutes=20), 60.0)
    k = int(np.argmin([f[3] for f in fine]))
    t, d = rpo._refine_tca("P", None, "S", None, fine[k][0], 60.0, lo, hi)
    assert t == tca
    assert abs(d - 1.2) < 1e-9
    assert d < fine[k][3]
