# 第二階段 Sprint 2：狀態（2026-11-18）

依據：第二階段規劃 v0.3 第 7 節 Sprint 2（避碰決策）、第 9 節回傳格式、第 10 節 L6 驗收。Python 與 Rust 雙版本以同一組測試資料驗證。

## 交付物

| # | 項目 | 狀態 | 位置（Python／Rust） |
|---|---|---|---|
| 1 | 沿跡向單脈衝避碰搜尋 | 完成：目標事件選擇 → 可行性預檢 → 參考軌跡 → 線性 STM 預篩 → 非線性驗證 → 排序 | `maneuver.py`／`src/phase2/maneuver.rs` |
| 2 | 三態結果＋兩種預檢結果 | 完成：`feasible_solution_found`、`no_solution_found`、`infeasible_constraints`（原因：notice_window_conflict、window_before_data_start、no_along_track_thrust、dv_budget_below_min_dv）；另有 `no_maneuver_required`、`target_not_found` | 同上 |
| 3 | 機動前後一致比較 | 完成：在機動窗起點取主星狀態（TLE 經 SGP4→GCRF）建立不機動的數值參考軌跡，所有候選自同一軌跡的節點分岔 | 同上 |
| 4 | 重新傳播、重新搜尋、重算風險 | 完成：每個驗證候選重新積分、自適應搜尋目標事件、協方差沿候選軌跡傳播（含機動執行誤差）、適用性篩選後計算 Foster／Chan Pc | 同上 |
| 5 | 二次篩選 | 完成：對原次星與指定物體清單（TLE 或狀態向量，≤ 200 顆）先做近遠地點預篩再逐對搜尋；機動後距離小於拒收距離且原本沒有、或原本較遠的交會（new／worsened）判為不通過；報告註明實際涵蓋範圍 | 同上 |
| 6 | 方案輸出 | 完成：Pc 變化（僅雙方傳播模式協方差時比較）、距離改善、推進劑、半長軸與週期變化、新增交會、線性預測對照 | 同上 |
| 7 | Schema | 完成：`maneuver_search` 新增 `n_times`、`n_dv`、`max_verify`、`secondary_screening.objects`；`maneuver_execution_error` 有型別；共用案例 37 個 | `scenario_v1.schema.json`、`schema.py`／`schema.rs` |
| 8 | Lab「避碰決策」分頁 | 完成：參數表單、10 個示範案例、狀態橫幅、方案比較表、所需最小 Δv 圖、距離歷程、機動前後 B-plane、3D（TCA 附近 RTN／全域軌道，可拖曳旋轉）；繁中／英／日 | `api/lab.py`、`web/static/js/lab_maneuver.js`／`src/lab.rs` |
| 9 | L6 驗收案例與跨語言比對 | 完成：`maneuver_cases.json` 10 案；跨語言報告新增 L-maneuver 層 | `testdata/`、`tools/phase2_xlang_report.py` |

## 驗證結果

- Python：全專案 235 項測試通過（新增 `tests/test_phase2_sprint2.py` 17 項）。
- Rust：`cargo test --release` 主程式 49 項、phase2_cli 27 項全過；建置無警告。
- 瀏覽器（Playwright）：Python 與 Rust 的避碰分頁 10 個案例橫幅皆符合預期，頁面錯誤 0、失敗請求 0。

### L6 驗收案例

| 案例 | 結果 | 說明 |
|---|---|---|
| base_feasible | feasible_solution_found | TCA 前 41.7 min、−T 0.345 m/s：距離 0.232 → 2.326 km，Pc 6.18e-6 → 8.37e-8（目標 1e-7），推進劑 41.6 g |
| notice_window_conflict | infeasible_constraints | 分析時刻＋通知時間晚於機動窗終點 |
| window_before_data_start | infeasible_constraints | 機動窗早於分析起點 |
| dv_budget_below_min | infeasible_constraints | 預算 0.005 m/s < 最小 Δv 0.01 m/s |
| no_along_track_thrust | infeasible_constraints | 推力方向只有 R、N |
| no_solution_small_dv | no_solution_found | Δv ≤ 0.01 m/s：網格內無解，回報最大 Δv 下的最佳候選 |
| no_maneuver_required | no_maneuver_required | 放寬目標後參考事件已達標 |
| target_not_found | target_not_found | 指定 TCA 附近沒有事件 |
| demo_covariance_miss_only | feasible_solution_found | 展示模式協方差：Pc 目標不採用，只以距離判定 |
| new_conjunction_rejects_best | feasible_solution_found | 加入一顆會被最省方案撞近的合成物體：最省方案因新增交會（0.32 km）被拒收，改選 TCA 前 135.7 min 的方案 |

### 線性預篩與非線性驗證

基準案例 6 個驗證候選的線性預測距離與非線性結果相差 ≤ 0.04%（約 1 m）；STM 對有限差分相對差 < 1e-4。

### 跨語言差異（L-maneuver）

10 個案例的狀態、原因、候選清單（方向、Δv、排序）、各項檢查、線性預篩圖全部一致。機動時刻差 1.1e-11 s（源自基準篩選 TCA 的既有差異）、機動後距離最大差 2.0e-6 m、Pc 相對差最大 2.3e-9。

### 效能

| 工作負載 | Python（本機） | Rust（本機） |
|---|---|---|
| 基準案例（24 時刻 × 20 量級 × 2 方向，驗證 6 個，含二次篩選） | 約 3 s | 約 0.02 s |
| W4 類：300 時刻、驗證 100 個（96 個另做二次篩選） | 22.5 s | — |

W4 初始目標為線上版 ≤ 60 s（不含二次篩選）；HF cpu-basic 的實測待部署後補上。

## Sprint 2 期間發現並修正

- 新增交會的判定原本只看「參考軌跡沒有的事件」，漏掉「原本就有但距離變近」的情況；改為 new／worsened 分類。
- 參考軌跡模型欄原為 Python dict 字串，改為結構化 JSON（Rust 才能逐鍵相同）。
- Rust `serde_json` 預設解析浮點數不保證正確捨入，開啟 `float_roundtrip`。
- 二次篩選狀態向量物體的曆元納入地球定向表範圍；`window_days` 早於機動時刻時不搜尋；API 對 `secondary_screening` 型別錯誤回 400。

## 已知限制

- 只支援沿跡向單脈衝；徑向、越軌向與多脈衝未實作。
- 線性預篩的 STM 不含阻力偏導數，Pc 用小 HBR 近似；判定一律以非線性驗證為準。
- 二次篩選只涵蓋使用者提供的物體清單（Lab 由伺服器資料庫以 NORAD 編號取 TLE），不是完整目錄；報告中明示涵蓋範圍。
- 二次篩選物體沒有協方差，新增交會只以距離判定（預設 1 km，或 `min_miss_km`）。
- 機動後軌道以數值星曆表示，不擬合新 TLE。
- 展示模式協方差不作機動前後 Pc 比較（規劃第 4 節）。
- EOP 快照到 2027-05-17。
