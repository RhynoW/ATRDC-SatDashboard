# user_defined_tracking_NORAD — NORAD 監測清單

把 `*.csv` 檔放進本目錄，網頁上方功能列的「🎯 NORAD 監測」即會載入清單，
可**同時追蹤多顆衛星**（地球上即時標示位置 + 軌道弧，每 15 秒自動更新）。
支援繁體中文；編碼 UTF-8 或 UTF-8 BOM。

## 欄位定義

| 欄位 | 必填 | 說明 |
|---|---|---|
| `norad_id` | ✔ | NORAD 編號（整數） |
| `alias` | | 顯示別名（可中文；空白時用衛星名稱） |
| `priority` | | 優先度：`高`/`中`/`低`（或 high/medium/low；預設 中，面板依此排序） |
| `color` | | 顯示顏色 `#RRGGBB`（空白時自動配色） |
| `enabled` | | `Y`/`N`（預設 Y；N 表示列在面板但不自動追蹤） |
| `notes` | | 備註（追蹤原因等） |

範例見 [tracking01.csv](tracking01.csv)。

## 行為

- 多檔案合併，同一 NORAD ID 以檔名排序靠後者為準
- 面板可勾選/取消個別衛星，也可手動輸入 NORAD ID 臨時加入（不寫回 CSV）
- 同時追蹤上限 50 顆
- API：`GET /api/tracking/list`、`GET /api/tracking/positions?ids=…`
