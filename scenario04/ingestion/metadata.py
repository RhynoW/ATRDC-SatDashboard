"""衛星 metadata 與分類規則（Phase 1.2 / 2.3）。

分類規則（country_map、constellation_rules）優先由
config/classification_rules.yaml 載入；檔案缺失或格式錯誤時退回內建預設。
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import datetime, timezone

from ..config import settings

logger = logging.getLogger(__name__)

# ── 內建預設分類規則（與 classification_rules.yaml 一致）───────────────────────
_COUNTRY_MAP_DEFAULT: dict[str, str] = {
    "United States":                    "美國",
    "Commonwealth of Independent States": "俄羅斯/蘇聯",
    "People's Republic of China":       "中國",
    "United Kingdom":                   "英國",
    "France":                           "法國",
    "Japan":                            "日本",
    "India":                            "印度",
    "European Space Agency":            "ESA",
    "Italy":                            "義大利",
    "Canada":                           "加拿大",
    "Germany":                          "德國",
    "South Korea":                      "韓國",
    "Israel":                           "以色列",
    "Australia":                        "澳洲",
    "Brazil":                           "巴西",
    "Argentina":                        "阿根廷",
    "United Arab Emirates":             "阿聯",
    "Iran":                             "伊朗",
    "North Korea":                      "朝鮮",
    "Luxembourg":                       "盧森堡",
    "Netherlands":                      "荷蘭",
    "Spain":                            "西班牙",
    "Sweden":                           "瑞典",
    "Norway":                           "挪威",
    "New Zealand":                      "紐西蘭",
    "Singapore":                        "新加坡",
    "Turkey":                           "土耳其",
    "Ukraine":                          "烏克蘭",
    "Saudi Arabia":                     "沙烏地阿拉伯",
    # 台灣（FORMOSAT 系列等衛星歸入台灣）
    "TAIWAN":                           "台灣",
    "Republic of China":                "台灣",
    # 商業運營商 → 歸入所在國
    "Globalstar":                       "美國",
    "ORBCOMM":                          "美國",
    "EUTELSAT":                         "法國",
    "International Telecommunications": "美國",
    "SOCIETE EUROPEENNE":               "盧森堡",
    "TBD":                              "不明",
}

_CONSTELLATION_RULES_DEFAULT: list[tuple[str, list[str]]] = [
    # 排列依照 InsideGNSS (2026-05) LEO 星座數量降冪，確保大型星座優先命中
    ("Starlink",            ["STARLINK"]),
    ("OneWeb",              ["ONEWEB"]),
    ("Kuiper",              ["KUIPER"]),
    ("互聯網/Hulianwang",   ["HONGYAN", "XINGYUN", "TIANQI"]),   # 鴻雁/行云/天啟
    ("Planet",              ["FLOCK", "DOVE", "SKYSAT"]),
    ("千帆/Qianfan",        ["QIANFAN", "SPACESAIL"]),
    ("Spire",               ["LEMUR", "SPIRE"]),
    ("Iridium",             ["IRIDIUM"]),
    ("GeeSat/Geespace",     ["GEESPACE", "JIYUAN"]),             # 吉利/吉空
    ("Globalstar",          ["GLOBALSTAR"]),
    ("Hawk",                ["HAWK"]),                           # HawkEye 360
    ("Orbcomm",             ["ORBCOMM"]),
    ("NuSat",               ["NUSAT", "SATELLOGIC"]),            # Satellogic 阿根廷
    ("Skykraft",            ["SKYKRAFT"]),
    ("SpaceMobile",         ["BLUEBIRD", "SPACEMOBILE"]),        # AST SpaceMobile
    ("Lynk",                ["LYNK"]),
    ("Telesat LEO",         ["TELESAT"]),
    ("吉林/Jilin",          ["JILIN"]),
    ("遙感/Yaogan",         ["YAOGAN"]),
    ("高分",                ["GAOFEN"]),
    ("風雲",                ["FENGYUN", "FY-"]),
]

# 模組層狀態（reload_classification_rules() 熱重載）
_COUNTRY_MAP: dict[str, str] = dict(_COUNTRY_MAP_DEFAULT)
_KNOWN_LABELS: set[str] = set(_COUNTRY_MAP.values())
_CONSTELLATION_RULES: list[tuple[str, list[str]]] = list(_CONSTELLATION_RULES_DEFAULT)


def _load_rules_yaml() -> tuple[dict[str, str], list[tuple[str, list[str]]]] | None:
    if not settings.CLASSIFICATION_RULES_FILE.exists():
        return None
    try:
        import yaml
        with settings.CLASSIFICATION_RULES_FILE.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("YAML 根節點必須是 mapping")
        cmap = data.get("country_map")
        crules_raw = data.get("constellation_rules")
        country_map = ({str(k): str(v) for k, v in cmap.items()}
                       if isinstance(cmap, dict) and cmap else None)
        crules: list[tuple[str, list[str]]] | None = None
        if isinstance(crules_raw, list) and crules_raw:
            crules = []
            for item in crules_raw:
                label = str(item["label"])
                kws   = [str(k).upper() for k in item["keywords"]]
                crules.append((label, kws))
        if country_map is None and crules is None:
            return None
        return (country_map or dict(_COUNTRY_MAP_DEFAULT),
                crules or list(_CONSTELLATION_RULES_DEFAULT))
    except Exception as exc:
        logger.warning("classification_rules.yaml 載入失敗，使用內建預設: %s", exc)
        return None


def reload_classification_rules() -> dict[str, int]:
    """載入（或重載）YAML 分類規則；回傳規則數量摘要。"""
    global _COUNTRY_MAP, _KNOWN_LABELS, _CONSTELLATION_RULES
    loaded = _load_rules_yaml()
    if loaded is not None:
        _COUNTRY_MAP, _CONSTELLATION_RULES = loaded
        logger.info(
            "分類規則載入: country_map=%d, constellation_rules=%d",
            len(_COUNTRY_MAP), len(_CONSTELLATION_RULES))
    else:
        _COUNTRY_MAP = dict(_COUNTRY_MAP_DEFAULT)
        _CONSTELLATION_RULES = list(_CONSTELLATION_RULES_DEFAULT)
    _KNOWN_LABELS = set(_COUNTRY_MAP.values())
    return {"country_map": len(_COUNTRY_MAP),
            "constellation_rules": len(_CONSTELLATION_RULES)}


reload_classification_rules()


# ── 分類函式 ──────────────────────────────────────────────────────────────────

def classify_country(source_code: str | None) -> str:
    if not source_code:
        return "不明"
    sc = source_code.strip()
    if sc in _KNOWN_LABELS:
        return sc
    for key, label in _COUNTRY_MAP.items():
        if key.lower() in sc.lower():
            return label
    return "其他"


def classify_constellation(name: str) -> str | None:
    n = name.upper()
    for label, kws in _CONSTELLATION_RULES:
        if any(k in n for k in kws):
            return label
    return None


def classify_purpose(name: str) -> str:
    n = name.upper()
    if " DEB" in n or n.endswith(" DEB") or "DEBRIS" in n:
        return "碎片"
    if " R/B" in n or n.endswith(" R/B") or " RB" in n or "ROCKET BODY" in n:
        return "火箭體"
    if "OBJECT" in n:
        return "不明物體"
    return "有效載荷"


def classify_era(launch_date: datetime | None, intl_code: str | None) -> str:
    if launch_date is None and intl_code:
        m = re.match(r"^(\d{4})", str(intl_code))
        if m:
            try:
                launch_date = datetime(int(m.group(1)), 1, 1, tzinfo=timezone.utc)
            except ValueError:
                pass
    if launch_date is None:
        return "不明"
    if launch_date.tzinfo is None:
        launch_date = launch_date.replace(tzinfo=timezone.utc)
    delta_days = (datetime.now(timezone.utc) - launch_date).days
    if delta_days < 365:
        return "< 1 年"
    if delta_days < 365 * 5:
        return "1–5 年"
    if delta_days < 365 * 10:
        return "5–10 年"
    return "> 10 年"


# ── sat_metadata.csv ─────────────────────────────────────────────────────────

def load_sat_metadata_csv() -> dict[int, dict[str, str]]:
    if not settings.SAT_META_CSV.exists():
        return {}
    result: dict[int, dict[str, str]] = {}
    try:
        with settings.SAT_META_CSV.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                raw_id = row.get("norad_id", "").strip()
                if not raw_id:
                    continue
                try:
                    nid = int(raw_id)
                except ValueError:
                    continue
                result[nid] = {k: (v.strip() if v else "") for k, v in row.items()
                               if k != "norad_id"}
        logger.info("sat_metadata.csv: %d 筆", len(result))
    except Exception as exc:
        logger.error("sat_metadata.csv 讀取失敗: %s", exc)
    return result
