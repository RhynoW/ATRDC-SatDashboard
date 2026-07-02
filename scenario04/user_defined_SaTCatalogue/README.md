# user_defined_SaTCatalogue — 使用者自訂衛星目錄

把 `*.csv` 檔放進本目錄即自動讀取，**覆寫**索引中對應 NORAD ID 的中繼資料
（名稱/國家/用途/星座/年代）。支援繁體中文；編碼 UTF-8 或 UTF-8 BOM
（Excel 另存 CSV UTF-8 即可）。

## 欄位定義

| 欄位 | 必填 | 說明 |
|---|---|---|
| `norad_id` | ✔ | NORAD 編號（整數） |
| `name_zh` | | 中文名稱（優先作為顯示名稱） |
| `name_en` | | 英文名稱（無 name_zh 時作為顯示名稱） |
| `country` | | 國家（中文，如 `台灣`、`美國`；對應儀表板「國家」分類） |
| `purpose` | | 用途（如 `光學遙測`、`載人太空站`；對應「用途」分類） |
| `constellation` | | 星座名稱（對應「星座」分類） |
| `operator` | | 操作單位 |
| `launch_date` | | 發射日期 `YYYY-MM-DD`（用於計算「年代」分類） |
| `intl_code` | | 國際編號（如 `2017-049A`；launch_date 缺時以年份推算年代） |
| `notes` | | 備註 |

範例見 [user_catalogue01.csv](user_catalogue01.csv)。

## 行為

- 只覆寫**有填值**的欄位，空欄位保留原資料
- 對應 NORAD ID 需有 TLE（DB 或 `user_defined_TLE/`）才會顯示；
  只有目錄、沒有 TLE 者會記 warning 並略過
- API：`GET /api/user/catalogue` 查看載入結果
