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
| **RPO 相對接近** | `/rpo`（案例總覽：`/rpo/cases`） | 近距離配對兩衛星 3D 相對接近、碰撞機率球（3σ R/T/N 橢球）、衛星視角切換 |
| **StoryMap 敘事頁** | `/story` | StoryMaps 式整合展示：GPS／北斗／Starlink／OneWeb／大陸 ISR・通訊星系即時位置、2026 機動候選事件、台灣假想雷達站效益、日本 2026 發射誌 |
| **軌道六參數調整器**（Orbit Tuner） | 主頁工具列面板（介紹頁：`/orbit-tuner`） | 互動滑桿調整古典軌道六要素（a/e/i/Ω/ω/ν），即時疊加繪製於 3D 地球並產生對應 STK Connect 指令；概念參考 AGI Orbit Tuner |
| 軌道要素歷史 | `/orbit` | 單顆衛星 SMA／傾角／RAAN／ARGP 歷史（Spiral Polar + SMA 圓形圖） |
| Starlink 顆數普查 | `/starlink-census` | 本系統 vs keeptrack.space 公開數字比對 |
| Starlink 離軌名單 | `/starlink-deorbit` | 即時離軌中的 Starlink（SGP4 再入時刻／落點估算） |
| Starlink V3 佈署統計 | `/starlink-v3` | V1.0→V3.0 世代規格與佈署數量比較 |
| 全球海纜態勢 | `/cable` | TeleGeography 海纜圖資＋台灣斷纜事件互動地圖 |
| **AIS 船舶追蹤** | 地球儀左側「向量圖層」→「🚢 AIS 船舶」 | 兩個來源以 MMSI 合併（較新回報為準，點選可看來源）：① aisstream.io（預設只訂閱台灣與東亞，`AIS_BBOX` 可改）；② 航港局「臺灣海域船舶動態資訊系統」GeoJSON（台灣周邊約 9,000 艘、每 60 秒輪詢；非正式開放資料、HF 等海外主機連不到，**線上版預設關閉**，`AIS_MPB=1` 啟用）。伺服器端連 aisstream.io（WebSocket＋permessage-deflate），船隻依航向／船型顯示、點選看船名／航速／目的地與 6 小時航跡、只顯示航行中；勾選才連線、閒置 10 分鐘斷線。需環境變數 `AISSTREAM_API_KEY`（HF Space 設為 Secret；本機也可放 `aisstream_key.txt`，已列入 .gitignore／.dockerignore）。API：`/api/ais/status`、`/api/ais/ships?w=&s=&e=&n=&moving=1&src=aisstream,mpb`、`/api/ais/ship/<mmsi>`。AIS 為自主回報、僅近岸有收訊 |
| **衛星管理** | 頂端「🗂 衛星管理」（`/satadmin`） | 依 NORAD ID 或名稱查詢與更名（保留 Space-Track 原名）、列出重複名稱（OBJECT A、TBA 等，可排除碎片）、多層群組（感測器類型、偵照角度沿用上層）、CSV 匯入匯出與 JSON 備份。「建議名稱」辨識 OBJECT A/B/C：official（Space-Track 已正式命名）＞ inferred（GCAT 逐顆判讀或單一酬載發射）＞ group_inferred（只確認發射群組，命名為「XX 群組衛星（推論）」、不指派 A/B/C 個體）；conflict 標示來源矛盾或類型不一致（例如 Space-Track 已判定為碎片）。每筆附發射群組／候選任務／個體對應分層判讀與來源鏈，勾選後才套用，更名紀錄保存名稱狀態、可信度、證據連結（可加 SeeSat-L 等）與複核狀態；任務類型與用途不自動歸因。GCAT（McDowell, planet4589.org, CC BY 4.0）與 SATCAT 由每日管線寫入 DB（`/api/satadmin/suggest`）。「SATCAT 異動」頁籤列出每日異動：以 Space-Track `satcat_change` 為準（名稱、國際編號、國家、發射日、DECAY 的異動與異動時間，近 90 天），再以每日 SATCAT 快照比對補上物體類型變更；分為正式命名（OBJECT x → 正式名稱）、更名、再入、再入日期修正、新編目、類型變更、其他欄位，可只看已更名或群組內的衛星；已套用推論名稱而 Space-Track 已正式命名者標「可升級」，TLE 目錄仍是 OBJECT x 也會註明；可匯出 CSV。（`/api/satadmin/changes`）**線上版的更名與群組只存在使用者自己的瀏覽器（localStorage），不上傳伺服器**；帳號權限與伺服器端保存只在 Rust 單機版。「衛星失能」以 SATCAT（CelesTrak，12 小時內只抓一次）列出群組內已再入（DECAY 日期、在軌天數）、TLE 停更、近期再入；「星歷資料」顯示最後更新時間與目錄曆元分布，重新抓取 CelesTrak 需 `ADMIN_TOKEN`（每 2 小時限一次）。API：`/api/satadmin/lookup`、`/api/satadmin/duplicates`、`POST /api/satadmin/status`、`/api/satadmin/recent_decay`、`/api/ephemeris/status`、`POST /api/ephemeris/refresh` |
| **觀測與身分驗證** | 衛星管理頁頂端「🔭 觀測與身分驗證」（`/obs`） | 四種紀錄：地面 POI（觀測站與關注地點）、觀測紀錄（RA/Dec J2000 或方位／仰角）、軌道解（TLE，支援 Alpha-5 編號）、身分主張（名稱＋來源類型＋可信度），每筆附複核狀態（未複核／已接受／有爭議／已否決），內容修改後自動退回未複核；以 CSV 匯入匯出（依標題列自動判斷種類）。交叉驗證：① 觀測殘差——目錄 TLE 與使用者軌道解 SGP4 傳播到觀測時間，比較觀測站看到的方向（IAU 1976 歲差轉 J2000，與 skyfield 差 < 0.004°），≤ 0.5° 符合、≤ 2° 勉強符合，並比對同次發射所有物體找最符合者；② 軌道比對——軌道解與目錄 TLE 在曆元 ±12 小時的最大位置差，≤ 10 km 一致、≤ 50 km 接近，並找同次發射中軌道最接近者（協助判斷 OBJECT A/B/C）；③ 身分主張與 Space-Track 正式名稱、其他主張比對。每顆衛星輸出 identity／orbit／observation 狀態與分層判讀（發射群組→候選任務→個體）；任務類型與能力一律為未知，不自動歸因。業餘觀測屬中等可信度來源，需人工複核。**線上版的觀測資料只存在使用者瀏覽器（localStorage）**，交叉驗證時送到伺服器計算後即丟棄；帳號權限、多人共用與複核紀錄在 Rust 單機版。與單機版同一套判定（測試資料逐筆一致）。API：`POST /api/obs/check` |
| **過頂預報** | 頂端「📅 過頂預報」（`/forecast`） | 多中心點（可設半徑）、最長 62 天，輸出過頂清單／日出日落／歷史誤差三個 CSV。過頂＝中心點仰角 ≥ 門檻；可偵照＝離天底角 ≤ 偵照角度（群組設定或預設 15°／30°）；每筆附太陽仰角、日照與當日日出日落，可只保留光學衛星的日照時段；歷史誤差以資料庫中同一顆衛星的 TLE 兩兩比對估算提前 1–30 天的位置與時間誤差，並標在每筆過頂。與 Rust 單機版同一套判定（31 天 3 顆 2 點 678 筆逐筆一致）。線上版一次最多 300 顆（`FORECAST_MAX_SATS`） |
| **碎片連鎖**（太空小遊戲） | `/game` | 衛星解體（NASA 標準解體模型）與凱斯勒連鎖蒙地卡羅：任務、緩解決策卡與對照組、風險儀表板；中／EN／日。計算核心與 Rust 單機版共用（`rust/debris_cli/`，以 `tools/sync_debris.py` 自 RUST_SatDashboard 同步，Docker 建置時編譯） |

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

### RPO 3D 相對接近與碰撞機率（已實作）

主頁功能列「🛰 RPO功能測試」進入 `/rpo`。對任一「近距離配對」以歷史 TLE 重建兩衛星之相對接近 3D 場景：

- **配對選單**：清單來自 `/api/conjunctions`；選任一配對即切換，亦支援 `?primary=&secondary=` 深連結
- **衛星視角切換**：點選配對後自動進入其中一顆衛星視角，可按鈕**來回切換**另一顆
- **碰撞機率球（預設開啟、可關閉）**：兩衛星**各繪 3σ 三軸橢球**（徑向 R / 沿軌 T / 越軌 N 獨立，
  `SIGMA_R/T/N_KM` 可調），配向至各自 RTN(LVLH) 座標系；顏色隨當下 Pc（綠<1e-6 / 琥珀 / 紅>1e-4）
- **Pc 為 Chan (2008) 各向同性首階排序代理**，非作業級碰撞機率，HUD 明確標註
- 預設展示：神龍太空梭 **58573 × 59884**（2024 在軌釋放與 6 月再接近至 ~1.2 km）
- 資料相容兩種 `raw_tle_archive` schema：含 `line1/line2` 用 `twoline2rv`；僅平均根數以 `sgp4init` 重建

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
│   │   ├── starlink_analysis.py    # Starlink 可見性 + 遮蔽模擬引擎
│   │   └── rpo.py                  # RPO 場景：歷史 TLE → 相對幾何 + Pc（schema 相容 line/根數）
│   ├── services/
│   │   └── passes_service.py       # 過頂預報背景執行服務
│   ├── api/                        # Flask Blueprints
│   │   ├── pages.py                # /、/taipei、/starlink、/cesium/*
│   │   ├── positions.py            # /api/stats、/api/positions、/api/search
│   │   ├── passes.py               # /api/taipei_coverage、/api/taipei_passes
│   │   ├── conjunctions.py         # /api/conjunctions、/api/cdm/*
│   │   ├── layers.py               # /api/layers/*、/api/globe_texture
│   │   ├── starlink.py             # /api/starlink/visibility、/api/starlink/obstruction
│   │   ├── rpo.py                  # /rpo、/api/rpo/<primary>/<secondary>
│   │   └── admin.py                # POST /api/admin/reload_cats
│   ├── DB/
│   │   ├── space_db_slim.duckdb    # 精簡 TLE 資料庫（含 line1/line2，約 51 MB；近14天+白名單完整歷史）
│   │   └── sat_metadata.csv        # 衛星元資料補充
│   └── web/
│       ├── templates/              # globe.html、taipei.html、starlink.html、rpo3d.html
│       └── static/                 # CSS / JS（globe、taipei、starlink、rpo3d）
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
| RPO 相對接近 | http://localhost:5013/rpo |
| 軌道六參數調整器（介紹頁） | http://localhost:5013/orbit-tuner |

**資料庫**：`run.py` 啟動時優先採用 `scenario-advanced01/DB/*.duckdb`（若存在），否則讀取
`scenario04/DB/space_db_slim.duckdb`（約 51 MB，已附於本倉庫；**含 line1/line2**，
全衛星保留最近 14 天 + 白名單 `58573,59884` 完整歷史供 RPO 展示）。
可用環境變數 `DB_PATH` 覆寫路徑；其他資源路徑用 `ATRDC_BASE_DIR` 指定。

## API 端點

### 衛星位置

| Method | Endpoint | 說明 |
|--------|----------|------|
| GET | `/api/stats` | 衛星統計摘要（各類別數量；`?payload_only=1` 排除碎片/火箭體） |
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

### RPO 相對接近

| Method | Endpoint | 說明 |
|--------|----------|------|
| GET | `/api/rpo/<primary>/<secondary>` | 兩衛星相對接近場景（orbit/series/meta/summary + Chan Pc） |

### StoryMap 敘事頁

| 端點 | 說明 |
|------|------|
| `GET /api/story/list` / `GET /api/story/<sid>` | 故事清單／單一故事 JSON（`scenario04/config/stories/*.json`） |
| `GET /api/story/groups` | 六個展示群組（gps／beidou／starlink／oneweb／prc_isr／prc_comm）數量 |
| `GET /api/story/positions?mode=group&val=prc_isr` | 群組即時位置（亦支援 all／country／constellation／purpose／ids） |
| `GET /api/story/group_stats?group=gps` | 高度／傾角分佈、發射年份、代表衛星 |
| `GET /api/story/isr_resolution?group=prc_isr` | 感測器類型與光學解析度級別（型號級公開推估） |
| `GET /api/story/maneuvers` | 2026 機動候選事件（`tools/build_maneuvers_2026.py` 預算） |
| `GET /api/story/radar_eval?group=prc_isr&n=30` / `GET /api/story/track?norad=` | 台灣假想雷達站效益評估／單星過頂 az/el |
| `GET /api/story/provenance` | 資料口徑：資料來源、TLE epoch 範圍／資料齡、SGP4 版本、座標系、Pc σ 假設、機動候選方法（故事頁「資料口徑」列與 Markdown 匯出共用） |

StoryMap 所需 TLE（六群組 + 故事明列 NORAD + 使用者目錄）以
`python tools/merge_storymap_tle.py [--target scenario04/DB/space_db_slim.duckdb]`
自父專案全量 `space_db.duckdb` 差異併入打包 DB（預設目標 `scenario-advanced01/DB/`，
每日管線重建 slim DB 後重跑即可）。

故事 JSON 以 `config/stories/schema.json` 驗證（載入時不合者略過並記 WARNING；`tests/test_story_schema.py` 全檢）。
`python tools/export_story_md.py <story-id>|--all` 匯出 Markdown 至 `docs/`（含資料口徑段、互動區塊連結）。
`/rpo` 另提供距離–時間與 R/T/N 分量–時間圖（`orbit[].rtn`，secondary 相對 primary 之 LVLH 分量）。

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
