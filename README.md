---
title: ATRDC SatDashboard
emoji: 🛰️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 5013
pinned: false
---

# ATRDC SatDashboard — 太空態勢儀表板

基於 Flask + CesiumJS 的離線太空態勢視覺化平台，涵蓋全目錄衛星即時位置、
台北覆蓋分析、接近事件偵測，以及 **Starlink 台灣服務能力分析**。

## 功能概覽

| 頁面 | 路由 | 說明 |
|------|------|------|
| 3D 地球儀 | `/` | 全目錄衛星即時位置（向量化 SGP4）、KD-tree 接近掃描、軌道弧 |
| 台北覆蓋分析 | `/taipei` | 各類別衛星過頂時間軸、仰角分布、SSN 站點覆蓋 |
| **Starlink 分析** | `/starlink` | 幾何可用性時間軸、RTT 下限估算、天空密度圖、遮蔽模擬 |

### 衛星類別（overpass_cats.yaml）

| 代碼 | 說明 | 顏色 |
|------|------|------|
| US_EO | 美國商用光學（WorldView / SkySat / Pelican） | 藍 |
| CN_COMM | 中國商用光學（SuperView / 吉林 / 高分） | 橙 |
| CN_MIL | 中國軍用偵察（遙感 Yaogan / Jianbing） | 紅 |
| TW_TASA | 台灣 TASA（Formosat-5/7/8 / COSMIC-2） | 青 |
| STARLINK | Starlink 星鏈（Gen1 / Gen2 / V2 Mini） | 紫 |

### Starlink 台灣服務能力分析（已實作）

- **可見衛星數量時間軸**：24 小時 × 15 分鐘步長，仰角遮蔽可調（預設 25°）
- **覆蓋空窗偵測**：標出幾何可用數 < 1 的斷線時段
- **天空密度圖**：極座標顯示衛星分布密度，輔助天線安裝方位決策
- **遮蔽模擬**：點選天空格子模擬地形/建物遮蔽，即時計算可用率衝擊
- **RTT 傳播延遲下限**：由斜距估算光速往返時間（台灣典型 4–9 ms）
- 7 個城市預設：台北 · 台中 · 台南 · 高雄 · 花蓮 · 金門 · 澎湖

## 目錄結構

```
scenario04-advanced01/
├── run.py                          # 啟動入口（Port 5013）
├── requirements.txt
├── overpass_cats.yaml              # 衛星類別定義（熱重載）
├── scenario04/
│   ├── __init__.py                 # Flask app factory：create_app()
│   ├── cache.py                    # 可選 Redis 快取後端
│   ├── config/
│   │   ├── settings.py             # 所有環境變數/路徑/常數的唯一來源
│   │   ├── stations.py             # SSN 站點載入 + schema 驗證
│   │   ├── ssn_stations.geojson    # 外部化 SSN 地面觀測站（29 站）
│   │   └── classification_rules.yaml  # 國別/星座分類規則（可熱重載）
│   ├── ingestion/                  # 資料存取層
│   │   ├── db.py                   # resolve_db / tle_select_sql / upsert
│   │   ├── metadata.py             # 衛星元資料 + 分類
│   │   ├── index.py                # 衛星索引（現行 + 歷史）、統計
│   │   └── spacetrack.py           # Space-Track 整合（可選）
│   ├── physics/                    # 純數學運算，無 Flask 依賴
│   │   ├── coords.py               # GMST、ECI→LLH、ECI→仰角/方位
│   │   ├── propagate.py            # SGP4（向量化 SatrecArray）
│   │   ├── coverage.py             # 台北覆蓋 / 過頂預報
│   │   ├── conjunction.py          # KD-tree 接近事件 + Chan (2008) Pc
│   │   ├── propagator_cache.py     # TLE 傳播快取層
│   │   └── starlink_analysis.py    # Starlink 可見性 + 遮蔽模擬引擎
│   ├── services/
│   │   └── passes_service.py       # 過頂預報背景執行服務
│   ├── api/                        # Flask Blueprints
│   │   ├── pages.py                # /、/taipei、/starlink、/cesium/*
│   │   ├── positions.py            # /api/stats、/api/positions、/api/search
│   │   ├── passes.py               # /api/taipei_coverage、/api/taipei_passes
│   │   ├── conjunctions.py         # /api/conjunctions、/api/cdm/*
│   │   ├── layers.py               # /api/layers/*、/api/globe_texture
│   │   ├── starlink.py             # /api/starlink/visibility、/api/starlink/obstruction
│   │   └── admin.py                # POST /api/admin/reload_cats
│   ├── DB/
│   │   ├── space_db_slim.duckdb    # 精簡 TLE 資料庫（含 line1/line2，59 MB）
│   │   └── sat_metadata.csv        # 衛星元資料補充
│   └── web/
│       ├── templates/              # globe.html、taipei.html、starlink.html
│       └── static/                 # CSS / JS（globe、taipei、starlink）
└── tests/                          # pytest 單元測試 + 冒煙測試
```

## 快速開始

```bash
# 1. 安裝套件
pip install -r requirements.txt

# 2. 設定環境變數
copy .env.example .env   # Windows
# cp .env.example .env   # Linux/macOS
# 填入 SPACE_TRACK_IDENTITY / SPACE_TRACK_PASSWORD（可選，供 TLE 下載使用）

# 3. 啟動
python run.py
```

| 頁面 | URL |
|------|-----|
| 3D 地球儀 | http://localhost:5013 |
| 台北覆蓋分析 | http://localhost:5013/taipei |
| Starlink 分析 | http://localhost:5013/starlink |

**資料庫**：預設讀取 `scenario04/DB/space_db_slim.duckdb`（59 MB，已附於本倉庫）。
可用環境變數 `DB_PATH` 覆寫路徑；其他資源路徑用 `ATRDC_BASE_DIR` 指定。

## API 端點

### 衛星位置

| Method | Endpoint | 說明 |
|--------|----------|------|
| GET | `/api/stats` | 衛星統計摘要（各類別數量） |
| GET | `/api/positions` | 即時位置（ECI → LLH，含過濾） |
| GET | `/api/search` | NORAD ID / 名稱搜尋 |

### 台北覆蓋

| Method | Endpoint | 說明 |
|--------|----------|------|
| GET | `/api/taipei_coverage` | 各類別覆蓋時間軸（24 h）|
| GET | `/api/taipei_passes` | 過頂清單（起始、最大仰角、方位） |

### Starlink 分析

| Method | Endpoint | 說明 |
|--------|----------|------|
| GET | `/api/starlink/visibility` | 可見性時間軸 + RTT 下限（202 非同步） |
| POST | `/api/starlink/obstruction` | 遮蔽模擬（套用遮蔽格子，同步回傳） |

### 系統

| Method | Endpoint | 說明 |
|--------|----------|------|
| POST | `/api/admin/reload_cats` | 熱重載 overpass_cats.yaml + 分類規則 + SSN 站點 |

## Docker 部署

```bash
docker build -t atrdc-satdashboard .
docker run -p 5013:5013 -e DB_PATH=/app/scenario04/DB/space_db_slim.duckdb atrdc-satdashboard
```

## 使用者自訂資料

| 目錄 | 說明 |
|------|------|
| `scenario04/user_defined_TLE/` | 自訂 TLE（2/3 行混用）|
| `scenario04/user_defined_SaTCatalogue/` | 衛星目錄 CSV |
| `scenario04/user_defined_tracking_NORAD/` | NORAD 監測清單 |
| `scenario04/geojson/` | 自訂向量圖層 |

## 測試

```bash
pytest tests/ -v
```

## 注意事項

- **Starlink 台灣現況**：截至 2026 年 7 月，Starlink 尚未在台灣正式商轉，
  主要受限於《電信管理法》第 36 條外資持股上限。本工具基於公開 TLE 計算純幾何可用性，
  供技術評估參考。
- 精簡 DB 每日由 `prc_maneuver/build_slim_db.py --keep-lines --days 14` 重建，
  去重策略：每顆衛星每日保留最新一筆 TLE。
