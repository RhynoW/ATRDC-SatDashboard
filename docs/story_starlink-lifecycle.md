# Starlink 一顆衛星的一生：發射・抬軌・運營・離軌

**用半長軸曲線讀懂低軌星系的生命週期 — 三顆處於不同階段的衛星**

同一套 TLE 資料、同一張 SMA 圓形時間圖：抬軌中的 STARLINK-37457（2026-08-11 發射）、運營中的 STARLINK-3005（2021 年發射、563 km 站位保持）、離軌中的 STARLINK-4437（30 天下降 143 km、再入在即）。星系統計與離軌清單為 2026-08-28 盤點。

> 故事內容更新：2026-08-28  
> 資料快照：2026-08-28 11:14 UTC  
> 文件匯出：2026-08-28T12:44:36+00:00  
> 互動版：[https://rhynowu-atrdc-satdashboard.hf.space/story/starlink-lifecycle](https://rhynowu-atrdc-satdashboard.hf.space/story/starlink-lifecycle)

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
| 歷史資料範圍 | 2021-12-23 ～ 2026-08-27（含少數未來 epoch） |
| 可用於目前傳播 | 最新 TLE ≤ 7 天者 29339 顆 |
| TLE 最新 epoch（≤ 匯出時） | 2026-08-27 22:02 UTC |
| TLE 資料齡 | 0.6 天（匯出時） |
| 資料快照（DB 更新） | 2026-08-28 11:14 UTC |
| 傳播模型 | SGP4/SDP4（python-sgp4 2.25） |
| 座標系 | SGP4 輸出 TEME；以 UTC 近似 GMST 作 TEME→ECEF 旋轉，再依 WGS-84 橢球求地理經緯度與大地高；未納入極移、章動、UT1−UTC 與完整 ITRF 地球定向參數（地面位置屬態勢展示等級） |
| 精度等級 | 公開 TLE 級（LEO 沿軌 1–3 km/日量級增長），非精密星曆；不宜作為操作級決策依據 |
| Pc proxy | Chan (2008) 2-D 近似；σ_R/T/N = 100/500/100 m 為固定假設值（非 CDM 協方差），Pc 僅供排序 |
| 機動候選 | 相鄰 TLE 半長軸跳變 \|Δa\| 門檻（LEO 0.5 km、MEO/GEO 2 km，間隔 ≤5 天）之候選事件；Δv 由 Δa 以 Δv≈n·Δa/2 換算之等效值；替代解釋：TLE 品質波動、阻力模型誤差、資料缺漏 |
| APP 版本 | git commit 3b8c593 |
| 文件狀態 | 技術展示／非操作級 |
| 匯出時間 | 2026-08-28T12:44:36+00:00 |

## 導言：一條曲線講完一生

Starlink 衛星的一生約 5 年：火箭把它放到 300 km 上下的停泊軌道，氬離子推進器花 1～3 個月把半長軸抬到 480～570 km 的工作殼層；接著是數年的站位保持（每月半長軸變化不到 1 km）；壽命末期主動降軌，最後數百公里由大氣阻力接手，數週內再入燒毀。這三個階段在 TLE 的半長軸（SMA）時序上是三種完全不同的形狀——上升斜坡、水平直線、加速下墜。本故事以本系統 /orbit 逐日軌道要素圖，各挑一顆正處於該階段的衛星。

## ① 星系全貌：殼層與軌道面（3D）

目錄中全部 Starlink 的 TLE 傳播位置（近即時；約 1.1 萬顆，含抬軌中與離軌中者）。以實際高度比例呈現可清楚看到 53°／43°／70° 等傾角族群的軌道面，以及貼近地表、高度較低的抬軌與離軌個體。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：[positions API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=group&val=starlink)（mode=group, val=starlink）
- 互動版：[開啟本節](https://rhynowu-atrdc-satdashboard.hf.space/story/starlink-lifecycle)

## ② 星系儀表板：高度／傾角分佈與歷年發射累積

高度直方圖的主峰在 400～520 km（約 6,400 顆穩定站位）與 560～580 km（約 670 顆，2021 年前後的 v1.0/v1.5 殼層）；400 km 以下約 640 顆，多為抬軌中新星與離軌中舊星的混合。發射年份長條反映 2022 年後的加速部署。

*（互動區塊：群組儀表板）*
- 資料：[group_stats API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/group_stats?group=starlink)（group=starlink）
- 互動版：[開啟本節](https://rhynowu-atrdc-satdashboard.hf.space/story/starlink-lifecycle)

## ③ 生命週期四階段（本故事代表星）

| 階段 | 典型高度 | SMA 變化率 | 歷時 | 代表衛星 | NORAD | 發射日 |
|---|---|---|---|---|---|---|
| 發射／停泊 | 280～350 km | — | 數天 | （同批 2026-08-11 發射 20 餘顆） | 100279–100298 | 2026-08-11 |
| 抬軌（電推進） | 350 → 480～570 km | +3～+6 km/日 | 1～3 個月 | STARLINK-37457 | 100294 | 2026-08-11 |
| 運營（站位保持） | 480～570 km | \|Δa\| < 1 km/月 | 約 5 年 | STARLINK-3005 | 48881 | 2021-06-30 |
| 離軌（主動降軌 + 阻力） | 570 → < 200 km | −2 → −10 km/日（加速） | 數月，末段數週 | STARLINK-4437 | 53506 | 2022-08-12 |

> 高度／變化率為本系統 TLE 統計之典型值（2026-08 盤點），非官方規格。NORAD ≥ 100000 之新星以 Alpha-5 六位數編目，本系統全鏈支援。

## ④ 抬軌中：STARLINK-37457（NORAD 100294，2026-08-11 發射）

發射後一週開始有 TLE，半長軸自約 6,660 km（高度 ~285 km）以每日數公里的斜率爬升，10 天內已抬升約 60 km，目前高度約 344 km，仍在前往工作殼層途中。Spiral Polar 圖上 RAAN 隨時間規律漂移、傾角維持 53° 不變——這是同批 20 餘顆衛星同時抬軌、逐步分散到目標軌道面的典型樣貌。可切換同批的 100293、100298 比較抬軌進度。

*（互動區塊：逐日軌道要素時序（/orbit））*
- NORAD 100294：[/orbit 軌道時序](https://rhynowu-atrdc-satdashboard.hf.space/orbit?norad=100294&start=2026-08-11)（norad=100294, start=2026-08-11）
- NORAD 100293：[/orbit 軌道時序](https://rhynowu-atrdc-satdashboard.hf.space/orbit?norad=100293&start=2026-08-11)（norad=100293, start=2026-08-11）
- NORAD 100298：[/orbit 軌道時序](https://rhynowu-atrdc-satdashboard.hf.space/orbit?norad=100298&start=2026-08-11)（norad=100298, start=2026-08-11）
- 互動版：[開啟本節](https://rhynowu-atrdc-satdashboard.hf.space/story/starlink-lifecycle#raising)

## ⑤ 運營中：STARLINK-3005（NORAD 48881，2021-06-30 發射）

服役第 6 年，高度 563 km，近 30 天半長軸變化僅 +0.09 km——SMA 圓形圖幾乎是一個完美的圓，偶爾的微小鋸齒就是站位保持點火（本系統機動偵測的 Δa 門檻 0.5 km 正是為了與這類微幅修正區隔）。對照 49161（STARLINK-3080，572 km 殼層）可看出不同殼層的站位高度差。

*（互動區塊：逐日軌道要素時序（/orbit））*
- NORAD 48881：[/orbit 軌道時序](https://rhynowu-atrdc-satdashboard.hf.space/orbit?norad=48881&start=2026-03-31)（norad=48881, start=2026-03-31）
- NORAD 49161：[/orbit 軌道時序](https://rhynowu-atrdc-satdashboard.hf.space/orbit?norad=49161&start=2026-03-31)（norad=49161, start=2026-03-31）
- 互動版：[開啟本節](https://rhynowu-atrdc-satdashboard.hf.space/story/starlink-lifecycle#operating)

## ⑥ 離軌中：STARLINK-4437（NORAD 53506，2022-08-12 發射）

服役僅 4 年即進入離軌程序：先以推進器主動降軌，離開工作殼層後大氣阻力隨高度下降而指數增強，近 30 天半長軸下降 143 km、近 7 天下降 70 km，目前高度約 141 km，再入已是數日之內。SMA 圓形圖上的螺旋向內收斂就是「一生的終點」。備選 46674（STARLINK-1718，2020 年）與 57156（STARLINK-6137，2023 年）處於同一階段。若本頁載入時該星已無新 TLE，即代表已再入。

*（互動區塊：逐日軌道要素時序（/orbit））*
- NORAD 53506：[/orbit 軌道時序](https://rhynowu-atrdc-satdashboard.hf.space/orbit?norad=53506&start=2026-03-31)（norad=53506, start=2026-03-31）
- NORAD 46674：[/orbit 軌道時序](https://rhynowu-atrdc-satdashboard.hf.space/orbit?norad=46674&start=2026-03-31)（norad=46674, start=2026-03-31）
- NORAD 57156：[/orbit 軌道時序](https://rhynowu-atrdc-satdashboard.hf.space/orbit?norad=57156&start=2026-03-31)（norad=57156, start=2026-03-31）
- 互動版：[開啟本節](https://rhynowu-atrdc-satdashboard.hf.space/story/starlink-lifecycle#deorbit)

## ⑦ 正在離軌的 Starlink：最接近再入者（2026-08-28 盤點）

| NORAD | 衛星 | 發射日 | 目前高度 (km) | 30 天 Δa (km) | 7 天 Δa (km) |
|---|---|---|---|---|---|
| 53506 | STARLINK-4437 | 2022-08-12 | 141 | −143 | −70 |
| 46674 | STARLINK-1718 | 2020-10-18 | 145 | −152 | −74 |
| 47657 | STARLINK-2041 | 2021-02-16 | 149 | −142 | −79 |
| 46142 | STARLINK-1597 | 2020-08-18 | 150 | −148 | −75 |
| 57156 | STARLINK-6137 | 2023-06-23 | 168 | −153 | −53 |
| 46743 | STARLINK-1892 | 2020-10-24 | 170 | −133 | −71 |

> 盤點口徑：近 5 天仍有 TLE、近 30 天半長軸下降逾 40 km 且高度低於 480 km 者共 381 顆；依發射年份為 2020:13、2021:18、2022:227、2023:30、2024:2、2025:91——2022 年批次進入集中退役期，2025 年批次則多為早期失效個體。資料：本系統 space_db（Space-Track GP）。

## ⑧ 對台灣的意義：Starlink 幾何可用性與延遲

一顆衛星的一生最終要回到服務：本系統的 Starlink 台灣服務能力分析，以即時 TLE 計算台北（可改座標／遮蔽仰角）上空可見衛星數時間軸、RTT 下限與天空密度。運營中的衛星數量、殼層分佈與退役節奏，直接決定這張圖的高低起伏。

*（互動區塊：內嵌頁面）*
- 頁面：[/starlink](https://rhynowu-atrdc-satdashboard.hf.space/starlink)
- 互動版：[開啟本節](https://rhynowu-atrdc-satdashboard.hf.space/story/starlink-lifecycle)

## 結語

三條曲線、三種形狀：上升斜坡、水平直線、向內螺旋。從 TLE 半長軸就能不靠任何內部資料判讀一顆衛星處於生命週期的哪個階段；把同樣的判讀套到整個星系，就得到「每年有多少顆在抬軌、多少顆在退役」的星系新陳代謝率——這也是本系統機動偵測（Δa 門檻、da_monotonic_decay 阻力排除旗標）所依據的物理直覺。

---

*本文件由 SatDashboard StoryMap 匯出（tools/export_story_md.py）。軌道數據為公開 TLE 以 SGP4 傳播之結果，屬態勢展示等級；互動圖表請開啟線上版。*
