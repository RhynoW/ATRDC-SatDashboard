# 2026年6月至8月 日本衛星發射觀測誌

**從全球星空到單一衛星 — 六個尺度的軌道觀測**

由全球衛星分布逐步聚焦：日本衛星 → QZSS 星羣 → 本季新發射衛星 → 單星軌道動態 → 近距離接近操作（RPO）。所有視圖皆為即時軌道資料。

> 更新：2026-08-26　|　互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026

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

## 導言

2026 年 6 月 12 日，H3-30S（F6）自種子島升空部署 6 顆衛星；8 月 10 日 H3-22S（F9）再送出準天頂衛星 QZS-7。本故事從全球視角開始，一路聚焦到單一衛星的逐日軌道動態。（編目註記：PETREL 為 NORAD 69503、STARS-X 母子星以單一物體 69502 編目；69501 實為前一日發射之 STARLINK-37843。）

## ① 全球現有衛星位置分布（3D）

本系統目錄中全部可傳播物體的即時三維位置（SGP4 向量化傳播、自動旋轉視角、依實際高度立體呈現）。貼近地表的低軌殼層與遠處的地球同步環清晰可辨。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=all
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026

## ② 日本全部衛星位置分布（3D）

目錄中隸屬日本的全部衛星即時三維位置：低軌遙測／科學衛星群，以及外圈的通訊與導航衛星。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=country&val=%E6%97%A5%E6%9C%AC
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026

## ③ QZSS 準天頂星羣最新 TLE 傳播位置（3D）

準天頂衛星系統（QZSS／引路）全星羣：QZS-1／1R／2／3／4／6 與本季新發射的 QZS-7，分布於約 6.6 倍地球半徑的傾斜地球同步軌道殼層。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=constellation&val=QZSS
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026

## ④ 2026 年 6–8 月發射清單

| 發射日期 | 衛星名稱 | NORAD ID | 發射載具 | 發射地點 | 備註 |
|---|---|---|---|---|---|
| 2026-06-12 | PETREL | 69503 | H3-30S (F6) | 種子島 | 東京科學大學海洋與地球觀測衛星 |
| 2026-06-12 | STARS-X | 69502 | H3-30S (F6) | 種子島 | 靜岡大學太空纜繩實驗衛星（母子星單一編目） |
| 2026-06-12 | BRO-22 | 69504 | H3-30S (F6) | 種子島 | 法國 UnseenLabs 頻譜監測衛星 |
| 2026-06-12 | VERTECS | 69506 | H3-30S (F6) | 種子島 | 九州工業大學宇宙背景輻射觀測衛星 |
| 2026-06-12 | HORN-L | 69505 | H3-30S (F6) | 種子島 | Bull Space 太空碎片減緩技術驗證 |
| 2026-06-12 | HORN-R | 69507 | H3-30S (F6) | 種子島 | Bull Space 太空碎片減緩技術驗證 |
| 2026-08-10 | QZS-7（引路 7 號） | 100270 | H3-22S (F9) | 種子島 | 準天頂定位衛星，搭載美國太空軍 SĀCHI SDA 酬載 |

> NORAD 對應依 Space-Track satcat（2026-08-26 查核）。

## ⑤ 新發射衛星最新 TLE 傳播位置（依發射順序輪播）

依發射順序逐顆聚焦：6 月批次的太陽同步軌道六星（約 530–570 km），以及 8 月的 QZS-7（約 32,800 km 準天頂部署軌道）。點按任一顆可手動切換。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=ids&val=69503%2C69502%2C69504%2C69506%2C69505%2C69507%2C100270
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026

## ⑥ PETREL 軌道時間動態（發射日起）

單星深入：PETREL（NORAD 69503）自 2026-06-12 發射以來的逐日軌道要素——SMA 圓形時間圖與傾角／RAAN／ARGP 三張 Spiral Polar 排成一列，載入後自動播放時間軸，白圈標記當日位置。

*（互動區塊：逐日軌道要素時序（/orbit））*
- NORAD 69503：https://rhynowu-atrdc-satdashboard.hf.space/orbit?norad=69503&start=2026-06-12
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026#sat-petrel

## ⑦ RPO 近距離接近操作展示

本系統的 RPO（Rendezvous and Proximity Operations）分析場景：實登錄案例之雙星相對接近 3D 視圖與最接近距離／碰撞機率（Chan Pc）計算。

*（互動區塊：內嵌頁面）*
- 頁面：https://rhynowu-atrdc-satdashboard.hf.space/rpo
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026

## 結語

從三萬顆物體的全球分布，到一顆衛星的逐日軌道，再到兩顆衛星的相對運動——同一份 TLE 資料，支撐六個尺度的太空態勢感知。

---

*本文件由 SatDashboard StoryMap 匯出（tools/export_story_md.py）。軌道數據為公開 TLE 以 SGP4 傳播之結果，屬態勢展示等級；互動圖表請開啟線上版。*
