# SatDashboard — 太空態勢感知（SSA）儀表板

> 純自主新創研發之獨立成果，與任何機構無隸屬或委辦關係。

低軌衛星態勢感知視覺化平台：以 Flask + DuckDB + 向量化 SGP4 為核心，涵蓋全目錄衛星即時位置、接近事件（RPO）分析、台北覆蓋預報，以及 Starlink 台灣服務能力分析。所有頁面皆以公開 TLE 資料即時運算，不含任何機密或管制資訊。

## 🔗 線上展示

| 項目 | 連結 | 狀態 |
|---|---|---|
| SatDashboard 正式版 | https://rhynowu-atrdc-satdashboard.hf.space | ✅ 運行中 |
| SatDashboard 三語版（中/英/日） | https://rhynowu-atrdc-satdashboard-i18n.hf.space | ✅ 運行中 |
| Starlink 一生 StoryMap | https://rhynowu-atrdc-satdashboard-i18n.hf.space/story/starlink-lifecycle | ✅ 運行中 |
| 機動偵測儀表板 三語版 | https://rhynowu-maneuver-detection-i18n.hf.space | ✅ 運行中 |
| 機動偵測儀表板 正式版 | https://huggingface.co/spaces/RhynoWu/maneuver-detection | ⏸ 已暫停（進頁按 Restart 即可喚醒） |

## 🧩 核心功能

- **3D 地球儀**（`/`）：全目錄衛星即時位置（向量化 SGP4）、KD-tree 接近掃描、軌道弧
- **台北覆蓋分析**（`/taipei`）：各類別衛星過頂時間軸、仰角分布、SSN 站點覆蓋
- **Starlink 台灣服務能力分析**（`/starlink`）：
  - 可見衛星數量時間軸、覆蓋空窗偵測、天空密度圖、遮蔽模擬、RTT 傳播延遲下限估算
  - 世代／機型篩選（v1.0／v1.5／v2 Mini／V3，依發射日期啟發式分類）
  - 自動排除目前偵測為離軌中的衛星，並顯示排除顆數
  - 備援情境模擬：隨機折損 0～90% 衛星，觀察覆蓋退化程度
- **RPO 相對接近分析**（`/rpo`）：任兩衛星 3D 相對接近場景、3σ 碰撞機率橢球（Chan 2008 首階排序代理，非作業級 Pc）
- **軌道六參數調整器**（主頁工具列，介紹頁 `/orbit-tuner`）：互動滑桿調整古典軌道六要素（半長軸／離心率／傾角／升交點赤經／近地點幅角／真近點角），即時疊加繪製於 3D 地球並自動產生對應 STK Connect 指令；概念參考 AGI 官方 Orbit Tuner 教學插件
- **StoryMap 敘事頁**（`/story`）：GPS／北斗／Starlink／OneWeb／大陸 ISR・通訊星系即時位置、機動候選事件、台灣假想雷達站效益
  - **Starlink 一生**故事：發射→抬軌→運營→離軌全生命週期，含三個即時子頁：
    - 正在離軌的 Starlink（即時 SGP4 再入時刻／落點估算）
    - Starlink 顆數普查（即時比對 keeptrack.space 公開數字，說明統計口徑差異）
    - Starlink V3 佈署數量統計、V1.0→V3.0 世代規格比較（含除役現況）

## 🛠 技術架構

- **後端**：Flask（Blueprint 分模組）＋ DuckDB（TLE 時序資料庫）＋ SGP4（向量化 SatrecArray）
- **資料**：分年 DuckDB／Parquet 母資料庫，部署用「精簡版」（近 14 天 + 白名單完整歷史，約 51 MB）
- **前端**：CesiumJS 3D 地球儀、Chart.js 圖表、原生 JS 三語（中/英/日）i18n
- **部署**：HuggingFace Spaces（Docker SDK），GitHub Actions 排程自動更新資料庫
- **機動偵測**：獨立 Streamlit App，LightGBM 模型，資料以 Parquet + httpfs 遠端直查（免下載整庫）

## 📌 使用限制與現況說明

- 本工具基於公開 TLE 資料計算純幾何可用性，供技術評估與政策討論參考，非官方規格或商用服務保證
- Starlink 相關世代分類、V3 偵測、離軌判定皆為**啟發式方法**（依發射日期／軌道高度間接推估），非硬體序號級精確分類，頁面內皆有明確註記
- 《電信管理法》第 36 條修正案已於 2026 年 7 月 21 日三讀通過，取消外資持股上限、改由主管機關逐案審核；惟截至目前 Starlink 尚未取得正式核准、也還未在台灣商轉

---
*文件產生：2026-09-09（可依需要增減章節後貼上 Notion）*
