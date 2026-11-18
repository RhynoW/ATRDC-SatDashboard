# 第二階段 Sprint 1：狀態（2026-11-18）

依據：第二階段規劃 v0.3（自適應近接搜尋、協方差傳播、批次篩選、Lab 頁面）。Python 與 Rust 雙版本以同一組測試資料驗證。

## 交付物

| # | 項目 | 狀態 | 位置（Python／Rust） |
|---|---|---|---|
| 1 | 星曆層 | 完成：數值星曆、SGP4 星曆（WGS-72、opsmode 'i'，TEME→GCRF）、相對狀態與評估次數計數 | `ephemeris.py`／`src/phase2/ephemeris.rs` |
| 2 | 自適應近接搜尋 | 完成：粗間距 60 s，距離下界低於門檻且曲率檢驗顯示可能有隱藏根時細分 6 段，最小 1 s；相對速度 < 0.01 km/s 標記 `low_relative_velocity` 並加跑黃金分割 | `tca.py`／`tca.rs` |
| 3 | 協方差傳播 | 完成：dP/dt = AP + PAᵀ + Qc，重力梯度＝二體解析＋J2–J4 中央差分；RK4 步長 LEO 20 s、GEO 300 s；延遲延伸、梯度快取 | `covprop.py`／`covprop.rs` |
| 4 | 事件組裝 | 完成：TCA、miss、Pc（Foster／Chan）、三態適用性篩選、協方差來源描述 | `events.py`／`events.rs` |
| 5 | 次星 CSV 與批次 | 完成：CSV 解析 8 種錯誤代碼、上限 500 列（API 上限 200 顆）；批次依 Pc 遞減排序、結果 CSV（含 BOM） | `csvio.py`、`batch.py`／`csvio.rs`、`batch.rs` |
| 6 | 端到端 Sprint 1 流程 | 完成：propagator auto／sgp4／numerical、協方差 demo／propagated；Schema 新增 3 欄位、3 個語意錯誤代碼，共用案例 32 個 | `e2e.py`、`schema.py`／`e2e.rs`、`schema.rs` |
| 7 | Lab 頁面與 API | 完成：`/lab`、`/api/lab/{demo,scenario,batch,batch.csv,csv/parse}`；批次與場景兩分頁、KPI、可排序表格、距離歷程圖、B-plane 圖、CSV／JSON 下載；繁中／英／日介面 | `api/lab.py`、`web/templates/lab.html`、`web/static/js/lab.js`／`src/lab.rs` |
| 8 | 跨語言差異報告擴充 | 完成：新增端到端（3 場景）、自適應搜尋、協方差傳播、CSV／批次四層 | `tools/phase2_xlang_report.py` → `reports/xlang_report.{md,json}` |

## 驗證結果

- Python：全專案 218 項測試通過（新增 `tests/test_phase2_sprint1.py` 10 項）。
- Rust：`cargo test --release` 主程式 42 項、phase2_cli 20 項全過；建置無警告。
- 瀏覽器（Playwright）：Python 與 Rust 的 `/lab` 均無頁面錯誤、無失敗請求；Rust 批次 CSV 回應與 Python 逐位元組相同。

### 代表結果

| 案例 | 結果 |
|---|---|
| ISS × 天宮（CSS），TLE，7 天 | 最近 45.912 km，2026-11-18T06:33:05.759Z |
| 合成 LEO 雙星，展示協方差 | miss 0.2316 km，Pc 1.441e-4 |
| 合成 LEO 雙星，傳播協方差 | Pc 6.177e-6 |

### 跨語言差異（Sprint 1 新增層）

| 層 | 最大差 |
|---|---|
| 端到端（3 場景） | ΔTCA 2.2e-11 s、Δ距離 1.3e-6 m、Pc 相對差 3.1e-8（出現在 1e-226 級尾值；主事件 2.6e-10） |
| 自適應搜尋（3 對 TLE） | 極小值數全部一致；ΔTCA ≤ 1.5e-9 s（低相對速度的對接配對 69673×67689 為 0.093 s，距離差僅 1e-10 km，極小值平坦） |
| 協方差傳播 | 相對差 4.1e-11 |
| CSV／批次 | 列、錯誤行號與代碼、配對狀態全部一致 |

`n_evals`（評估次數）在 TLE 案例偶有 ±1–3 差異：Brent 最後一步的方向受 1e-11 s 級浮點雜訊影響，TCA 與距離不受影響，不列入比對。

### 效能

| 案例 | Python | Rust |
|---|---|---|
| ISS 7 天自適應搜尋 | 1.05 s | — |
| 共軌 59103 配對 | 9 s | — |
| 傳播協方差 7 天 | 7.4 s | — |
| 三個 e2e 場景（各） | 0.2–1.2 s | 約 0.02 s |

## Sprint 1 期間發現並修正

- Chan 級數在 v 很大時溢位：改為 w_m 遞迴，v/2 > 700 時直接回 0（Python、Rust 同步）。
- 自適應搜尋初版過慢（66 s）：改用隨距離變化的加速度上界與曲率隱藏根檢驗，ISS 7 天降到 1.05 s。
- 協方差與有限差分 STM 差 6e-4：RK4 誤差隨 h⁴，LEO 步長 60 s → 20 s 後降到 8e-6。
- 傳播協方差 7 天 24 s：地球定向表加快取、梯度快取、延遲傳播後 7.4 s。
- Rust SGP4 時間改用 python-sgp4 的算法（jdsatepoch＋日內分數），位置差由 7e-9 km 降到 1e-11 km。
- Rust `iso()` 在微秒為 0 時不輸出小數，與 Python `isoformat()` 一致。

## 已知限制

- 協方差傳播的 A 矩陣不含阻力偏導數。
- EOP 快照到 2027-05-17 為止；超出範圍需更新快照或讀伺服器的 `EOP-All.csv`。
- Python 傳播協方差 7 天仍需約 7 s；Rust 未移植 `frames.py` 的快取（不影響數值）。
- 數值星曆超出範圍時，Python 拋 ValueError，Rust CLI 直接 panic（Lab API 轉成 400）。
- Rust 主程式 SGP4 用 AFSPC 模式（opsmode 'a'），phase2 用 'i'；兩者並存。
- Lab 的主星名稱在 Rust 版套用使用者目錄的中文名稱（例「國際太空站」），Python 版為 TLE 名稱。
- 阻力仍為單層指數大氣；NRLMSISE-00 尚未接入新力模型。
