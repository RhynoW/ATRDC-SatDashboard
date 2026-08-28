# 太空態勢整合展示：星系・機動・地基追蹤・碰撞預警

**從六大星系到一座假想的台灣地面追蹤站——同一套公開軌道資料的四種能力**

① 星系展示（GPS／北斗／Starlink／OneWeb／大陸 ISR 遙感／大陸通訊）② 2026 年度機動候選事件 ③ 台灣假想地面追蹤站建立前後之可見性與觀測覆蓋評估 ④ 幾何接近事件篩選示範。全部以最新可取得之公開 TLE（Space-Track GP）進行近即時 SGP4/SDP4 傳播，並結合本系統統計分析；結果屬公開資料與簡化模型之技術展示，不代表精密星曆、實際雷達偵測能力、已確認機動或操作級碰撞機率。

> 故事內容更新：2026-08-28  
> 資料快照：2026-08-28 11:14 UTC  
> 文件匯出：2026-08-28T13:10:42+00:00  
> 互動版：[開啟「太空態勢整合展示：星系・機動・地基追蹤・碰撞預警」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase)

### 使用限制

本展示以最新可取得之公開 TLE 為基礎，使用 SGP4/SDP4 進行近即時軌道傳播，整合星座態勢、軌道異常候選、地面站可見性與近距離幾何事件。結果屬公開資料與簡化模型的技術展示，不代表精密星曆、實際雷達偵測能力、已確認機動或操作級碰撞機率。

| 限制 | 影響 |
|---|---|
| 公開 TLE + SGP4 | 位置為近即時傳播估計，非精密星曆；不適合精密編隊或操作級決策 |
| 固定協方差 σ_R/T/N | Pc 為 proxy／排序值，非 CDM 碰撞風險；3σ 橢球為示意 |
| 半長軸跳變門檻 | 機動結果為「候選事件」，非確認機動；Δv 為近圓切向脈衝之等效估算 |
| 幾何可見性模型 | 地面站評估為可見性／觀測覆蓋，不含雷達方程式，不代表偵測能力 |
| 公開解析度推估 | 感測器／解析度分類為公開資料分類，不代表實際任務效能 |
| 名稱規則分類 | 星系／國別／用途由目錄名稱與 metadata 規則判定，受資料完整性影響 |

### 資料口徑

| 項目 | 內容 |
|---|---|
| 資料來源 | Space-Track GP（公開 TLE）；本系統 DuckDB 每日彙整 |
| 目錄衛星數 | 31707 顆（去重 NORAD，含歷史） |
| TLE 記錄數 | 857961 筆 |
| TLE epoch 範圍 | 2021-12-23 ～ 2026-08-27 |
| epoch 品質註記 | 相對於文件匯出時間，資料庫含少數 epoch 較晚之紀錄（GEO 等常見）；此類紀錄不作為歷史回放或「目前」狀態值，計算最新 epoch 與資料齡時已排除 |
| 資料齡篩選通過（不代表全部傳播成功） | 29330 顆；最新 TLE 年齡 ≤ 7 天（資料齡篩選）；未逐一檢查 SGP4 誤差碼與衰減／再入狀態 |
| TLE 最新 epoch（≤ 匯出時） | 2026-08-27 22:02 UTC |
| TLE 資料齡 | 0.6 天（相對於文件匯出時間之最新 TLE epoch） |
| 資料快照（DB 更新） | 2026-08-28 11:14 UTC |
| 傳播模型 | python-sgp4 2.25，依軌道週期自動使用 SGP4（近地）或 SDP4（週期 ≥225 min 深空） |
| 座標系 | SGP4 輸出 TEME；以 UTC 近似 UT1 計算 GMST 作 TEME→ECEF 旋轉，再依 WGS-84 橢球求地理經緯度與大地高；未納入極移、章動、UT1−UTC 與完整 ITRF 地球定向參數（地面位置屬態勢展示等級） |
| 精度等級 | 公開 TLE 級（LEO 沿軌 1–3 km/日量級增長），非精密星曆；不宜作為操作級決策依據 |
| 幾何接近篩選 | 幾何接近篩選為單一傳播時刻（請求當下 UTC）之全目錄 pairwise 距離篩選（KD-tree），非時間窗 TCA 搜尋；展開 3D 後才於兩星 TLE 重疊期間以 30 分鐘取樣間隔粗掃（區間過長時放大至總點數 ≤1,500）求全域最小距離，再於最接近時刻 ±12 h 聚焦窗以 60 秒取樣細掃；距離門檻僅用於初始篩選 |
| Pc proxy（碰撞風險排序代理值） | Chan (2008) 2-D 簡化式：以相對 RTN 框架之固定示意標準差 σ_R/σ_T 合成單一相對協方差（σ_R/T/N = 100/500/100 m；σ_N 僅用於 3D 橢球繪製），等效碰撞半徑 5 m，最接近點 B-plane 假設；非兩星各自 CDM 協方差相加，Pc proxy 僅供事件排序 |
| 機動候選 | 相鄰 TLE 半長軸跳變 \|Δa\| 門檻（LEO 0.5 km、MEO/GEO 2 km，間隔 ≤5 天）之候選事件；Δv 由 Δa 以 Δv≈n·Δa/2 換算之等效值；替代解釋：TLE 品質波動、阻力模型誤差、資料缺漏 |
| 分類規則版本 | ISR_RES_RULES v1.0（commit 6ed982d） |
| APP 版本 | git commit 6ed982d |
| 文件狀態 | 技術展示／非操作級 |
| 匯出時間 | 2026-08-28T13:10:42+00:00 |

## 章節總覽

四個子題各自獨立，點磚直達，分別對應四種能力：1. 星系態勢展示；2. 軌道異常候選偵測；3. 地面站幾何可見性與觀測覆蓋；4. 幾何接近事件篩選。全篇 3D 圖一律以顏色區分軌道域：LEO 青、MEO 綠、GEO／IGSO 琥珀，並繪出 MEO 與 GEO 參考環。

- 🛰️ **① 星系展示** — GPS・北斗・Starlink・OneWeb・大陸 ISR・大陸通訊：3D TLE 傳播位置＋儀表板＋偵照解析度分類
- 📈 **② 2026 機動偵測** — 六星系年度機動候選事件：月分佈與活躍衛星
- 📡 **③ 台灣地面追蹤站** — 建立前後可見性與觀測覆蓋評估＋過頂追蹤示範
- ⚠️ **④ 幾何接近篩選** — <10 km 幾何接近配對與 3D 相對接近場景（Pc proxy）

## 第一部：星系展示

本部完成後，讀者應能比較六個星系的高度、傾角、軌道域與目錄規模，但不應把 TLE 傳播位置視為精密定軌結果。每個星系先以 3D 自動旋轉呈現 TLE 傳播軌道位置（純色背景、按實際高度比例呈現、依軌道域配色並標示衛星名稱），接著是儀表板：數量、軌道域、高度／傾角分佈、歷年發射累積，以及可點入逐日軌道歷史（/orbit）的代表衛星。歷年發射累積以目錄 metadata（中繼資料：sat_metadata.csv 之 launch_date）統計，缺發射日期者不計入，不代表完整歷史發射數。

## GPS（NAVSTAR）— TLE 傳播軌道位置

美國全球定位系統，MEO 約 20,200 km、傾角 55°、六個軌道面。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：[positions API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=group&val=gps)（mode=group, val=gps）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「GPS（NAVSTAR）— TLE 傳播軌道位置」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#pos-gps)

## GPS — 儀表板

*（互動區塊：群組儀表板）*
- 資料：[group_stats API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/group_stats?group=gps)（group=gps）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「GPS — 儀表板」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#stats-gps)

## 北斗（BeiDou）— TLE 傳播軌道位置

中國北斗導航系統：MEO＋IGSO＋GEO 三種軌道混合構型，是與 GPS 最大的結構差異。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：[positions API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=group&val=beidou)（mode=group, val=beidou）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「北斗（BeiDou）— TLE 傳播軌道位置」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#pos-beidou)

## 北斗 — 儀表板

*（互動區塊：群組儀表板）*
- 資料：[group_stats API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/group_stats?group=beidou)（group=beidou）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「北斗 — 儀表板」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#stats-beidou)

## Starlink — TLE 傳播軌道位置

SpaceX 巨型星座，目錄中逾萬顆；多殼層（約 340／530／550／570 km）與 53°／70°／97.6° 傾角面。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：[positions API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=group&val=starlink)（mode=group, val=starlink）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「Starlink — TLE 傳播軌道位置」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#pos-starlink)

## Starlink — 儀表板

*（互動區塊：群組儀表板）*
- 資料：[group_stats API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/group_stats?group=starlink)（group=starlink）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「Starlink — 儀表板」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#stats-starlink)

## OneWeb — TLE 傳播軌道位置

極軌 1,200 km、傾角 87.9° 的通訊星座，與 Starlink 的高度／傾角策略形成對照。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：[positions API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=group&val=oneweb)（mode=group, val=oneweb）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「OneWeb — TLE 傳播軌道位置」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#pos-oneweb)

## OneWeb — 儀表板

*（互動區塊：群組儀表板）*
- 資料：[group_stats API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/group_stats?group=oneweb)（group=oneweb）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「OneWeb — 儀表板」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#stats-oneweb)

## 大陸 ISR／遙感衛星（遙感／尖兵、高分、吉林、天繪、海洋、TJS…，含北斗）

依美國太空軍《Space Threat Fact Sheet》口徑——解放軍可取用之具光學、多光譜、雷達、射頻感測器衛星（不分軍用、國家民用、商業）——本目錄以名稱規則辨識（各系列數量以下方儀表板與分類統計之即時值為準；2026-08 初盤點為 519 顆＋北斗 62＝581，對應 USSF「510+ ISR」與 UPI「成像＋導航＋SIGINT 595 顆」之量級）。分類以 NORAD ID 去重、單一主分類、規則先到先得（優先順序見 ISR_RES_RULES；分類規則版本與 commit 見資料口徑表）；提供「含北斗」與「不含北斗」兩種口徑。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：[positions API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=group&val=prc_isr)（mode=group, val=prc_isr）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「大陸 ISR／遙感衛星（遙感／尖兵、高分、吉林、天繪、海洋、TJS…，含北斗）」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#pos-prc-isr)

## 大陸 ISR／遙感（含北斗）— 儀表板

*（互動區塊：群組儀表板）*
- 資料：[group_stats API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/group_stats?group=prc_isr)（group=prc_isr）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「大陸 ISR／遙感（含北斗）— 儀表板」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#stats-prc-isr)

## 偵照衛星感測器類型與光學解析度分類統計

把群組依型號級別的公開文獻分為光學／SAR／射頻訊號／氣象掩星／技術試驗／導航，光學再依「公開報導之最佳解析度」分級（≤0.5 m、0.5–1 m、1–5 m、5–30 m、>30 m）。此為公開資料分類，不代表實際在軌解析度、當日任務解析度或目標辨識能力；軍用遙感系列官方未公開，解析度為推估並標註。

*（互動區塊：感測器／解析度分類（公開資料分類））*
- 資料：[isr_resolution API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/isr_resolution?group=prc_isr)（group=prc_isr）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「偵照衛星感測器類型與光學解析度分類統計」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#isr-resolution)

## 大陸通訊星系（千帆、國網、中星、亞太、天鏈、天通）

低軌巨型星座千帆（Qianfan／SpaceSail）與國網（Guowang／衛星互聯網），加上 GEO 的中星、亞太、天鏈中繼與天通移動通訊。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：[positions API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=group&val=prc_comm)（mode=group, val=prc_comm）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「大陸通訊星系（千帆、國網、中星、亞太、天鏈、天通）」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#pos-prc-comm)

## 大陸通訊星系 — 儀表板

*（互動區塊：群組儀表板）*
- 資料：[group_stats API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/group_stats?group=prc_comm)（group=prc_comm）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「大陸通訊星系 — 儀表板」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#stats-prc-comm)

## 第二部：2026 年度機動偵測成果

本部完成後，讀者應能看出各星系「候選事件」的規模與月分佈，並理解候選≠確認。對上述六個星系，以本系統的統計級偵測（相鄰 TLE 半長軸跳變超過門檻：LEO 0.5 km、MEO/GEO 2 km，間隔 ≤5 天）掃描 2026 年 1 月至今的全部 TLE；大陸群組另併入 prc_maneuver 管線（da／di／de／dΩ 複合評分）之 1–5 月旗標事件。Starlink 的候選事件數較高，部分可能與其龐大樣本數、頻繁軌道調整及電推進站位保持有關，也受 TLE 更新頻率、阻力模型與資料品質影響，不能單獨據此確認推進活動；跨星系比較請看「每 100 顆衛星事件率」而非總數。等效 Δv = n·Δa/2 為近圓、瞬時切向脈衝之一階估算，對高偏心率或低推力機動不適用。

## 各星系 2026 年機動候選事件：月分佈與活躍衛星

*（互動區塊：機動候選事件統計）*
- 資料：[maneuvers API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/maneuvers)
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「各星系 2026 年機動候選事件：月分佈與活躍衛星」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#maneuvers-2026)

## 第三部：台灣假想地面追蹤站——可見性與觀測覆蓋評估

本部完成後，讀者應能量化「多一座站」對觀測弧段與間隙的影響，但不應把它讀成定軌精度或雷達偵測能力。假設台灣建立一座地面追蹤站（以新竹樂山為址：24.395°N、120.905°E、海拔 2.6 km），並與全球已知地面追蹤站（本系統 SSN 站點圖層）合作交換觀測資料。以大陸 ISR 低軌衛星為樣本，比較「建立前（僅全球既有站）」與「建立後（＋台灣站）」24 小時內的可見弧段數、最大無觀測間隙、累計可見時間，並以 σ∝1/√(觀測量) 建立「相對觀測資訊增益」概念指標。模型為純幾何可見性（仰角 >5°、SGP4 步長 60 s），未納入雷達方程式、SNR、RCS、地形遮蔽與大氣折射，故為可見性評估而非雷達偵測效益。API 名稱 radar_eval 沿用既有實作，實際輸出為幾何可見性與觀測覆蓋指標。本部所有評估皆以樂山站為主體；第 20 節內嵌之台北頁面為本系統既有的獨立地面站視覺化示範，不納入前述比較。

## 建立前 vs 建立後：可見性與觀測覆蓋評估（大陸 ISR 低軌樣本 30 顆）

*（互動區塊：假想地面追蹤站可見性與觀測覆蓋評估（API 名稱 radar_eval 沿用既有實作，輸出為幾何可見性指標））*
- 資料：[radar_eval API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/radar_eval?group=prc_isr)（group=prc_isr）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「建立前 vs 建立後：可見性與觀測覆蓋評估（大陸 ISR 低軌樣本 30 顆）」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#visibility-eval)

## 追蹤示範：樂山站對大陸遙感衛星之未來過頂（方位／仰角天空圖）

由樂山站視角以 TLE 預報未來 24 小時過頂：AOS／LOS、最大仰角與航跡；播放時標記沿航跡移動，模擬地面站的追蹤與連線視窗（幾何可見性，非偵測判定）。

*（互動區塊：過頂 Skyplot（radar_eval 之視圖；API 名稱沿用既有實作，輸出為幾何可見性指標））*
- 資料：[radar_eval API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/radar_eval?group=prc_isr)（group=prc_isr）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「追蹤示範：樂山站對大陸遙感衛星之未來過頂（方位／仰角天空圖）」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#skyplot-leshan)

## 3D 追蹤示範（Cesium）：台北站覆蓋與時間軸（既有獨立示範頁）

本系統既有的台北站覆蓋與過頂時間軸（Cesium 3D）——地面站視角的追蹤／連線示範。此頁以台北為站址、屬獨立視覺化示範，與第三部樂山站評估無關。

*（互動區塊：內嵌頁面）*
- 頁面：[/taipei](https://rhynowu-atrdc-satdashboard.hf.space/taipei)
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「3D 追蹤示範（Cesium）：台北站覆蓋與時間軸（既有獨立示範頁）」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#taipei-embed)

## 第四部：幾何接近事件篩選示範

本部完成後，讀者應能理解「距離 <10 km」是篩選門檻而非風險判定。篩選方式：以資料快照為基礎、在單一傳播時刻（請求當下 UTC）以向量化 SGP4 計算全目錄位置，用 KD-tree 做 pairwise 距離篩選；此為單一時刻幾何距離篩選，不是時間窗內的 TCA 搜尋。展開 3D 後才對該配對在重疊期間粗掃（≥30 min 步長）並於聚焦窗細掃（60 s）求最接近時刻。表中列出最接近距離與 Pc proxy（碰撞風險排序代理值：Chan 2-D 近似、固定假設 σ_R/T/N=100/500/100 m，僅供排序，非操作級碰撞機率），3D 場景含「示意性 3σ 橢球」（由固定假設 σ 建立，用於展示相對不確定性概念，不代表任一衛星之實際 CDM 協方差）。

## 幾何接近配對（<10 km，TLE 傳播）與 3D 展開

*（互動區塊：幾何接近事件（單一時刻距離篩選，非碰撞風險判定））*
- 資料：[conjunctions API](https://rhynowu-atrdc-satdashboard.hf.space/api/conjunctions?threshold_km=10)（threshold_km=10）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「幾何接近配對（<10 km，TLE 傳播）與 3D 展開」](https://rhynowu-atrdc-satdashboard.hf.space/story/integrated-showcase#conjunction-screen)

## 結語

星系全景、機動候選、地面站可見性、幾何接近篩選——四種能力共用同一份公開 TLE 與同一套傳播／偵測引擎。
近即時，不是即時；候選，不是確認；幾何接近，不是碰撞風險；
可見性，不是偵測能力；公開解析度分類，不是實際任務效能；
固定假設橢球，不是實際 CDM 協方差。

---

*本文件由 SatDashboard StoryMap 匯出（tools/export_story_md.py）。軌道數據為公開 TLE 以 SGP4 傳播之結果，屬態勢展示等級；互動圖表請開啟線上版。*
