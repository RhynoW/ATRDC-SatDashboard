# Samba 與 Tango 的一生：ESA Cluster 雙星目標式再入觀測

**從 2000 年發射到 ESA 預報於 2026-08-31／09-01 再入——為再入觀測而調整軌道的兩顆衛星**

ESA Cluster 四顆結構相同的磁層科學衛星於 2000 年發射；Salsa 已於 2024-09 完成首次目標式再入（targeted reentry）觀測 [ESA-reported]；Samba（NORAD 26410）與 Tango（26464）依 ESA 2026-08-24 公開預報，預計於 2026-08-31／09-01 在南太平洋再入 [ESA-reported]。本故事以公開 TLE 歷史展示兩星的軌道要素演化，並把 ESA 任務資訊與本系統 TLE 推導結果分開標示：[ESA-reported]＝ESA 公開資訊、[TLE-derived]＝本系統由 TLE 計算、[interpretation]＝作者解讀。公開 TLE 只能提供軌道趨勢，不足以獨立證明點火、任務成功、實際再入時間或解體狀態。

> 故事內容更新：2026-08-28  
> 資料快照：2026-08-28 11:14 UTC  
> 文件匯出：2026-08-28T14:02:45+00:00  
> 互動版：[開啟「Samba 與 Tango 的一生：ESA Cluster 雙星目標式再入觀測」](https://rhynowu-atrdc-satdashboard.hf.space/story/cluster-samba-tango)

### 使用限制

本故事以公開 TLE 歷史與 ESA／外部公開資訊撰寫；下表為本故事專用之使用限制。

| 限制 | 影響 |
|---|---|
| 公開 TLE 歷史 | 只能重建公開軌道要素趨勢，不能重建任務遙測；點火、任務成功與實際再入須以 ESA 任務公告為準 |
| 高偏心率軌道 | 近圓軌道 Δv≈n·Δa/2 近似不適用；本故事不估算真實 Δv，SMA 變化僅作為軌道調整候選指標 |
| TLE 平均要素 | 理想化 Kepler 近地點高度 ≤0 km 表示幾何軌道穿入參考橢球，不是實際飛行高度 |
| 再入預報 | 2026-08-31／09-01 為 ESA 公開預報，非本系統以 TLE 產生；本系統不重新驗證其不確定度 |
| 固定協方差 | Pc proxy／3σ 橢球僅為示意，不是操作級碰撞機率（本故事未使用） |
| 再入估算 | 本系統兩階段估算以公開 TLE＋NRLMSIS 為基礎，回測誤差量級以 Salsa 案例為準；不等同 ESA 以實測軌道決定之預報 |

### 資料口徑

| 項目 | 內容 |
|---|---|
| 資料來源 | Space-Track GP（公開 TLE）；本系統 DuckDB 每日彙整 |
| 目錄衛星數 | 31707 顆（去重 NORAD，含歷史） |
| TLE 記錄數 | 857961 筆 |
| TLE epoch 範圍 | 2021-12-23 ～ 2026-08-27 |
| epoch 品質註記 | 相對於文件匯出時間，資料庫含少數 epoch 較晚之紀錄（GEO 等常見）；此類紀錄不作為歷史回放或「目前」狀態值，計算最新 epoch 與資料齡時已排除 |
| 資料齡篩選通過（不代表全部傳播成功） | 29306 顆；最新 TLE 年齡 ≤ 7 天（資料齡篩選）；未逐一檢查 SGP4 誤差碼與衰減／再入狀態 |
| 本故事案例可用 TLE 範圍 | Salsa 26411：2024-01-01～2024-09-08（153 筆）；Samba 26410：2024-01-01～2026-08-16（820 筆）；Tango 26464：2024-01-01～2026-08-26（886 筆） |
| TLE 最新 epoch（≤ 匯出時） | 2026-08-27 22:02 UTC |
| TLE 資料齡 | 0.7 天（相對於文件匯出時間之最新 TLE epoch） |
| 資料快照（DB 更新） | 2026-08-28 11:14 UTC |
| 傳播模型 | python-sgp4 2.25，依軌道週期自動使用 SGP4（近地）或 SDP4（週期 ≥225 min 深空） |
| 座標系 | SGP4 輸出 TEME；以 UTC 近似 UT1 計算 GMST 作 TEME→ECEF 旋轉，再依 WGS-84 橢球求地理經緯度與大地高；未納入極移、章動、UT1−UTC 與完整 ITRF 地球定向參數（地面位置屬態勢展示等級） |
| 精度等級 | 公開 TLE 級（LEO 沿軌 1–3 km/日量級增長），非精密星曆；不宜作為操作級決策依據 |
| 幾何接近篩選 | 幾何接近篩選為單一傳播時刻（請求當下 UTC）之全目錄 pairwise 距離篩選（KD-tree），非時間窗 TCA 搜尋；展開 3D 後才於兩星 TLE 重疊期間以 30 分鐘取樣間隔粗掃（區間過長時放大至總點數 ≤1,500）求全域最小距離，再於最接近時刻 ±12 h 聚焦窗以 60 秒取樣細掃；距離門檻僅用於初始篩選 |
| Pc proxy（碰撞風險排序代理值） | Chan (2008) 2-D 簡化式：以相對 RTN 框架之固定示意標準差 σ_R/σ_T 合成單一相對協方差（σ_R/T/N = 100/500/100 m；σ_N 僅用於 3D 橢球繪製），等效碰撞半徑 5 m，最接近點 B-plane 假設；非兩星各自 CDM 協方差相加，Pc proxy 僅供事件排序 |
| 機動候選 | 相鄰 TLE 半長軸跳變 \|Δa\| 門檻（LEO 0.5 km、MEO/GEO 2 km，間隔 ≤5 天）之候選事件；Δv 由 Δa 以 Δv≈n·Δa/2 換算之等效值；替代解釋：TLE 品質波動、阻力模型誤差、資料缺漏 |
| APP 版本 | git commit d014aa4 |
| 文件狀態 | 技術展示／非操作級 |
| 匯出時間 | 2026-08-28T14:02:45+00:00 |

## 導言：為什麼要讓兩顆衛星「約好」再入

衛星再入解體的實測資料極為稀少——事件轉瞬即逝、地點多在無人海域，儀器只能靠飛機帶到現場。Cluster 四顆衛星完全相同，於不同軌跡與氣象條件下觀測多次再入，等於一組可重複的解體物理實驗，有助設計下一代「零碎片」（zero-debris、design-for-demise）衛星 [ESA-reported]。Samba 與 Tango 原已處於安全處置軌道、會自然再入南太平洋，但兩者再入的時間與地點相距太遠，單一飛機任務無法兼顧。ESA 飛行動力團隊在 2026 年 1 月 19～20 日各執行一次小幅點火：Samba 再入點略向東、Tango 略向西，讓兩次再入相隔約 24 小時，觀測團隊得以返回基地加油、輪休後再次出勤 [ESA-reported，C1/C3]。ESA 使用的術語為 targeted reentry（目標式再入），指經軌道調整使自然再入落在預定海域與時間窗，並非可精確控制落點的 controlled re-entry [interpretation]。

## ① Cluster 四星：同一批孿生衛星的四種結局 [ESA-reported / 目錄]

| 衛星 | NORAD | 國際編號 | 發射 | 結局 |
|---|---|---|---|---|
| Rumba（Cluster 1 / FM5） | 26463 | 2000-045A | 2000-08-09 Soyuz-Fregat | 2025 年目標式再入 [ESA-reported，外部任務資訊]；本庫無可用 TLE 歷史 |
| Salsa（Cluster 2 / FM6） | 26411 | 2000-041B | 2000-07-16 Soyuz-Fregat | 2024-09-08 首次目標式再入（南太平洋，飛機觀測）[ESA-reported，C4] |
| Samba（Cluster 3 / FM7） | 26410 | 2000-041A | 2000-07-16 Soyuz-Fregat | ESA 預報 2026-08-31 21:42 UTC ±10 min 再入（紐西蘭以北南太平洋）[ESA-reported，C2] |
| Tango（Cluster 4 / FM8） | 26464 | 2000-045B | 2000-08-09 Soyuz-Fregat | ESA 預報 2026-09-01 21:33 UTC ±10 min 再入（同區域，相隔 24 h）[ESA-reported，C2] |

> Cluster 為高橢圓極軌：原始約 19,000 × 119,000 km（高度）；本庫 2026-08-16 TLE 計算 Samba 遠地點地心距約 137,800 km（高度約 131,400 km，WGS-84 平均半徑 6,378.137 km）、傾角約 150° [TLE-derived]。再入預報為 ESA 以 2026-08-24 最後地面過境資料所作，本文件不重新驗證其不確定度。NORAD 與暱稱對應依 CelesTrak／Space-Track（26410=Samba、26464=Tango）；表中結局欄之外部任務資訊來源見文末「歷史事件來源」。

## ② Samba 與 Tango 現在在哪（3D，TLE 傳播位置）[TLE-derived]

兩顆衛星以本庫最後一筆 TLE 傳播之位置（Samba 最後 TLE 2026-08-16、Tango 2026-08-26；資料齡已逾 10 天，位置僅供示意）。依 2026-08-16 TLE 計算，Samba 軌道週期約 53.5 h、遠地點高度約 131,400 km、理想化近地點高度已低於 0 km（幾何軌道穿入參考橢球）——衛星大部分時間遠在月球距離三分之一以外，約每 2.2 天一次掠過大氣邊緣，再入即發生在其中一次掠過 [TLE-derived / interpretation]。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：[positions API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=ids&val=26410%2C26464)（mode=ids, val=26410,26464）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「② Samba 與 Tango 現在在哪（3D，TLE 傳播位置）[TLE-derived]」](https://rhynowu-atrdc-satdashboard.hf.space/story/cluster-samba-tango#positions)

## ③ 先行者 Salsa（26411）：2024 年的首次目標式再入

Salsa 的理想化近地點高度自 2024-01 的約 3,000 km 逐月下降，8 月降至 0 km 以下（幾何軌道穿入參考橢球，非實際飛行高度）；ESA 於 2024-09-08 完成目標式再入觀測 [ESA-reported，C4]。本系統 TLE 歷史止於 2024-09-08，時間軸播到最後即是資料終止之日 [TLE-derived]。在本系統的 TLE 平均要素時序中，SMA 相對穩定（約 72,500 km），偏心率與近地點幅角（ARGP）的變化主導了理想化近地點的估算——這是 TLE 模型下的趨勢描述；高橢圓軌道近地點的長期下降主要由月球與太陽引力攝動驅動，而非大氣阻力 [interpretation]。

*（互動區塊：逐日軌道要素時序（/orbit））*
- NORAD 26411：[/orbit](https://rhynowu-atrdc-satdashboard.hf.space/orbit?norad=26411&start=2024-01-01)（norad=26411, start=2024-01-01）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「③ 先行者 Salsa（26411）：2024 年的首次目標式再入」](https://rhynowu-atrdc-satdashboard.hf.space/story/cluster-samba-tango#salsa)

## ④ Samba（26410）：30 個月、理想化近地點高度從 14,600 km 降至 0 以下

依本庫 TLE 平均要素計算之理想化近地點高度：2024-01 約 14,597 km → 2025-01 約 7,661 km → 2026-01 約 2,022 km → 2026-07 約 70 km → 2026-08 已低於 0（幾何軌道穿入參考橢球；實際近地點在 100 km 上下）[TLE-derived]。每月約降 500 km、均勻而單調，符合「安全處置軌道」的設計思路：不必多耗燃料，讓天體力學把衛星送回大氣層 [interpretation]。2026-01-16→01-20 的相鄰 TLE 顯示 SMA 由 72,108.6 km 增至 72,154.1 km（+45.5 km）[TLE-derived]，與 ESA 所述 1 月 19～20 日軌道調整的時段一致，本系統將其標記為機動候選；但 TLE 擬合誤差、攝動模型與軌道決定更新亦可能造成類似訊號，單靠 TLE 不能獨立證明點火時間或真實 Δv [interpretation]。SMA 增加對應週期加長；ESA 稱此調整使再入時刻延後、落點向東 [ESA-reported，C1]。

*（互動區塊：逐日軌道要素時序（/orbit））*
- NORAD 26410：[/orbit](https://rhynowu-atrdc-satdashboard.hf.space/orbit?norad=26410&start=2024-01-01)（norad=26410, start=2024-01-01）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「④ Samba（26410）：30 個月、理想化近地點高度從 14,600 km 降至 0 以下」](https://rhynowu-atrdc-satdashboard.hf.space/story/cluster-samba-tango#samba)

## ⑤ Tango（26464）：同一條路，反向微調

Tango 的理想化近地點曲線與 Samba 幾乎重疊（2024-01 約 14,582 km → 2026-08 約 −101 km）[TLE-derived]，畢竟兩顆是同一批、同一軌道面部署的孿生星。2026-01-15→01-19 的相鄰 TLE 顯示 SMA 由 72,556.8 km 減至 72,551.6 km（−5.2 km）[TLE-derived]，方向與 Samba 相反、且幅度小；ESA 稱 Tango 的調整使再入時刻提前、落點向西，兩者再入因此相隔約 24 小時 [ESA-reported，C1]。−5.2 km 接近 TLE 平均要素的擬合噪聲量級，本系統僅將其列為弱候選，不作為點火證據 [interpretation]。

*（互動區塊：逐日軌道要素時序（/orbit））*
- NORAD 26464：[/orbit](https://rhynowu-atrdc-satdashboard.hf.space/orbit?norad=26464&start=2024-01-01)（norad=26464, start=2024-01-01）
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「⑤ Tango（26464）：同一條路，反向微調」](https://rhynowu-atrdc-satdashboard.hf.space/story/cluster-samba-tango#tango)

## ⑥ 2026-01-19／20 軌道調整在 TLE 上的簽章 [TLE-derived]

| 衛星 | 點火前 TLE | SMA 前 (km) | 點火後 TLE | SMA 後 (km) | ΔSMA | ESA 所述效果 [ESA-reported] |
|---|---|---|---|---|---|---|
| Samba 26410 | 2026-01-16 | 72,108.6 | 2026-01-20 | 72,154.1 | +45.5 km | 再入延後、落點向東 |
| Tango 26464 | 2026-01-15 | 72,556.8 | 2026-01-19 | 72,551.6 | −5.2 km | 再入提前、落點向西 |

> ΔSMA 為相鄰 TLE 平均要素差分，時段與 ESA 公告一致，故本系統標記為機動候選（Samba 超過 MEO/GEO 2 km 門檻；Tango −5.2 km 亦超過門檻但接近噪聲量級）。Cluster 為高偏心率軌道，本故事**不以**近圓軌道 Δv≈n·Δa/2 估算真實 Δv：真實點火量取決於點火時刻（近／遠地點速度差異極大）、推力方向與軌道狀態，需精密星曆或任務遙測求解；ESA 亦未公開 Δv。兩星傾角同時期由 148.2° 緩升至 148.5°，為月日攝動之自然演化 [interpretation]。

## ⑦ 再入時程（ESA 預報）與觀測任務 [ESA-reported]

| 項目 | Samba | Tango |
|---|---|---|
| ESA 預報再入時刻（UTC） | 2026-08-31 21:41:54 ±10 min | 2026-09-01 21:33:20 ±10 min |
| ESA 預報再入時刻（台北 UTC+8） | 2026-09-01 05:42 | 2026-09-02 05:33 |
| ESA 預報再入區域 | 紐西蘭以北南太平洋（實際落區與時間仍具不確定性） | 同一區域 |
| 預報依據 | 最後地面過境資料 2026-08-24 | 同左 |
| Space-Track 18 SDS 60 日衰減預報（日級，訊息 2026-08-05／07-29）[外部] | 2026-09-14（日級） | 2026-09-01（日級） |
| 本系統階段二數值 MC 中位（校準後）[TLE-derived] | 2026-08-31 19:35 UTC（−30.5°, −128.5°） | 2026-09-01 20:46 UTC（−30.4°, −145.3°） |
| 觀測方式 | 空中觀測團隊於禁航區邊緣以科學儀器觀測 | 同一團隊 24 h 後再次出勤（「fly out and back twice」） |
| 本庫最後 TLE | 2026-08-16 | 2026-08-26 |

> 以上時間與區域均為 ESA 2026-08-24 公開預報 [C2]，不是本系統以 TLE 獨立產生的操作級再入預報；本文件不重新驗證 ESA 的再入不確定性，也不將 TLE 傳播結果視為同等精度的再入預報。實際再入時間、位置與解體狀態以事後任務公告與觀測結果為準。2024 年 Salsa 再入已由飛機觀測 [C3]；ESA 文中未指明機型與機隊，本文件不擴大為確定的機體識別。 18 SDS 60 日衰減訊息為美軍第 18 太空防禦中隊之例行日級預報（Space-Track decay 類別），與 ESA 分鐘級預報精度不同；Samba 之 09-14 明顯晚於 ESA 08-31，顯示日級預報對高橢圓軌道末段之侷限。本系統數值估算詳見第 ⑧ 節。

## ⑧ 本系統再入落點估算 vs ESA 預報 [TLE-derived]

用公開 TLE 能把再入估到多準？本節以兩階段方法估算，並與 ESA 預報並列比較（不互相背書）。階段一：SGP4/SDP4 由最後 TLE 外推，逐圈找近地點掠過、首次低於 100 km 的那一圈即零階再入圈次，次衛星點為落點；此法能正確指出「哪一圈」，但時間誤差 1 h 即對應經度 15°。階段二：數值傳播（J2＋月日點質量＋NRLMSIS 2.1 阻力＋大氣共轉），以迎風面積 3.8–6.6 m²、密度尺度做 Monte Carlo，密度尺度先以 Salsa 2024-09-08 18:47Z 實際再入回測校準。最新 TLE 已由 Space-Track／CelesTrak 確認（Samba 08-16、Tango 08-26 之後無更新）；Space-Track 18 SDS 的 60 日衰減預報為日級精度，一併列出。所有本系統數值均為 TLE-derived 估算，非操作級再入預報。

| 衛星 | 最後 TLE | 階段一：SGP4 近地點掠過 [TLE-derived] | 階段二：數值 MC 中位 [TLE-derived] | ESA 預報 [ESA-reported] | 本系統 − ESA |
|---|---|---|---|---|---|
| Samba | 2026-08-16 | 2026-08-31T22:57:49Z（-29.91°, 175.17°） | 2026-08-31T19:35:32Z（-30.53°, -128.49°；5–95% 跨度 0.33 h） | 2026-08-31T21:41:54Z（±10 min，紐西蘭以北南太平洋） | S1 1.27 h／S2 -2.11 h |
| Tango | 2026-08-26 | 2026-09-01T22:03:44Z（-29.66°, -172.29°） | 2026-09-01T20:46:21Z（-30.44°, -145.34°；5–95% 跨度 0.24 h） | 2026-09-01T21:33:20Z（±10 min，紐西蘭以北南太平洋） | S1 0.51 h／S2 -0.78 h |

**Salsa 2024-09-08 18:47Z 回測（誤差＝本系統 − ESA 實際）**

| 前置時間 | 階段一誤差 | 階段二誤差 | MC 中位誤差 |
|---|---|---|---|
| 13 天前 TLE（2024-08-26） | -52.02 h | -1.31 h | -1.25 h（跨度 0.39 h） |
| 1 天前 TLE（2024-09-06） | 1.61 h | -0.03 h | 0.08 h（跨度 0.6 h） |

> 密度尺度校準：以 Salsa 回測取 NRLMSIS 密度尺度 ×0.3（掃描 [0.3, 0.5, 0.7, 1.0, 1.4]，平均誤差 {'0.3': -0.67, '0.5': -1.27, '0.7': -1.85, '1.0': -2.71, '1.4': -3.78}），套用於 Samba／Tango。

> Space-Track 18 SDS 衰減預報（日級）：26410：decay 2026-09-14 0:00:00（60day_msg，訊息 2026-07-15 17:53:16）；26464：decay 2026-09-01 0:00:00（60day_msg，訊息 2026-07-08 17:06:56）

> 產生時間 2026-08-28T13:52:27Z；方法：SGP4/SDP4 由最後 TLE 外推，逐圈近地點掠過（大地高極小），首次 ≤100 km 者為零階再入圈次；次衛星點=該時刻傳播位置／數值傳播：J2＋太陽／月球點質量（astropy 內建星曆）＋NRLMSIS 2.1 阻力（pymsis，CelesTrak 太空天氣）＋大氣共轉；DOP853；再入判定為大地高 ≤80 km；Monte Carlo 抽樣迎風面積 3.8–6.6 m²（Cd 2.2、質量 550 kg）與密度尺度 lnN(0,0.3)。初始軌道為公開 TLE（TEME 視為慣性系），無任務遙測；屬 TLE-derived 估算，非操作級再入預報
*（互動區塊：再入估算（SGP4 近地點掠過 ＋ 數值 Monte Carlo；TLE-derived））*
- 資料：[reentry API](https://rhynowu-atrdc-satdashboard.hf.space/api/story/reentry)
- 註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準
- 互動版：[開啟本節「⑧ 本系統再入落點估算 vs ESA 預報 [TLE-derived]」](https://rhynowu-atrdc-satdashboard.hf.space/story/cluster-samba-tango#reentry-estimate)

## 結語：TLE 能看見的，與看不見的

從公開 TLE 就能還原這段旅程的骨架：30 個月單調下降的理想化近地點、1 月那兩次方向相反的 SMA 微幅變化、以及 8 月中下旬戛然而止的資料 [TLE-derived]。看不見的是點火本身、真實 Δv、再入時的解體序列、碎片存活率與熱流——那正是 ESA 要用飛機去現場量測的東西 [ESA-reported]。對本系統而言，Cluster 也是一個提醒：高橢圓軌道的理想化近地點下降由月日攝動主導，與低軌大氣阻力衰減的形狀完全不同，機動偵測的阻力排除旗標（da_monotonic_decay）與近圓 Δv 近似都不能直接套用在這類軌道上 [interpretation]。

### 歷史事件來源（claim-to-source）

| claim_id | 內容 | 來源 | 來源支持範圍 |
|---|---|---|---|
| C1 | 2026-01-19／20 兩次小幅點火，Samba 再入點略向東、Tango 略向西，使再入相隔約 24 h | [ESA – Moving satellites to meet a plane for rare reentry data](https://www.esa.int/Space_Safety/Space_Debris/Moving_satellites_to_meet_a_plane_for_rare_reentry_data) | 點火日期、目的、方向；未公開 Δv |
| C2 | Samba 2026-08-31 21:41:54 UTC ±10 min、Tango 2026-09-01 21:33:20 UTC ±10 min 再入（紐西蘭以北南太平洋） | [ESA Rocket Science blog 2026-08-24（CEST 換算 UTC）](https://blogs.esa.int/rocketscience/2026/08/24/final-cluster-reentries-31-aug-1-sept-2026/) | 預報時刻、不確定度、區域、預報依據（最後地面過境 08-24） |
| C3 | 同一支空中觀測團隊於相隔 24 h 的兩趟出勤觀測兩次再入；2024 年 Salsa 再入已由飛機觀測 | [ESA 文章與 blog（同上）](https://www.esa.int/Space_Safety/Space_Debris/Moving_satellites_to_meet_a_plane_for_rare_reentry_data) | 任務安排；文中稱「fly out and back twice, 24 hours apart」，未指明機型 |
| C4 | Cluster 四星 NORAD／國際編號對應（26463/26411/26410/26464）、Salsa 2024-09-08 再入、Rumba 2025 年再入 | [CelesTrak SATCAT、Space-Track、ESA Salsa FAQ](https://www.esa.int/Science_Exploration/Space_Science/Cluster/Frequently_asked_questions_Cluster_s_Salsa_reentry) | 身分與編目、Salsa 再入日期；Rumba 日期為外部任務資訊 |
| C5 | 近地點／SMA／傾角演化、2026-01 相鄰 TLE 之 SMA 變化 +45.5 km（Samba）／−5.2 km（Tango）、軌道週期約 53.5 h | 本系統 TLE 資料庫（Space-Track GP）快照 2026-08-28 11:14 UTC；計算方法見資料口徑 | TLE 平均要素差分之趨勢；非任務遙測 |
| C6 | Space-Track 18 SDS 60 日衰減預報（Samba 2026-09-14、Tango 2026-09-01，日級） | [Space-Track decay 類別（需帳號）](https://www.space-track.org/) | 第三方日級預報；非 ESA 官方 |

> 標籤說明：[ESA-reported]＝外部公開資訊、[TLE-derived]＝本系統由 TLE 計算、[interpretation]＝作者解讀；本系統 TLE 推導結果與外部來源之數字分層標示，不互相背書。

---

*本文件由 SatDashboard StoryMap 匯出（tools/export_story_md.py）。軌道數據為公開 TLE 以 SGP4 傳播之結果，屬態勢展示等級；互動圖表請開啟線上版。*
