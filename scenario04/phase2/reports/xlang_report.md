# 第二階段跨語言差異報告

產生時間：2026-11-18T05:22:54+00:00　EOP：{'file': 'eop_snapshot_20261118.csv', 'sha256': '346d920b48a5e16a', 'mjd_range': [60310.0, 61542.0], 'last_observed': '2026-11-17'}

每一層餵入完全相同的輸入，差異只來自該層的實作。

## L-SGP4（python-sgp4 vs Rust sgp4 crate）

TLE 400 筆 × 3 個時刻；錯誤旗標不一致 0 筆。

| 類別 | 筆數 | 位置差中位數 m | p99 m | 最大 m | 速度差最大 mm/s |
|---|---|---|---|---|---|
| 近地（週期 < 225 min） | 900 | 1.274e-07 | 8.880e-07 | 1.243e-06 | 1.465e-06 |
| 深空（週期 ≥ 225 min） | 300 | 1.335e-06 | 8.319e-05 | 2.914e-04 | 1.213e-05 |

## L-frame（erfa vs sofars，IAU-2006/2000A）

120 筆：GCRF 位置差最大 2.738e-09 m、速度差最大 2.667e-09 mm/s；ITRF 位置差最大 1.819e-09 m。

## L-integ（相同初始狀態，RK4 傳播 1 天）

| 案例 | 最大位置差 m |
|---|---|
| LEO 500 km J2–J4 | 7.347e-07 |
| LEO 400 km J2–J4＋阻力 | 1.018e-06 |
| GEO J2–J4 | 4.729e-08 |

## L-algo（相同二體解析軌跡）

| 案例 | ΔTCA s | Δ距離 m | Δ相對速度 mm/s | Pc Foster | Pc 相對差 | 篩選 Py／Rs | 極小值數相同 |
|---|---|---|---|---|---|---|---|
| LEO 交叉 | 0.00e+00 | 1.06e-08 | 8.88e-10 | 3.276e-09 | 2.53e-10 | passes_initial_screen／passes_initial_screen | True |
| LEO 迎頭 | 1.39e-17 | 0.00e+00 | 0.00e+00 | 1.592e-03 | 4.09e-16 | undetermined／undetermined | True |
| LEO 小角度追越 | 5.68e-14 | 2.98e-10 | 1.04e-11 | 3.557e-15 | 6.57e-12 | fails_initial_screen／fails_initial_screen | True |
| GEO 慢速 | 0.00e+00 | 0.00e+00 | 1.08e-13 | 2.733e-08 | 7.27e-16 | undetermined／undetermined | True |

Pc 固定輸入（Foster 相對差／Chan 相對差）：1.501e-04：0.0e+00／0.0e+00；9.938e-03：0.0e+00／0.0e+00；9.704e-02：0.0e+00／0.0e+00；1.915e-43：0.0e+00／0.0e+00

## L-schema

37 個案例，代碼不一致 0 個。

## L-adaptive（相同 TLE、SGP4 星曆，自適應搜尋 1 天，門檻 1000 km）

| 配對 | 極小值數 Py／Rs | 評估次數 Py／Rs | 最大 ΔTCA s | 最大 Δ距離 m | 標記一致 |
|---|---|---|---|---|---|
| 25544×48274 | 32／32 | 1554／1555 | 1.46e-11 | 1.59e-07 | True |
| 59103×68762 | 16／16 | 10317／10317 | 1.53e-09 | 1.97e-07 | True |
| 69673×67689 | 31／31 | 2752／2751 | 9.29e-02 | 2.22e-07 | True |

## L-covprop（相同名義軌跡，傳播模式協方差）

| τ s | 最大相對差 |
|---|---|
| 3600 | 4.70e-13 |
| 10800 | 1.79e-12 |
| 20000 | 1.36e-11 |
| 43200 | 4.14e-11 |

## L-csv／L-batch

CSV 解析一致：True。

| 次星 | 狀態一致 | 事件數 Py／Rs | 極小值數 Py／Rs | 最大 Δ距離 m |
|---|---|---|---|---|
| 48274 | True | 1／1 | 32／32 | 2.41e-08 |
| 68762 | True | 0／0 | 32／32 | 0.00e+00 |
| 59103 | True | 0／0 | 32／32 | 0.00e+00 |
| 69673 | True | 0／0 | 10／10 | 0.00e+00 |

## L-e2e：e2e_leo_synthetic

事件數 Python 3、Rust 3；極小值數一致 True。

| TCA | ΔTCA s | Δ距離 m | Pc | Pc 相對差 | 篩選一致 |
|---|---|---|---|---|---|
| 2026-11-20T05:12:41.713318Z | 1.09e-11 | 7.34e-07 | 2.878e-218 | 9.31e-09 | True |
| 2026-11-20T05:59:59.953373Z | 1.09e-11 | 4.98e-07 | 1.441e-04 | 2.56e-10 | True |
| 2026-11-20T06:47:18.311825Z | 2.18e-11 | 1.34e-06 | 1.921e-226 | 3.11e-08 | True |

## L-e2e：e2e_leo_propagated

事件數 Python 3、Rust 3；極小值數一致 True。

| TCA | ΔTCA s | Δ距離 m | Pc | Pc 相對差 | 篩選一致 |
|---|---|---|---|---|---|
| 2026-11-20T05:12:41.713318Z | 1.09e-11 | 7.34e-07 | 3.484e-58 | 8.26e-10 | True |
| 2026-11-20T05:59:59.953373Z | 1.09e-11 | 4.98e-07 | 6.177e-06 | 8.12e-11 | True |
| 2026-11-20T06:47:18.311825Z | 2.18e-11 | 1.34e-06 | 2.546e-30 | 5.52e-10 | True |

## L-e2e：e2e_tle_iss_css

事件數 Python 1、Rust 1；極小值數一致 True。

| TCA | ΔTCA s | Δ距離 m | Pc | Pc 相對差 | 篩選一致 |
|---|---|---|---|---|---|
| 2026-11-18T06:33:05.759227Z | 0.00e+00 | 2.41e-08 | 0.000e+00 | 0.00e+00 | True |

## L-maneuver（相同避碰場景）

| 案例 | 狀態 Py／Rs | 原因一致 | 候選相同 | 檢查一致 | 線性圖一致 | 候選數 | 最大 Δ機動時刻 s | 最大 Δ距離 m | 最大 ΔTCA s | 最大 Δ線性距離 m | Pc 相對差 | 最大 Δa 差 m |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| base_feasible | feasible_solution_found／feasible_solution_found | True | True | True | True | 6 | 1.1e-11 | 1.97e-06 | 5.46e-11 | 1.17e-06 | 4.15e-10 | 2.73e-09 |
| notice_window_conflict | infeasible_constraints／infeasible_constraints | True | True | True | True | 0 | 0.0e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 |
| window_before_data_start | infeasible_constraints／infeasible_constraints | True | True | True | True | 0 | 0.0e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 |
| dv_budget_below_min | infeasible_constraints／infeasible_constraints | True | True | True | True | 0 | 0.0e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 |
| no_along_track_thrust | infeasible_constraints／infeasible_constraints | True | True | True | True | 0 | 0.0e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 |
| no_solution_small_dv | no_solution_found／no_solution_found | True | True | True | True | 3 | 1.1e-11 | 4.30e-07 | 1.82e-11 | 4.95e-07 | 5.66e-11 | 9.09e-10 |
| no_maneuver_required | no_maneuver_required／no_maneuver_required | True | True | True | True | 0 | 0.0e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 |
| target_not_found | target_not_found／target_not_found | True | True | True | True | 0 | 0.0e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 | 0.00e+00 |
| demo_covariance_miss_only | feasible_solution_found／feasible_solution_found | True | True | True | True | 6 | 1.1e-11 | 1.52e-06 | 8.37e-11 | 1.19e-06 | 2.35e-09 | 1.82e-09 |
| new_conjunction_rejects_best | feasible_solution_found／feasible_solution_found | True | True | True | True | 6 | 1.1e-11 | 1.97e-06 | 5.46e-11 | 1.17e-06 | 4.15e-10 | 2.73e-09 |

## 結論與容差建議

- SGP4 層：近地物體最大差 1.24e-06 m、深空物體最大差 2.91e-04 m；建議第 10 節「TLE 輸入（經 SGP4）的位置」容差 ε_abs = 1 mm（實測最大值約 3 倍餘裕）。兩邊皆採 WGS-72、opsmode 'i'；Rust sgp4 crate 的 Constants::from_elements 預設為 WGS-84，不可使用。
- 框架層：GCRF 最大差 2.7e-09 m，遠低於 1 mm 容差。
- 積分層：最大差 LEO 500 km J2–J4 7.3e-07 m、LEO 400 km J2–J4＋阻力 1.0e-06 m、GEO J2–J4 4.7e-08 m。
- 演算法層：ΔTCA 最大 5.7e-14 s，Pc 相對差最大 2.5e-10；篩選狀態全部一致。
- Schema 層：37 案例代碼不一致 0 個。
- 自適應搜尋層：25544×48274 極小值 32／32、ΔTCA ≤ 1.5e-11 s；59103×68762 極小值 16／16、ΔTCA ≤ 1.5e-09 s；69673×67689 極小值 31／31、ΔTCA ≤ 9.3e-02 s。
- 協方差傳播層：最大相對差 4.1e-11。
- CSV 解析一致：True；批次配對狀態全部一致：True。
- 避碰層：10 案例狀態全部一致 True；候選方案與檢查結果全部相同 True；機動後距離最大差 2.0e-06 m、Pc 相對差最大 2.3e-09。
