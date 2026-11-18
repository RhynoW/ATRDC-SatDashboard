# 第二階段凍結基準清單（golden-v1，2026-11-18）

共用設定：常數 phase2-constants-v1（GM=398600.4418、Re=6378.137、J2=0.00108262668355、J3=-2.53265648533e-06、J4=-1.61962159137e-06）；框架 GCRF（IAU-2006/2000A CIO，含 dX/dY）；EOP 快照 eop_snapshot_20261118.csv（sha256 346d920b48a5e16a）；內部積分 RK4 固定步長（LEO 10 s、GEO 60 s）。

| ID | 案例 | 驗收層 | 力模型 | 參考工具 | 門檻 | 負責人 | 狀態 |
|---|---|---|---|---|---|---|---|
| G1 | LEO 二體 7 天 | L2 | two_body | 解析二體（內部）；Orekit KeplerianPropagator；STK TwoBody | 位置差 ≤ 1 m | 待指派 | 輸入凍結，待執行 |
| G2 | LEO J2–J4 7 天 | L2 | two_body、zonal: J2-J4（僅帶諧，不含田諧） | Orekit NumericalPropagator（HolmesFeatherstone 4×0，DormandPrince853 相對容差 1e-12）；STK HPOP（重力場 4×0、無其他攝動） | 位置差 ≤ 10 m | 待指派 | 輸入凍結，待執行 |
| G3 | GEO J2–J4 7 天 | L2 | two_body、zonal: J2-J4 | Orekit NumericalPropagator；STK HPOP | 位置差 ≤ 10 m | 待指派 | 輸入凍結，待執行 |
| G4 | LEO 400 km 指數大氣阻力 1 天 | L2（僅報告） | two_body、zonal: J2-J4、drag: exp | Orekit SimpleExponentialAtmosphere；STK HPOP Exponential | 僅報告差異（大氣模型實作差異） | 待指派 | 輸入凍結，待執行 |
| G5 | 固定幾何 Pc（4 例） | L4 | — | NASA CARA Analysis Tools（Pc2D_Foster）；STK CAT／AdvCAT（Foster 1992） | Pc ≥ 1e-6：相對差 ≤ 5%；以下：絕對差 ≤ 1e-8 | 待指派 | 輸入凍結，內部值已算出，待外部比對 |
| G6 | 合成 LEO 會合端到端 | L0／L4 | — | STK AdvCAT（HPOP 4×0）；Orekit 自行搜尋 | TCA 差 ≤ 1 ms（同力模型）；最小距離差 ≤ 1 m；Pc 依 G5 門檻 | 待指派 | 輸入凍結，待執行 |
| G7 | TEME→ITRF→GCRF（Vallado 2006 範例） | L1 | — | Vallado (2006) AIAA 2006-6753 範例 | 位置差 ≤ 2 cm（參考值有效位數限制） | 自動測試（tests/test_phase2.py、cargo test） | 已通過（Python 與 Rust） |

工具版本（Orekit、STK）待負責人指派後填入並凍結；初始狀態等完整輸入見 golden_cases.json。

- 清單凍結後，輸入、常數與門檻的任何修改須升版本號並記錄理由
- 外部工具版本、執行者與執行日期於結果回填時記錄
- 比對結果存於 benchmarks/results/<id>/
