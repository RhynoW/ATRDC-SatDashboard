"""凍結的物理常數與數值設定（與 Rust src/phase2/constants.rs 逐值相同；改值須同步並更新基準清單）。"""
from __future__ import annotations

CONSTANTS_VERSION = "phase2-constants-v1"

# 地球重力場（EGM-96 未正規化帶諧係數；Vallado 4th ed. Table D-1 等常用值）
GM_KM3_S2 = 398600.4418
RE_KM = 6378.137
J2 = 1.08262668355e-3
J3 = -2.53265648533e-6
J4 = -1.61962159137e-6

# 地球自轉（IERS 標稱值；實際轉速 = OMEGA_E × (1 − LOD/86400)）
OMEGA_E_RAD_S = 7.292115146706979e-5

# WGS-84 橢球（大地座標換算用 SOFA gc2gd 參數 n=1）
WGS84_N = 1

ARCSEC_TO_RAD = 4.848136811095359935899141e-6
DAY_S = 86400.0
MJD0 = 2400000.5
TT_MINUS_TAI_S = 32.184

# 數值設定
RK4_STEP_LEO_S = 10.0
RK4_STEP_GEO_S = 60.0
PN_NODE_STEP_S = 3600.0          # 歲差章動量（X、Y、s）與 EOP 的內插節點間距
BRENT_XTOL_S = 1.0e-6            # TCA 求根時間容差
GL_RADIAL_N = 32                 # Foster 2D：徑向 Gauss–Legendre 節點數
GL_ANGULAR_N = 128               # Foster 2D：角向梯形節點數（週期函數，譜精度）
CHAN_MAX_TERMS = 200

# 自適應近接搜尋（Sprint 1）
SEARCH_COARSE_STEP_S = 60.0
SEARCH_MIN_STEP_S = 1.0
SEARCH_SUBDIVIDE = 6
SEARCH_ACCEL_BOUND_KM_S2 = 0.02      # 相對加速度上界（≈ 2 μ/Re²）
SEARCH_LOW_VREL_KM_S = 0.01          # 低相對速度（10 m/s）
SEARCH_TIDAL_COEF_S2 = 4.7e-6        # 潮汐項係數 3μ/Re³（1/s²），乘距離得相對加速度上界
SEARCH_PERT_ACCEL_KM_S2 = 3.0e-5     # 兩星攝動加速度差上界（J2 量級 ×2）
SEARCH_OMEGA_MAX_RAD_S = 1.3e-3      # 最大軌道角速率（LEO 下限高度），用於相對 jerk 上界

# 協方差傳播（Sprint 1）
COV_STEP_LEO_S = 20.0                 # RK4 誤差 ∝ h⁴：60 s 相對誤差 6e-4、20 s 為 8e-6（3 小時 LEO）
COV_STEP_GEO_S = 300.0
COV_JAC_DR_KM = 1.0e-3               # 帶諧加速度 Jacobian 中央差分步長

# 二維 Pc 適用性啟發式篩選初始判定值（v0.3 第 5 節；待 Sprint 4 以 Monte Carlo 校正）
SCREEN_K_SIGMA = 5.0
SCREEN_TENC_MAX_FRAC_PERIOD = 0.01
SCREEN_LIN_MAX_FRAC = 0.1
SCREEN_COV_DET_MAX_REL = 0.10
SCREEN_COV_AXIS_MAX_REL = 0.10
SCREEN_COV_ROT_MAX_DEG = 5.0


def as_dict() -> dict:
    return {k: v for k, v in globals().items() if k.isupper()}
