"""使用者自訂資料載入：TLE、衛星目錄（CSV）、NORAD 監測清單（CSV）。

三個目錄放檔案即生效（衛星索引每 INDEX_TTL 秒重建，或呼叫
POST /api/admin/reload_cats 立即生效）：

- user_defined_TLE/            *.tle / *.txt，支援 2 行與 3 行 TLE 混用，
                               名稱行可帶 3LE 的「0 」前綴，# 開頭為註解
- user_defined_SaTCatalogue/   *.csv（utf-8 / utf-8-sig，支援繁體中文），
                               欄位：norad_id,name_zh,name_en,country,purpose,
                                     constellation,operator,launch_date,intl_code,notes
- user_defined_tracking_NORAD/ *.csv，欄位：norad_id,alias,priority,color,enabled,notes
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)

_TLE_PATTERNS = ("*.tle", "*.txt")

# 監測清單優先度正規化（中文 / 英文皆可）
_PRIORITY_MAP = {
    "高": "high", "high": "high", "h": "high",
    "中": "medium", "medium": "medium", "m": "medium",
    "低": "low", "low": "low", "l": "low",
}
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# 監測衛星未指定 color 時依序自動配色
_TRACK_AUTO_COLORS = [
    "#FF4081", "#FFD740", "#00E5FF", "#69F0AE", "#B388FF",
    "#FF8A65", "#4FC3F7", "#F4FF81", "#FF80AB", "#A7FFEB",
]


def _tle_norad_id(line: str) -> int | None:
    """取 TLE line1/line2 第 3–7 欄的 NORAD ID。"""
    try:
        return int(line[2:7].strip())
    except (ValueError, IndexError):
        return None


def _parse_tle_text(text: str, source: str) -> dict[int, dict[str, str]]:
    """解析單一檔案內容：2 行 / 3 行 TLE 混用，# 開頭為註解。"""
    result: dict[int, dict[str, str]] = {}
    pending_name = ""
    pending_l1 = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("1 ") and len(line) >= 69:
            pending_l1 = line
            continue
        if line.startswith("2 ") and len(line) >= 69:
            if not pending_l1:
                logger.warning("user TLE (%s): line2 前缺 line1，略過: %s", source, line[:24])
                continue
            nid1 = _tle_norad_id(pending_l1)
            nid2 = _tle_norad_id(line)
            if nid1 is None or nid1 != nid2:
                logger.warning("user TLE (%s): line1/line2 NORAD 不一致（%s vs %s），略過",
                               source, nid1, nid2)
                pending_l1 = ""; pending_name = ""
                continue
            if nid1 in result:
                logger.warning("user TLE (%s): NORAD %d 重複，後者覆蓋前者", source, nid1)
            result[nid1] = {
                "name":        pending_name,
                "line1":       pending_l1,
                "line2":       line,
                "source_file": source,
            }
            pending_l1 = ""; pending_name = ""
            continue
        # 其餘視為名稱行（3 行格式；3LE「0 」前綴要去掉）
        name = line.strip()
        if name.startswith("0 "):
            name = name[2:].strip()
        pending_name = name
        pending_l1 = ""
    return result


def load_user_tles() -> dict[int, dict[str, str]]:
    """掃描 USER_TLE_DIR 內所有 *.tle / *.txt；回傳 {norad_id: {name,line1,line2,source_file}}。"""
    d: Path = settings.USER_TLE_DIR
    if not d.is_dir():
        return {}
    result: dict[int, dict[str, str]] = {}
    files = sorted({f for pat in _TLE_PATTERNS for f in d.glob(pat)})
    for f in files:
        try:
            parsed = _parse_tle_text(f.read_text(encoding="utf-8-sig"), f.name)
            result.update(parsed)
        except Exception as exc:
            logger.error("user TLE 檔讀取失敗 %s: %s", f.name, exc)
    if result:
        logger.info("使用者自訂 TLE: %d 顆（%d 檔）", len(result), len(files))
    return result


def load_user_catalogue() -> dict[int, dict[str, str]]:
    """掃描 USER_CATALOGUE_DIR 內所有 *.csv；回傳 {norad_id: row dict}。"""
    d: Path = settings.USER_CATALOGUE_DIR
    if not d.is_dir():
        return {}
    result: dict[int, dict[str, str]] = {}
    files = sorted(d.glob("*.csv"))
    for f in files:
        try:
            with f.open(encoding="utf-8-sig", newline="") as fh:
                for i, row in enumerate(csv.DictReader(fh)):
                    raw_id = (row.get("norad_id") or "").strip()
                    if not raw_id:
                        continue
                    try:
                        nid = int(raw_id)
                    except ValueError:
                        logger.warning("user catalogue (%s) 第 %d 列 norad_id 無效: %r",
                                       f.name, i + 2, raw_id)
                        continue
                    entry = {k: (v.strip() if v else "") for k, v in row.items()
                             if k and k != "norad_id"}
                    entry["source_file"] = f.name
                    result[nid] = entry
        except Exception as exc:
            logger.error("user catalogue 讀取失敗 %s: %s", f.name, exc)
    if result:
        logger.info("使用者自訂衛星目錄: %d 筆（%d 檔）", len(result), len(files))
    return result


def load_tracking_list() -> list[dict[str, Any]]:
    """掃描 USER_TRACKING_DIR 內所有 *.csv；回傳監測清單（依 priority 排序）。"""
    d: Path = settings.USER_TRACKING_DIR
    if not d.is_dir():
        return []
    by_id: dict[int, dict[str, Any]] = {}
    files = sorted(d.glob("*.csv"))
    for f in files:
        try:
            with f.open(encoding="utf-8-sig", newline="") as fh:
                for i, row in enumerate(csv.DictReader(fh)):
                    raw_id = (row.get("norad_id") or "").strip()
                    if not raw_id:
                        continue
                    try:
                        nid = int(raw_id)
                    except ValueError:
                        logger.warning("tracking (%s) 第 %d 列 norad_id 無效: %r",
                                       f.name, i + 2, raw_id)
                        continue
                    pr_raw = (row.get("priority") or "").strip()
                    priority = _PRIORITY_MAP.get(pr_raw.lower(), _PRIORITY_MAP.get(pr_raw, "medium"))
                    enabled_raw = (row.get("enabled") or "Y").strip().upper()
                    by_id[nid] = {
                        "norad_id":       nid,
                        "alias":          (row.get("alias") or "").strip(),
                        "priority":       priority,
                        "priority_label": pr_raw or "中",
                        "color":          (row.get("color") or "").strip(),
                        "enabled":        enabled_raw not in ("N", "NO", "0", "FALSE", "否"),
                        "notes":          (row.get("notes") or "").strip(),
                        "source_file":    f.name,
                    }
        except Exception as exc:
            logger.error("tracking 清單讀取失敗 %s: %s", f.name, exc)

    items = sorted(by_id.values(),
                   key=lambda x: (_PRIORITY_ORDER.get(x["priority"], 1), x["norad_id"]))
    # 未指定 color 者自動配色
    for i, item in enumerate(items):
        if not item["color"]:
            item["color"] = _TRACK_AUTO_COLORS[i % len(_TRACK_AUTO_COLORS)]
    if items:
        logger.info("NORAD 監測清單: %d 筆（%d 檔）", len(items), len(files))
    return items
