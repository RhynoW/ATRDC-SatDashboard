# geojson — 使用者自訂向量圖層

把 `*.geojson`（WGS84 經緯度，FeatureCollection）放進本目錄，
主頁左側「向量圖層」區塊會自動出現對應的開關並載入顯示。

## 行為

- 每個檔案一個圖層開關，自動輪流配色；feature 自帶 `color` 屬性（`#RRGGBB`）時優先使用
- Polygon/MultiPolygon → 畫貼地外框線（不填色）；LineString → 貼地線；Point → 圓點
- 預設開啟；**大型圖層**（> 3,000 features 或 > 4 MB）預設關閉，勾選時才載入
- 解析失敗的檔案會列出但停用（tooltip 顯示錯誤），不影響其他圖層
- 檔案變更後重新整理網頁即生效（伺服器端 5 分鐘快取）

## 內附圖層

| 檔案 | 內容 | 來源 |
|---|---|---|
| `Taiwan-admin-ploygon.geojson` | 台灣行政區界 22 區（精細） | 使用者提供 |
| `submarine-cables.geojson` | 全球海底電纜 714 條（官方配色） | submarinecablemap.com API v3（TeleGeography，2026-07-02 下載） |
| `openflights-airports.geojson` | 全球機場 7,698 點（大型，預設關閉） | openflights-geojson npm 套件產出（OpenFlights.org 資料） |

## 台灣行政區界（精細）

`Taiwan-admin-ploygon.geojson` 為精細台灣行政界。**此檔存在時**，
「全球國界」圖層（Natural Earth 110m）會自動剔除粗糙的台灣輪廓，
改由本檔疊加顯示；刪除此檔則恢復原始粗糙輪廓。

## API

- `GET /api/layers/user_geojson` — 圖層檔清單（含 feature 數 / 錯誤訊息）
- `GET /api/layers/user_geojson/<檔名>` — 取得單一圖層內容
