"""集中設定（Phase 1.3）：所有環境變數、路徑與常數的唯一來源。

其他模組一律 `from scenario04.config import settings` 取值，
禁止在業務程式碼中散落 os.getenv() 呼叫。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ── 目錄結構 ──────────────────────────────────────────────────────────────────
# settings.py 位於 scenario-advanced01/scenario04/config/
CONFIG_DIR  = Path(__file__).resolve().parent
PACKAGE_DIR = CONFIG_DIR.parent           # scenario04/
APP_DIR     = PACKAGE_DIR.parent          # scenario-advanced01/
# 資料檔（DuckDB、sat_metadata.csv、data/…）仍在原專案根目錄
BASE_DIR    = Path(os.getenv("ATRDC_BASE_DIR", str(APP_DIR.parent)))

load_dotenv(BASE_DIR / ".env")

# ── 資料庫 ────────────────────────────────────────────────────────────────────
# 預設讀取 scenario04/DB/；該目錄找不到時 resolve_db() 回退專案根目錄（舊位置）
DB_DIR        = PACKAGE_DIR / "DB"
LEGACY_DB_DIR = BASE_DIR
DEFAULT_DB    = str(DB_DIR / "space_db_slim.duckdb")
DB_PATH       = Path(os.getenv("DB_PATH", DEFAULT_DB))
RAW_TABLE     = "raw_tle_archive"
META_TABLE    = "sat_n2yo_metadata"
# sat_metadata.csv 同樣優先讀 DB 目錄，缺檔時退回專案根目錄
SAT_META_CSV = (DB_DIR / "sat_metadata.csv"
                if (DB_DIR / "sat_metadata.csv").exists()
                else BASE_DIR / "sat_metadata.csv")

# ── 伺服器 ────────────────────────────────────────────────────────────────────
HOST      = os.getenv("HOST", "0.0.0.0")
PORT      = int(os.getenv("PORT", "5013"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ── 快取 TTL（秒）─────────────────────────────────────────────────────────────
STATS_TTL   = int(os.getenv("STATS_TTL", "600"))
INDEX_TTL   = 600
DB_INFO_TTL = 300
REDIS_URL   = os.getenv("REDIS_URL", "")

# ── 接近事件 / 碰撞機率 ───────────────────────────────────────────────────────
CONJ_THRESHOLD_KM = float(os.getenv("CONJ_THRESHOLD_KM", "10.0"))
CONJ_TTL          = int(os.getenv("CONJ_TTL", "120"))
SIGMA_R_KM        = float(os.getenv("SIGMA_R_KM", "0.1"))   # 位置 1-sigma (km)
SIGMA_T_KM        = float(os.getenv("SIGMA_T_KM", "0.5"))   # 切向 1-sigma
SAT_RADIUS_KM     = 0.005  # 衛星等效硬體半徑

# ── Space-Track ───────────────────────────────────────────────────────────────
SPACETRACK_USER     = os.getenv("SPACETRACK_USER", "")
SPACETRACK_PASS     = os.getenv("SPACETRACK_PASS", "")
CDM_CACHE_TTL       = int(os.getenv("CDM_CACHE_TTL", "3600"))
CDM_HIGH_RISK_LIMIT = int(os.getenv("CDM_HIGH_RISK_LIMIT", "100"))

# ── 台北覆蓋分析 ──────────────────────────────────────────────────────────────
TAIPEI_LAT  = 25.0330
TAIPEI_LON  = 121.5654
TAIPEI_H_KM = 0.01
COVER_KM    = 2000.0
MASK_DEG    = 5.0

TIMELINE_DAYS        = 30    # 時間軸 ±30 天
MAX_PASSES_MATRIX_MB = 512   # 過頂預報矩陣記憶體上限

CESIUM_ION_TOKEN = os.getenv("CESIUM_ION_TOKEN", "")

# ── 檔案資源 ──────────────────────────────────────────────────────────────────
OVERPASS_CATS_FILE        = BASE_DIR / "overpass_cats.yaml"
GLOBE_TEXTURE_LOCAL       = BASE_DIR / "data" / "globe_texture.jpg"
CESIUM_LOCAL_DIR          = BASE_DIR / "data" / "cesium"
BORDERS_LOCAL             = BASE_DIR / "data" / "borders.geojson"
TEXTURE_DIR               = BASE_DIR / "data" / "textures"
LOGO_FILE                 = BASE_DIR / "Logo_ATRDC.png"
SSN_STATIONS_FILE         = CONFIG_DIR / "ssn_stations.geojson"
CLASSIFICATION_RULES_FILE = CONFIG_DIR / "classification_rules.yaml"

# ── 使用者自訂資料（放檔案即生效；格式見各目錄 README.md）────────────────────
USER_TLE_DIR       = PACKAGE_DIR / "user_defined_TLE"             # *.tle / *.txt（2 行或 3 行 TLE）
USER_CATALOGUE_DIR = PACKAGE_DIR / "user_defined_SaTCatalogue"    # *.csv（衛星目錄，支援繁中）
USER_TRACKING_DIR  = PACKAGE_DIR / "user_defined_tracking_NORAD"  # *.csv（NORAD 監測清單，支援繁中）
USER_GEOJSON_DIR   = PACKAGE_DIR / "geojson"                      # *.geojson（自訂向量圖層）
# 精細台灣行政界：檔案存在時，全球國界圖層自動剔除粗糙的台灣輪廓改用本檔
TAIWAN_ADMIN_FILE  = USER_GEOJSON_DIR / "Taiwan-admin-ploygon.geojson"

# ── 外部資源 URL ──────────────────────────────────────────────────────────────
NE_BORDERS_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector"
    "/master/geojson/ne_110m_admin_0_countries.geojson"
)
GLOBE_TEXTURE_CDN = (
    "https://cdn.jsdelivr.net/gh/mrdoob/three.js"
    "/examples/textures/land_ocean_ice_cloud_2048.jpg"
)
