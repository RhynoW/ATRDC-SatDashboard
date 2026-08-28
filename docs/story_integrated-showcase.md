# 太空態勢整合展示：星系・機動・地基追蹤・碰撞預警

**從八大星系到一座假想的台灣雷達站——同一套軌道資料的四種能力**

① 星系展示（GPS／北斗／Starlink／OneWeb／大陸 ISR 遙感／大陸通訊）② 2026 年度機動偵測成果 ③ 台灣假想地基雷達站建立前後之追蹤效益評估與即時追蹤 ④ 碰撞預警示範。全部以即時 TLE 與本系統偵測成果計算。

> 更新：2026-08-27　|　互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

### 資料口徑

| 項目 | 內容 |
|---|---|
| 資料來源 | Space-Track GP（公開 TLE）；本系統 DuckDB 每日彙整 |
| TLE epoch 範圍 | 2021-12-23 ～ 2026-08-27（31707 顆） |
| TLE 最新 epoch（≤ 匯出時） | 2026-08-27 22:02 UTC |
| TLE 資料齡 | 0.6 天（匯出時） |
| 資料庫更新 | 2026-08-28 11:14 UTC |
| 傳播模型 | SGP4/SDP4（python-sgp4 2.25） |
| 座標系 | TEME → GMST 轉 ECEF → WGS-84 經緯高（無極移／章動修正） |
| 精度等級 | 公開 TLE 級（LEO 沿軌 1–3 km/日量級增長），非精密星曆；不宜作為操作級決策依據 |
| 碰撞機率 | Chan (2008) 2-D 近似；σ_R/T/N = 100/500/100 m 為固定假設值（非 CDM 協方差），Pc 僅供排序 |
| 機動候選 | 相鄰 TLE 半長軸跳變 \|Δa\| 門檻（LEO 0.5 km、MEO/GEO 2 km，間隔 ≤5 天）之候選事件；Δv 由 Δa 以 Δv≈n·Δa/2 換算之等效值；替代解釋：TLE 品質波動、阻力模型誤差、資料缺漏 |
| 匯出時間 | 2026-08-28T12:03:24+00:00 |

## 章節總覽

四個子題各自獨立，點磚直達；全篇 3D 圖一律以顏色區分軌道域：LEO 青、MEO 綠、GEO／IGSO 琥珀，並繪出 MEO 與 GEO 參考環。

- 🛰️ **① 星系展示** — GPS・北斗・Starlink・OneWeb・大陸 ISR・大陸通訊：3D TLE 傳播位置＋儀表板＋偵照解析度分類
- 📈 **② 2026 機動偵測** — 六星系年度機動候選事件：月分佈與活躍衛星
- 📡 **③ 台灣地基雷達站** — 建立前後追蹤效益評估＋即時過頂追蹤示範
- ⚠️ **④ 碰撞預警** — 即時 <10 km 接近配對與 3D 相對接近場景

## 第一部：星系展示

每個星系先以 3D 自動旋轉呈現 TLE 傳播軌道位置（純色底、實高度比例、軌道域配色、衛星名稱），接著是儀表板：數量、軌道域、高度／傾角分佈、歷年發射累積，以及可點入逐日軌道歷史（/orbit）的代表衛星。

## GPS（NAVSTAR）— TLE 傳播軌道位置

美國全球定位系統，MEO 約 20,200 km、傾角 55°、六個軌道面。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=group&val=gps
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## GPS — 儀表板

*（互動區塊：群組儀表板）*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/group_stats?group=gps
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## 北斗（BeiDou）— TLE 傳播軌道位置

中國北斗導航系統：MEO＋IGSO＋GEO 三種軌道混合構型，是與 GPS 最大的結構差異。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=group&val=beidou
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## 北斗 — 儀表板

*（互動區塊：群組儀表板）*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/group_stats?group=beidou
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## Starlink — TLE 傳播軌道位置

SpaceX 巨型星座，目錄中逾萬顆；多殼層（約 340／530／550／570 km）與 53°／70°／97.6° 傾角面。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=group&val=starlink
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## Starlink — 儀表板

*（互動區塊：群組儀表板）*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/group_stats?group=starlink
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## OneWeb — TLE 傳播軌道位置

極軌 1,200 km、傾角 87.9° 的通訊星座，與 Starlink 的高度／傾角策略形成對照。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=group&val=oneweb
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## OneWeb — 儀表板

*（互動區塊：群組儀表板）*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/group_stats?group=oneweb
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## 大陸 ISR／遙感衛星（遙感／尖兵、高分、吉林、天繪、海洋、TJS…，含北斗）

依美國太空軍《Space Threat Fact Sheet》口徑——解放軍可取用之具光學、多光譜、雷達、射頻感測器衛星（不分軍用、國家民用、商業）——本目錄可辨識 581 顆：軍用遙感（尖兵）160、天繪 14、雲海 18、寧夏一號 10、TJS 訊號 26、實驗／實踐 73、高分 34、海洋 10、資源／環境 23、吉林一號 30、四維／珠海等商業 EO／SAR 61、風雲／天目／雲遙氣象 60，合計 519；加北斗導航 62 即 581，對應 USSF「510+ ISR」與 UPI「成像＋導航＋SIGINT 595 顆」之量級。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=group&val=prc_isr
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## 大陸 ISR／遙感（含北斗）— 儀表板

*（互動區塊：群組儀表板）*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/group_stats?group=prc_isr
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## 偵照衛星感測器類型與光學解析度分類統計

把 581 顆依型號級別的公開文獻分為光學／SAR／射頻訊號／氣象掩星／技術試驗／導航，光學再依最佳公開解析度分級（≤0.5 m、0.5–1 m、1–5 m、5–30 m、>30 m）。軍用遙感系列官方未公開，解析度為推估並標註。

*（互動區塊：感測器／解析度分類）*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/isr_resolution?group=prc_isr
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## 大陸通訊星系（千帆、國網、中星、亞太、天鏈、天通）

低軌巨型星座千帆（Qianfan／SpaceSail）與國網（Guowang／衛星互聯網），加上 GEO 的中星、亞太、天鏈中繼與天通移動通訊。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=group&val=prc_comm
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## 大陸通訊星系 — 儀表板

*（互動區塊：群組儀表板）*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/group_stats?group=prc_comm
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## 第二部：2026 年度機動偵測成果

對上述六個星系，以本系統的統計級偵測（相鄰 TLE 半長軸跳變超過門檻：LEO 0.5 km、MEO/GEO 2 km，間隔 ≤5 天）掃描 2026 年 1 月至今的全部 TLE；大陸群組另併入 prc_maneuver 管線（da／di／de／dΩ 複合評分）之 1–5 月旗標事件。數字為「候選事件」，Starlink 的高計數反映其連續電推進站位保持。

## 各星系 2026 年機動候選事件：月分佈與活躍衛星

*（互動區塊：機動候選事件統計）*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/maneuvers
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## 第三部：台灣假想地基雷達站

假設台灣建立一座地基雷達追蹤站（以新竹樂山為址），並與全球已知地面追蹤站（本系統 SSN 站點圖層）合作交換觀測資料。以大陸 ISR 低軌衛星為樣本，比較「建立前（僅全球既有站）」與「建立後（＋台灣站）」24 小時內的追蹤弧段數、最大無觀測間隙、累計追蹤時間，並以 σ∝1/√(觀測量) 之簡化模型估算定軌精度提升。

## 建立前 vs 建立後：追蹤效益評估（大陸 ISR 低軌樣本 30 顆）

*（互動區塊：假想雷達站效益評估）*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/radar_eval?group=prc_isr
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## 即時追蹤示範：台灣站對大陸遙感衛星之未來過頂（方位／仰角天空圖）

由台灣站視角預報未來 24 小時過頂：AOS／LOS、最大仰角與航跡；播放時標記沿航跡移動，模擬雷達／地面站的即時追蹤與連線視窗。

*（互動區塊：過頂 Skyplot（radar_eval 之視圖））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/radar_eval?group=prc_isr
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## 3D 即時追蹤（Cesium）：台北站覆蓋與時間軸

本系統既有的台北站即時覆蓋與過頂時間軸（Cesium 3D）——地面站視角的即時追蹤／連線示範。

*（互動區塊：內嵌頁面）*
- 頁面：https://rhynowu-atrdc-satdashboard.hf.space/taipei
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## 第四部：碰撞預警示範

以全目錄向量化 SGP4 掃描目前 10 km 內的接近配對，列出最接近距離、TCA 與碰撞機率（Chan 首階排序代理），任一配對可展開 3D 相對接近場景與 3σ 誤差橢球。

## 即時接近配對（<10 km）與 3D 展開

*（互動區塊：幾何接近事件（篩選，非碰撞風險判定））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/conjunctions?threshold_km=10
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase

## 結語

星系全景、機動偵測、地基追蹤效益、碰撞預警——四種能力共用同一份 TLE 資料與同一套傳播／偵測引擎。

---

*本文件由 SatDashboard StoryMap 匯出（tools/export_story_md.py）。軌道數據為公開 TLE 以 SGP4 傳播之結果，屬態勢展示等級；互動圖表請開啟線上版。*
