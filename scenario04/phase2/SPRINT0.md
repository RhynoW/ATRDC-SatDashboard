# 第二階段 Sprint 0：出場條件狀態（2026-11-18）

依據：第二階段規劃 v0.3 第 7 節（Sprint 0 五項交付物）。

| # | 交付物 | 狀態 | 位置 |
|---|---|---|---|
| 1 | 可執行的 JSON Schema | 完成：結構＋語意驗證，17 種錯誤代碼；27 個共用案例 Python／Rust 代碼完全一致 | `scenario_v1.schema.json`、`schema.py`；Rust `src/phase2/schema.rs`；案例 `testdata/schema_cases.json` |
| 2 | 共用框架測試資料 | 完成：Vallado 2006 範例（ITRF、GCRF 差 < 2 cm）、round-trip（< 1e-9 km）、與 SOFA c2t06a 一致（< 1e-11）、EOP 內插、重力梯度、阻力共轉項 | `tests/test_phase2.py`；Rust `src/phase2/tests.rs`；EOP 快照 `testdata/eop_snapshot_20261118.csv` |
| 3 | 最小端到端案例 | 完成：合成 LEO 雙星 → GCRF RK4（J2–J4）→ 近接極小值 → 展示模式 Pc → 報告；Python 與 Rust 各跑一次 | `e2e.py`、`testdata/e2e_leo_synthetic*.json`；Rust `phase2_cli e2e` |
| 4 | 跨語言差異報告 | 完成：SGP4／框架／積分／演算法／Schema／端到端六層分開量測；據此訂 TLE 輸入位置容差 1 mm | `tools/phase2_xlang_report.py` → `reports/xlang_report.{md,json}` |
| 5 | 凍結的基準清單 | 完成（輸入凍結）：7 案例，G7 已通過，G1–G6 待指派負責人與工具版本 | `benchmarks/golden_cases.{json,md}` |
| 附 | 回歸案例證據包 | 完成：58573×59884、58204×43874 | `tools/build_evidence_pack.py` → `evidence/` |

## 跨語言差異（摘要）

| 層 | 最大差 |
|---|---|
| SGP4（400 TLE × 3 時刻） | 近地 1.8e-5 m、深空 2.9e-4 m |
| 框架 TEME→GCRF | 2.7e-9 m |
| RK4 傳播 1 天 | LEO 1.9e-6 m、GEO 4.7e-8 m |
| 演算法（TCA／Pc） | ΔTCA 5.7e-14 s、Pc 相對差 2.5e-10 |
| 端到端 | Δ距離 1.3e-6 m、Pc 相對差 3e-8 |

## Sprint 0 期間發現並修正

- Rust `sgp4` crate 的 `Constants::from_elements` 預設 WGS-84，與官方 SGP4（WGS-72）差 100 m～1.7 km；phase2 改用 WGS-72、opsmode 'i'，與 python-sgp4 一致。
- Chan 級數 `1 − e^{−u/2}Σ…` 在 u 很小時相減相消；改為由大到小累加 Poisson 尾項。

## 已知限制（Sprint 1 處理）

- 近接極小值搜尋以積分節點為掃描間距（LEO 10 s），尚未自適應細分。
- 協方差傳播模式（STM）尚未實作；端到端只用展示模式。
- 阻力只接單層指數大氣（測試與比對用）；NRLMSISE-00 尚未接入新力模型。
- 傳播區間不得跨越閏秒。
