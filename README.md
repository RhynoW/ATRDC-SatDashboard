---
title: ATRDC SatDashboard
emoji: 🛰️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 5013
pinned: false
---

# ATRDC SatDashboard — 太空態勢儀表板（模組化重構版）

原 `scenario04-Cesium-advanced04.py`（3880 行單一 Flask 檔案）依
`scenario04_redesign_phases_20260702.md` **Phase 1（拆解與模組化）** 重構，
功能不變，另外先行落實 Phase 2 兩項低風險改善（分類規則與 SSN 站點外部化 + 熱重載）。

**新增功能**：全目錄 3D 態勢儀表板、台北在地覆蓋/過頂預報，以及
**RPO 3D 相對接近與碰撞機率視覺化**（`/rpo`）——對任一近距離配對重建兩顆衛星之
相對接近場景、可切換衛星視角、並以 3σ 三軸橢球視覺化碰撞機率（詳見下節）。

## 目錄結構

```
scenario-advanced01/
├── run.py                        # 啟動入口（Port 5013，與原版相同）
├── requirements.txt
├── scenario04/
│   ├── __init__.py               # create_app()：Flask app factory
│   ├── cache.py                  # 可選 Redis 快取後端
│   ├── config/
│   │   ├── settings.py           # 所有環境變數/路徑/常數的唯一來源（Phase 1.3）
│   │   ├── stations.py           # SSN 站點載入 + schema 驗證（Phase 2.4）
│   │   ├── ssn_stations.geojson  # 外部化的 SSN 地面觀測站清單（29 站）
│   │   └── classification_rules.yaml  # 外部化的國別/星座分類規則（Phase 2.3）
│   ├── ingestion/                # 資料存取層（Phase 1.2）
│   │   ├── db.py                 # resolve_db / tle_select_sql / upsert / db_info
│   │   ├── metadata.py           # sat_metadata.csv、classify_*（規則可 YAML 熱重載）
│   │   ├── index.py              # 衛星索引（現行 + 歷史時間軸）、統計
│   │   └── spacetrack.py         # Space-Track 可選整合與降級
│   ├── physics/                  # 純數學運算，無 Flask 依賴（Phase 1.2）
│   │   ├── coords.py             # GMST、ECI→LLH、ECI→仰角/方位
│   │   ├── propagate.py          # SGP4（向量化 SatrecArray 優先）
│   │   ├── coverage.py           # 台北覆蓋 / 過頂預報（含時間軸版本）
│   │   ├── conjunction.py        # KD-tree 接近事件 + Chan (2008) Pc
│   │   └── rpo.py                # RPO 場景資料：歷史 TLE → 相對幾何 + Pc；schema 相容（line/根數）
│   ├── services/
│   │   └── passes_service.py     # 過頂預報背景執行服務（封裝原模組層共享狀態）
│   ├── api/                      # Flask Blueprints：每組路由一個檔案
│   │   ├── pages.py              # /、/taipei、/cesium/*、/api/logo
│   │   ├── positions.py          # /api/stats、/api/positions、/api/search…
│   │   ├── passes.py             # /api/taipei_coverage(_at)、/api/taipei_passes(_at)
│   │   ├── conjunctions.py       # /api/conjunctions、/api/cdm/*、/api/decay/*…
│   │   ├── layers.py             # /api/layers/*、/api/globe_texture*、/api/textures
│   │   ├── rpo.py                # /rpo（RPO 3D 場景）、/api/rpo/<primary>/<secondary>
│   │   └── admin.py              # POST /api/admin/reload_cats（熱重載所有設定檔）
│   └── web/                      # 前後端分離（Phase 1.1）
│       ├── templates/            # globe.html、taipei.html、rpo3d.html（Jinja2）
│       └── static/               # css/*.css、js/globe.js、js/taipei.js、js/rpo3d.js
├── DB/                           # 本機打包精簡 DB（space_db_slim.duckdb，run.py 優先採用）
└── tests/                        # pytest（Phase 1.4）：純函式單元測試 + app 冒煙測試
```

## 啟動

```bash
cd scenario-advanced01
pip install -r requirements.txt
python run.py
# http://localhost:5013          3D 地球儀
# http://localhost:5013/taipei   台北 2D 覆蓋（時間軸）
# http://localhost:5013/rpo      RPO 3D 相對接近 + 碰撞機率
```

**資料庫優先序**：`run.py` 啟動時優先採用 `scenario-advanced01/DB/*.duckdb`
（本機打包之精簡 DB，取最新 mtime 者）；缺檔則退化為 `scenario04/DB/space_db.duckdb`
→ 專案根目錄的 full/slim。可用環境變數 `DB_PATH` 覆寫。
發布用精簡 DB **保留 line1/line2**（live 目錄傳播、conjunction、RPO 皆需），
全衛星保留最近 14 天 + 白名單（`58573,59884`）完整歷史，約 50 MB；
以 `download_tle_unified.bat`（`--keep-lines --recent-days 14 --whitelist 58573,59884`）重建。
其他資料檔（`data/`、`overpass_cats.yaml`、`.env` 等）讀取上層專案根目錄，
可用 `ATRDC_BASE_DIR` 指到其他位置（`/api/logo` 於缺 logo 檔時回 404，不影響功能）。

## RPO 3D 相對接近與碰撞機率視覺化（`/rpo`）

主頁功能列「🛰 RPO功能測試」進入。對任一「近距離配對」以歷史 TLE 重建兩顆衛星之相對接近 3D 場景：

- **配對選單**：清單來自 `/api/conjunctions`（KD-tree 瞬時鄰近篩選），選任一配對即切換該對視角；
  亦支援 `?primary=&secondary=` 深連結。
- **衛星視角切換**：點選配對後自動進入其中一顆衛星視角（相機跟隨），可用按鈕**來回切換**另一顆。
- **碰撞機率球（預設開啟、可關閉）**：兩顆衛星**各繪一個 3σ 三軸橢球**（徑向 R / 沿軌 T / 越軌 N
  獨立，`SIGMA_R/T/N_KM` 可調），各自配向至該衛星 RTN(LVLH) 座標系；顏色隨當下 Pc
  （綠 <1e-6 / 琥珀 / 紅 >1e-4）。
- **Pc 為 Chan (2008) 各向同性首階「排序代理」**，非作業級碰撞機率，HUD 明確標註界定。
- 預設展示案例：神龍太空梭 **58573 × 59884**（2024 在軌釋放與 6 月再接近至 ~1.2 km）。

資料由 `physics/rpo.py` 產生，並落地為 `scenario04/DB/rpo_scene_<p>_<s>.json` 快取（與 archive 鎖狀態解耦）。
相容兩種 `raw_tle_archive` schema：含 `line1/line2` 者用 `twoline2rv`；僅平均根數者以 `sgp4init` 重建。

## 測試

```bash
cd scenario-advanced01
pytest tests/ -v
```

## 使用者自訂資料（放檔案即生效）

| 目錄 | 內容 | 格式說明 |
|---|---|---|
| `scenario04/user_defined_TLE/` | 自訂 TLE（覆蓋或新增衛星） | 2 行 / 3 行混用，`#` 註解；見 [README](scenario04/user_defined_TLE/README.md) |
| `scenario04/user_defined_SaTCatalogue/` | 衛星目錄 CSV（中文名稱/國家/用途…） | UTF-8(-BOM)；見 [README](scenario04/user_defined_SaTCatalogue/README.md) |
| `scenario04/user_defined_tracking_NORAD/` | NORAD 監測清單 CSV | 別名/優先度/顏色/enabled；見 [README](scenario04/user_defined_tracking_NORAD/README.md) |
| `scenario04/geojson/` | 自訂向量圖層（*.geojson） | 每檔一個圖層開關；見 [README](scenario04/geojson/README.md) |

- 主頁上方功能列「🎯 NORAD 監測」載入監測清單，**多顆衛星同時追蹤**：
  地球上即時標示 + 軌道弧，每 15 秒自動更新；面板可勾選、也可手動輸入
  NORAD ID 臨時加入（上限 50 顆）。
- 範例：`user_tle01.tle`（含合成測試衛星 NORAD 99001、ISS 25544、福衛五號 42920）、
  `user_catalogue01.csv`、`tracking01.csv`。
- API：`GET /api/user/tles`、`GET /api/user/catalogue`、
  `GET /api/tracking/list`、`GET /api/tracking/positions?ids=…`。
- 檔案變更後索引最多 10 分鐘自動重建，或呼叫 reload API 立即生效（見下節）。

## 熱重載外部化設定（不需重啟服務）

```bash
curl -X POST http://localhost:5013/api/admin/reload_cats
```

同時重載三個檔案：`overpass_cats.yaml`（過頂類別）、
`scenario04/config/classification_rules.yaml`（國別/星座分類規則）、
`scenario04/config/ssn_stations.geojson`（SSN 站點清單）。

## 與原版差異（行為層面）

- 前端改由 Jinja2 模板 + `/static/` 靜態檔提供；改 JS/CSS 不需重啟 Flask。
- Cesium ION token 由 `render_template(..., cesium_token=...)` 注入
  `window.CESIUM_ION_TOKEN`，不再做字串 `replace()`。
- `/api/admin/reload_cats` 除了 overpass 類別，也一併重載分類規則與 SSN 站點。
- 其餘路由、參數、回應格式、快取 TTL 行為與原版一致。

## 後續（見 redesign phases 文件）

- Phase 2：獨立 ingestion job（排程抓 TLE、預建索引）、Redis Streams、schema migration
- Phase 3：WebSocket 事件推送、SceneSnapshot 場景抽象層、告警規則引擎
- Phase 4：實測感測器資料接入、track fusion、STK 匯出
