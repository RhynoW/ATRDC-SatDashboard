"""碰撞機率（Chan 2008 近似）單元測試（Phase 1.4）。"""
import pytest

from scenario04.physics.conjunction import compute_pc_chan


def test_pc_bounded():
    for miss in (0.0, 0.1, 1.0, 10.0, 100.0):
        pc = compute_pc_chan(miss)
        assert 0.0 <= pc <= 1.0


def test_pc_monotonic_decreasing_in_miss():
    pcs = [compute_pc_chan(m) for m in (0.0, 0.05, 0.1, 0.5, 1.0, 5.0)]
    assert all(a >= b for a, b in zip(pcs, pcs[1:]))


def test_pc_zero_miss_is_maximum():
    assert compute_pc_chan(0.0) >= compute_pc_chan(0.01)


def test_pc_large_miss_negligible():
    assert compute_pc_chan(100.0) == pytest.approx(0.0, abs=1e-12)


def test_pc_zero_sigma():
    assert compute_pc_chan(1.0, sigma_r=0.0, sigma_t=0.0) == 0.0


def test_pc_known_value():
    """golden value：miss=0、sigma_r=0.1、sigma_t=0.5、r=0.005 km。

    Pc = (1 - exp(-r²/(2σ²)))，σ² = 0.26 → ≈ 4.8077e-5
    """
    pc = compute_pc_chan(0.0, sigma_r=0.1, sigma_t=0.5, r_sat=0.005)
    assert pc == pytest.approx(4.8077e-5, rel=1e-3)
