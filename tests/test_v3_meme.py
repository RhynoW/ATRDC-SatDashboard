# -*- coding: utf-8 -*-
"""tests/test_v3_meme.py — scenario04/physics/v3_meme.py 的內插邏輯。

不碰真正的 DuckDB：直接把 _load_samples() 換成假資料，只驗證內插/涵蓋範圍
判斷本身（DB 讀取那段已經是很單純的 SQL + reshape，風險低）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from scenario04.physics import v3_meme


def _fake_samples():
    t = np.array([0.0, 60.0, 120.0])          # 0s, 60s, 120s（unix 相對值，測試用）
    xyz = np.array([
        [7000.0, 0.0, 0.0],
        [7010.0, 10.0, 0.0],
        [7020.0, 20.0, 0.0],
    ])
    return {339974: {"t": t, "xyz": xyz}}


def test_interpolates_between_bracketing_samples(monkeypatch):
    monkeypatch.setattr(v3_meme, "_load_samples", _fake_samples)
    t_mid = datetime.fromtimestamp(30.0, tz=timezone.utc)   # 正中間
    out = v3_meme.meme_positions_eci([339974], t_mid)
    assert 339974 in out
    np.testing.assert_allclose(out[339974], [7005.0, 5.0, 0.0])


def test_out_of_coverage_is_skipped_not_extrapolated(monkeypatch):
    monkeypatch.setattr(v3_meme, "_load_samples", _fake_samples)
    too_late = datetime.fromtimestamp(999.0, tz=timezone.utc)
    out = v3_meme.meme_positions_eci([339974], too_late)
    assert 339974 not in out


def test_unknown_norad_id_skipped(monkeypatch):
    monkeypatch.setattr(v3_meme, "_load_samples", _fake_samples)
    out = v3_meme.meme_positions_eci([1], datetime.fromtimestamp(30.0, tz=timezone.utc))
    assert out == {}


def test_coverage_info_reports_span(monkeypatch):
    monkeypatch.setattr(v3_meme, "_load_samples", _fake_samples)
    info = v3_meme.coverage_info()
    assert info["available"] is True
    assert info["satellites"] == 1
