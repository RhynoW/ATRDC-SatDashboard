"""Starlink 顆數普查與離軌名單：即時查詢本系統資料庫，並與 keeptrack.space 公開數字比對。

背景：不同來源對「Starlink 顆數」的統計口徑不同——是否扣除已再入、是否要求近期仍有
TLE 更新——會讓數字相差百餘顆。本模組把口徑攤開來看，而非只給單一數字。

重要：部署用的 slim DB（build_slim_db.py 產出）不含 object_name 欄位（已被裁減），
所以「哪些 NORAD 是 Starlink」不能用 `raw_tle_archive.object_name ILIKE '%STARLINK%'`
查，改用本專案既有的名稱來源 sat_metadata.csv（經 classify_constellation() 分類，
與 config/classification_rules.yaml 的 Starlink 關鍵字規則一致），取得 NORAD 集合後
再拿去對 raw_tle_archive 做 JOIN／IN 篩選。

函式：
  keeptrack_starlink_count()  伺服器端抓取 keeptrack.space 公開頁面之 FAQPage JSON-LD
                              （結構化資料，比解析任意 HTML 穩定），內建 6 小時快取。
  our_starlink_counts()       本系統依幾種常見口徑各算一次 Starlink 顆數。
  list_deorbiting_starlinks() 近期仍有 TLE、但半長軸快速下降的 Starlink（離軌候選）；
                              對每顆用簡單線性外推給一個「粗估剩餘天數」，非精確再入預測。
  count_v3_candidates()       依國際編號精確比對 Starlink V3（新一代）已編目數量＋
                              CelesTrak 補充檔暫定名單（尚未正式編目者）。
  estimate_reentry_detail()   單顆衛星之零階再入估算（SGP4 逐圈外推近地點）。
"""
from __future__ import annotations

import copy
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Any

import duckdb
import pandas as pd

from ..config import settings
from ..ingestion.db import resolve_db
from ..ingestion.metadata import classify_constellation, load_sat_metadata_csv

logger = logging.getLogger(__name__)

KEEPTRACK_URL = "https://keeptrack.space/starlink-satellite-count"
_KEEPTRACK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_KEEPTRACK_TTL = 6 * 3600  # 對方文案自述「每日更新」，6 小時快取足夠且不過度打擾對方伺服器

_kt_cache: dict[str, Any] = {}
_kt_cached_at: float = 0.0

# 離軌候選判定口徑（與 starlink_lifecycle story 舊版靜態表格一致，改為即時查詢）
REENTRY_ALT_KM = 80.0         # 線性粗估之「再入」高度（與分段校準 stop_alt_km 一致）
DEORBIT_FRESH_DAYS = 5        # 近 N 天仍有 TLE（代表尚未失去追蹤/尚未確認再入）
DEORBIT_DA30_KM = 40.0        # 近 30 天半長軸下降門檻（km）
DEORBIT_ALT_MAX_KM = 480.0    # 高度低於工作殼層下緣

_starlink_ids_cache: set[int] | None = None
_starlink_ids_cached_at: float = 0.0
_STARLINK_IDS_TTL = 3600.0    # sat_metadata.csv 更新頻率低（隨每日管線），1 小時重載一次足夠


def _starlink_norad_ids() -> set[int]:
    """由 sat_metadata.csv（本專案唯一可靠的衛星名稱來源）取得目前已知的 Starlink NORAD 集合。"""
    global _starlink_ids_cache, _starlink_ids_cached_at
    now = time.monotonic()
    if _starlink_ids_cache is not None and (now - _starlink_ids_cached_at) < _STARLINK_IDS_TTL:
        return _starlink_ids_cache
    meta = load_sat_metadata_csv()
    ids = {nid for nid, row in meta.items()
           if classify_constellation(row.get("name_en", "") or "") == "Starlink"}
    _starlink_ids_cache = ids
    _starlink_ids_cached_at = now
    logger.info("_starlink_norad_ids(): %d 顆（sat_metadata.csv 分類）", len(ids))
    return ids


def _register_starlink_ids(con: duckdb.DuckDBPyConnection, ids: set[int]) -> None:
    con.register("starlink_ids", pd.DataFrame({"norad_id": list(ids)}))


def _starlink_name(norad_id: int) -> str | None:
    meta = load_sat_metadata_csv()
    row = meta.get(norad_id)
    return row.get("name_en") if row else None


def keeptrack_starlink_count() -> dict[str, Any]:
    """抓取並解析 keeptrack.space 的 Starlink 顆數頁面（FAQPage JSON-LD），6 小時快取。

    失敗時（對方改版、網路不通等）回傳 {"error": "..."}，呼叫端應優雅降級，
    不要讓外部網站不可用拖垮本系統頁面。
    """
    global _kt_cache, _kt_cached_at
    now = time.monotonic()
    if _kt_cache and (now - _kt_cached_at) < _KEEPTRACK_TTL:
        return _kt_cache

    try:
        import requests
        resp = requests.get(KEEPTRACK_URL, headers={"User-Agent": _KEEPTRACK_UA}, timeout=10)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:  # noqa: BLE001
        logger.warning("keeptrack.space 抓取失敗：%s", exc)
        return {"error": f"抓取失敗：{exc}", "source_url": KEEPTRACK_URL}

    result: dict[str, Any] = {"source_url": KEEPTRACK_URL,
                              "fetched_at": datetime.now(timezone.utc).isoformat()}
    try:
        # 該頁有多個 JSON-LD <script> 區塊；找含 FAQPage 的那一個
        for m in re.finditer(
            r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
        ):
            block = m.group(1)
            if '"FAQPage"' not in block:
                continue
            data = json.loads(block)
            qa = {q["name"]: q["acceptedAnswer"]["text"]
                  for q in data.get("mainEntity", []) if "name" in q}
            for _name, text in qa.items():
                nm = re.search(r"([\d,]+)\s+Starlink satellites (?:are\s+)?in orbit", text)
                if nm:
                    result["in_orbit"] = int(nm.group(1).replace(",", ""))
                    dm = re.search(r"As of ([A-Za-z]+ \d{1,2}, \d{4})", text)
                    if dm:
                        result["as_of"] = dm.group(1)
                lm = re.search(r"launched ([\d,]+) Starlink satellites", text)
                if lm:
                    result["launched_total"] = int(lm.group(1).replace(",", ""))
                dem = re.search(r"About ([\d,]+) of those have since deorbited", text)
                if dem:
                    result["deorbited_total"] = int(dem.group(1).replace(",", ""))
                wm = re.search(r"([\d,]+) Starlink satellites are currently working", text)
                if wm:
                    result["working"] = int(wm.group(1).replace(",", ""))
            break
        if "in_orbit" not in result:
            result["error"] = "頁面結構已變更，未能解析出數字（FAQPage JSON-LD 找不到預期欄位）"
    except Exception as exc:  # noqa: BLE001
        logger.warning("keeptrack.space 解析失敗：%s", exc)
        result["error"] = f"解析失敗：{exc}"

    _kt_cache = result
    _kt_cached_at = now
    return result


def our_starlink_counts() -> dict[str, Any]:
    """本系統依幾種常見口徑各算一次 Starlink 顆數（即時查詢，不快取——DB 本身已有連線層快取）。"""
    db = resolve_db()
    if db is None:
        return {"error": "資料庫不存在"}
    ids = _starlink_norad_ids()
    if not ids:
        return {"error": "sat_metadata.csv 內找不到任何 Starlink 衛星（檔案缺失或分類規則異常）"}
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            _register_starlink_ids(con, ids)

            def count(where_extra: str = "") -> int:
                sql = (f"SELECT COUNT(DISTINCT r.norad_id) FROM {settings.RAW_TABLE} r "
                       f"JOIN starlink_ids s ON s.norad_id = r.norad_id {where_extra}")
                return int(con.execute(sql).fetchone()[0])

            latest = con.execute(
                f"SELECT max(epoch_utc) FROM {settings.RAW_TABLE}").fetchone()[0]
            out = {
                "db_latest_epoch": latest.isoformat() if hasattr(latest, "isoformat") else str(latest),
                "known_starlink_norad_count": len(ids),
                "cumulative_all_time": count(),
                "fresh_30d": count("WHERE r.epoch_utc >= now() - INTERVAL 30 DAY"),
                "fresh_14d": count("WHERE r.epoch_utc >= now() - INTERVAL 14 DAY"),
                "fresh_7d": count("WHERE r.epoch_utc >= now() - INTERVAL 7 DAY"),
            }
            return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("our_starlink_counts 失敗：%s", exc)
        return {"error": str(exc)}


DEORBIT_LOOKBACK_DAYS = 45            # 需涵蓋「30 天前」＋ TLE 間隙餘裕
DEORBIT_CACHE_TTL_S = 600
_DEORBIT_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}


def list_deorbiting_starlinks(limit: int = 60) -> dict[str, Any]:
    """近期仍有 TLE、半長軸快速下降的 Starlink（離軌候選），依目前高度由低到高排序。

    「估計剩餘天數」為粗略線性外推（以近 7 天平均每日下降速率、外推目前高度降到 80 km 所需天數），
    未考慮阻力隨高度下降而指數增強，因此會**高估**剩餘天數（實際再入更早；2026-09 以 38 顆
    Starlink 官方 TIP 回測，舊版「外推到高度歸零」高估 350–960 小時）。較可靠的預測為
    services.deorbit_forecast 之分段 M/A 校準（背景批次快取；「詳細」按鈕即時計算）。
    """
    db = resolve_db()
    if db is None:
        return {"error": "資料庫不存在"}
    ids = _starlink_norad_ids()
    if not ids:
        return {"error": "sat_metadata.csv 內找不到任何 Starlink 衛星（檔案缺失或分類規則異常）"}
    cached = _DEORBIT_CACHE.get(int(limit))
    if cached and time.monotonic() - cached[0] < DEORBIT_CACHE_TTL_S:
        return copy.deepcopy(cached[1])
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            _register_starlink_ids(con, ids)
            # 只取近 DEORBIT_LOOKBACK_DAYS 天：「30 天前高度」只需這段期間；原本對全歷史開窗函數、
            # 且總數另跑一次同樣查詢，完整庫要 40–55 s、HF 精簡庫約 20 s。
            sql = f"""
                WITH base AS (
                    SELECT r.norad_id, r.epoch_utc, r.sma_km - 6378.137 AS alt_km
                    FROM {settings.RAW_TABLE} r
                    JOIN starlink_ids s ON s.norad_id = r.norad_id
                    WHERE r.epoch_utc >= now() - INTERVAL {DEORBIT_LOOKBACK_DAYS} DAY
                ), cur AS (
                    SELECT norad_id, epoch_utc, alt_km FROM base
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY norad_id ORDER BY epoch_utc DESC) = 1
                ), past30 AS (
                    SELECT norad_id, alt_km AS alt_km_30d_ago FROM base
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY norad_id
                        ORDER BY abs(date_diff('hour', epoch_utc, now() - INTERVAL 30 DAY))
                    ) = 1
                ), past7 AS (
                    SELECT norad_id, alt_km AS alt_km_7d_ago FROM base
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY norad_id
                        ORDER BY abs(date_diff('hour', epoch_utc, now() - INTERVAL 7 DAY))
                    ) = 1
                )
                SELECT cur.norad_id, cur.epoch_utc, cur.alt_km,
                       cur.alt_km - past30.alt_km_30d_ago AS da_30d_km,
                       cur.alt_km - past7.alt_km_7d_ago AS da_7d_km,
                       m.launch_date
                FROM cur
                JOIN past30 USING (norad_id)
                JOIN past7 USING (norad_id)
                LEFT JOIN {settings.META_TABLE} m ON m.norad_id = cur.norad_id
                WHERE cur.epoch_utc >= now() - INTERVAL {DEORBIT_FRESH_DAYS} DAY
                  AND (past30.alt_km_30d_ago - cur.alt_km) >= {DEORBIT_DA30_KM}
                  AND cur.alt_km < {DEORBIT_ALT_MAX_KM}
                ORDER BY cur.alt_km ASC
            """
            allrows = con.execute(sql).fetchdf()
        total_n = len(allrows)
        rows = allrows.head(int(limit))

        items = []
        for _, r in rows.iterrows():
            da7 = float(r["da_7d_km"])
            alt = float(r["alt_km"])
            daily_rate = da7 / 7.0  # 負值＝下降
            est_days = (round(max(alt - REENTRY_ALT_KM, 0.0) / abs(daily_rate), 1)
                        if daily_rate < -0.05 else None)
            nid = int(r["norad_id"])
            items.append({
                "norad_id": nid,
                "name": _starlink_name(nid) or f"NORAD-{nid}",
                "launch_date": str(r["launch_date"]) if r["launch_date"] is not None else None,
                "epoch_utc": r["epoch_utc"].isoformat() if hasattr(r["epoch_utc"], "isoformat") else str(r["epoch_utc"]),
                "alt_km": round(alt, 1),
                "da_30d_km": round(float(r["da_30d_km"]), 1),
                "da_7d_km": round(da7, 1),
                "est_days_to_reentry_rough": est_days,
            })
        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "criteria": {
                "fresh_days": DEORBIT_FRESH_DAYS, "da30_km_min": DEORBIT_DA30_KM,
                "alt_km_max": DEORBIT_ALT_MAX_KM,
            },
            "total_matching": total_n,
            "shown": len(items),
            "items": items,
            "method": "est_days_to_reentry_rough 為近 7 天平均下降速率線性外推至 80 km 之天數，"
                      "未計入阻力隨高度指數增強，會高估剩餘天數（實際再入更早），僅供排序參考；"
                      "較可靠之再入預測見 seg 欄（分段 M/A 校準，背景批次每 6 小時更新）。",
        }
        _DEORBIT_CACHE[int(limit)] = (time.monotonic(), result)
        return copy.deepcopy(result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_deorbiting_starlinks 失敗：%s", exc)
        return {"error": str(exc)}


# ── Starlink 世代分類（啟發式，依公開已知的發射日期區間切分）─────────────────
# Space-Track／CelesTrak 目錄不含硬體世代標記，這裡完全依 sat_metadata.csv 的
# launch_date（官方編目日期，可靠）對照「公開已知的世代量產時期」做近似切分。
# 世代交界日期為近似值（實際交接通常有數週到數月的重疊過渡期，並非某天硬切換），
# 僅供教學/展示用的粗略篩選，不是逐顆硬體序號比對的精確分類。
# 這個日期只用於本頁「世代篩選」下拉選單的粗略分類；判定某顆衛星是否為 V3 的精確方法
# 見下方 count_v3_candidates()（比對 TLE 國際編號，非本日期）。
V3_GENERATION_START = "2026-09-28"   # Starship Flight 14 發射日
GENERATION_BANDS: list[tuple[str, str | None, str | None]] = [
    # (代號, 起始日期含, 結束日期含；None 表示不設下限/上限)
    ("v1.0",   None,          "2021-05-31"),
    ("v1.5",   "2021-06-01",  "2022-12-31"),
    ("v2mini", "2023-01-01",  None),   # 結束日在 _generation_of() 內以 V3_GENERATION_START 動態代入
]
GENERATION_LABELS = {"v1.0": "v1.0", "v1.5": "v1.5", "v2mini": "v2 Mini", "v3": "V3"}


def _generation_of(launch_date_str: str | None) -> str:
    if not launch_date_str:
        return "unknown"
    if launch_date_str >= V3_GENERATION_START:
        return "v3"
    for gen, start, end in GENERATION_BANDS:
        if start and launch_date_str < start:
            continue
        if end and launch_date_str > end:
            continue
        return gen
    return "unknown"


def starlink_ids_by_generation(generation: str) -> set[int]:
    """回傳指定世代（"all"｜"v1.0"｜"v1.5"｜"v2mini"｜"v3"）的 Starlink NORAD 集合。"""
    all_ids = _starlink_norad_ids()
    if generation in ("", "all"):
        return all_ids
    meta = load_sat_metadata_csv()
    return {nid for nid in all_ids
            if _generation_of((meta.get(nid, {}) or {}).get("launch_date")) == generation}


def generation_breakdown() -> dict[str, int]:
    """各世代目前已知顆數（即時，依 sat_metadata.csv 現況）。"""
    meta = load_sat_metadata_csv()
    counts: dict[str, int] = {}
    for nid in _starlink_norad_ids():
        gen = _generation_of((meta.get(nid, {}) or {}).get("launch_date"))
        counts[gen] = counts.get(gen, 0) + 1
    return counts


def deorbiting_norad_ids() -> set[int]:
    """目前正在離軌的 Starlink NORAD 集合（與 list_deorbiting_starlinks() 同一套判定口徑，
    但不含 limit、不算 est_days，供其他計算（如可見性分析）用來排除失效衛星）。"""
    db = resolve_db()
    if db is None:
        return set()
    ids = _starlink_norad_ids()
    if not ids:
        return set()
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            _register_starlink_ids(con, ids)
            sql = f"""
                WITH base AS (
                    SELECT r.norad_id, r.epoch_utc, r.sma_km - 6378.137 AS alt_km
                    FROM {settings.RAW_TABLE} r
                    JOIN starlink_ids s ON s.norad_id = r.norad_id
                ), cur AS (
                    SELECT norad_id, epoch_utc, alt_km FROM base
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY norad_id ORDER BY epoch_utc DESC) = 1
                ), past30 AS (
                    SELECT norad_id, alt_km AS alt_km_30d_ago FROM base
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY norad_id
                        ORDER BY abs(date_diff('hour', epoch_utc, now() - INTERVAL 30 DAY))
                    ) = 1
                )
                SELECT cur.norad_id FROM cur JOIN past30 USING (norad_id)
                WHERE cur.epoch_utc >= now() - INTERVAL {DEORBIT_FRESH_DAYS} DAY
                  AND (past30.alt_km_30d_ago - cur.alt_km) >= {DEORBIT_DA30_KM}
                  AND cur.alt_km < {DEORBIT_ALT_MAX_KM}
            """
            return {int(r[0]) for r in con.execute(sql).fetchall()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("deorbiting_norad_ids 失敗：%s", exc)
        return set()


# ── Starlink 軌道殼層分類（即時；StoryMap「shells」區塊）─────────────────────────
# 依傾角分群（公開申請文件之殼層傾角）；工作高度不寫死——2026 年起各殼層已陸續降到
# ~460–485 km，故以該群「近 30 天有 TLE」衛星最密集的高度帶即時代表工作高度。
SHELL_BANDS: list[tuple[str, float, float]] = [
    ("43°", 42.0, 44.0), ("53.0°", 52.5, 53.1), ("53.2°", 53.1, 53.5),
    ("70°", 69.0, 71.0), ("97.6°", 96.5, 98.5),
]
SHELL_TOL_KM = 20.0          # 工作高度 ±20 km 內視為「在工作殼層」
SHELL_LOW_KM = 300.0         # 低於此高度＝即將再入
SHELL_FRESH_DAYS = 30
SHELL_CACHE_TTL_S = 600
_SHELL_CACHE: tuple[float, dict[str, Any]] | None = None


def _mode_alt(alt: pd.Series, bin_km: float = 5.0) -> float:
    """最密集 bin_km 高度帶內的中位數（殼層可能雙峰，如 70° 同時有 ~475 與 ~570 km 兩群）。"""
    b = (alt // bin_km) * bin_km
    top = b.value_counts().idxmax()
    return float(alt[(alt >= top - bin_km) & (alt < top + 2 * bin_km)].median())


def classify_shells(df: pd.DataFrame, tol_km: float = SHELL_TOL_KM,
                    low_km: float = SHELL_LOW_KM) -> dict[str, Any]:
    """df 欄位：norad_id、alt_km、inc_deg（每星一列，最新 TLE）。回傳各殼層統計。"""
    shells, used = [], pd.Series(False, index=df.index)
    for name, lo, hi in SHELL_BANDS:
        m = (df["inc_deg"] >= lo) & (df["inc_deg"] < hi)
        used |= m
        alt = df.loc[m, "alt_km"]
        if alt.empty:
            shells.append({"shell": name, "inc_range": [lo, hi], "count": 0})
            continue
        work = _mode_alt(alt[alt >= low_km] if (alt >= low_km).any() else alt)
        at = (alt - work).abs() <= tol_km
        shells.append({
            "shell": name, "inc_range": [lo, hi], "count": int(m.sum()),
            "work_alt_km": round(work, 1),
            "alt_p10_km": round(float(alt.quantile(0.1)), 1), "alt_p90_km": round(float(alt.quantile(0.9)), 1),
            "at_shell": int(at.sum()),
            "below": int(((alt < work - tol_km) & (alt >= low_km)).sum()),
            "above": int((alt > work + tol_km).sum()),
            "reentry_imminent": int((alt < low_km).sum()),
            "retiring": work < 400.0,   # 最密集高度帶已低於 400 km：舊世代退役中之殼層
        })
    return {"shells": shells, "total": int(len(df)), "unclassified": int((~used).sum()),
            "tol_km": tol_km, "low_km": low_km}


def starlink_shells() -> dict[str, Any]:
    """近 30 天有 TLE 的 Starlink，依傾角殼層分類（10 分鐘快取）。"""
    global _SHELL_CACHE
    if _SHELL_CACHE and time.monotonic() - _SHELL_CACHE[0] < SHELL_CACHE_TTL_S:
        return copy.deepcopy(_SHELL_CACHE[1])
    db = resolve_db()
    if db is None:
        return {"error": "資料庫不存在"}
    ids = _starlink_norad_ids()
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            _register_starlink_ids(con, ids)
            df = con.execute(f"""
                SELECT r.norad_id, r.sma_km - 6378.137 AS alt_km, r.inclination_deg AS inc_deg, r.epoch_utc
                FROM {settings.RAW_TABLE} r JOIN starlink_ids s ON s.norad_id = r.norad_id
                WHERE r.epoch_utc >= now() - INTERVAL {SHELL_FRESH_DAYS} DAY AND r.epoch_utc <= now()
                QUALIFY ROW_NUMBER() OVER (PARTITION BY r.norad_id ORDER BY r.epoch_utc DESC) = 1
            """).fetchdf()
        out = classify_shells(df)
        out.update({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fresh_days": SHELL_FRESH_DAYS,
            "db_latest_epoch": str(df["epoch_utc"].max()) if len(df) else None,
            "method": f"傾角分群；工作高度＝該群最密集的 5 km 高度帶（排除 <{SHELL_LOW_KM:.0f} km）；"
                      f"±{SHELL_TOL_KM:.0f} km 內為在工作殼層。單筆 TLE 無法分辨抬軌或離軌，"
                      "「低於工作殼層」兩者皆含，離軌者見離軌名單。",
        })
        _SHELL_CACHE = (time.monotonic(), out)
        return copy.deepcopy(out)
    except Exception as exc:  # noqa: BLE001
        logger.warning("starlink_shells 失敗：%s", exc)
        return {"error": str(exc)}


# ── Starlink V3 部署統計（2026-09-29 改版：國際編號比對＋CelesTrak 補充檔暫定名單）──────
# 舊版「部署時程 + 入軌殼層」啟發式已停用：Flight 14 實際入軌約 270 km（發射方位角 30.5°
# 低傾角停泊軌道），遠低於原本估計的 323–327.5／473–477.5 km 工作殼層，證明入軌高度隨
# 單次任務差異極大、不適合當通用判別依據。改為直接比對 TLE line1 之國際編號（第 10–14
# 字元＝兩位發射年＋三位當年度發射序號，見 https://en.wikipedia.org/wiki/International_Designator）：
# 每次已知的 Starship V3 部署批次，依公開報導／CelesTrak 補充檔確認後手動加入本清單。
V3_INTL_LAUNCHES: list[tuple[int, int]] = [
    (2026, 225),   # Starship Flight 14，2026-09-28 12:46 UTC 發射，26 顆 STARLINK-400xx
]
V3_PROVISIONAL_TTL_S = 4 * 3600     # CelesTrak 補充檔約每日 3 次更新（04:30/12:30/20:30 UTC）
_V3_PROVISIONAL_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _v3_intl_sql_filter(column: str) -> str:
    """組出以 TLE line1 國際編號欄位比對 V3_INTL_LAUNCHES 的 SQL 條件（1-indexed substr）。"""
    if not V3_INTL_LAUNCHES:
        return "1=0"
    return " OR ".join(
        f"(substr({column}, 10, 2) = '{yr % 100:02d}' AND substr({column}, 12, 3) = '{num:03d}')"
        for yr, num in V3_INTL_LAUNCHES
    )


def fetch_v3_provisional_roster() -> dict[str, Any]:
    """向 CelesTrak 補充檔（SpaceX 自行提供之軌道根數）即時查詢已知 V3 批次的暫定名單。

    這些物件多數尚未取得正式 Space-Track NORAD 編號——回傳的 NORAD_CAT_ID 是 CelesTrak
    的暫用佔位碼，本系統**不會**把它們寫入資料庫，以免日後正式編目後與真正的 NORAD 號碼
    衝突。純粹用來顯示「已發射、SpaceX 已釋出軌道根數，但尚未進入本系統每日 TLE 管線」
    這段空窗期的部署進度；失敗（CelesTrak 無回應等）時優雅降級為 {"error": ...}。
    """
    key = ",".join(f"{yr}-{num:03d}" for yr, num in V3_INTL_LAUNCHES)
    now = time.monotonic()
    cached = _V3_PROVISIONAL_CACHE.get(key)
    if cached and (now - cached[0]) < V3_PROVISIONAL_TTL_S:
        return cached[1]
    import requests
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for yr, num in V3_INTL_LAUNCHES:
        intdes = f"{yr}-{num:03d}"
        try:
            resp = requests.get(
                "https://celestrak.org/NORAD/elements/supplemental/sup-gp.php",
                params={"INTDES": intdes, "FORMAT": "json"},
                headers={"User-Agent": _KEEPTRACK_UA}, timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{intdes}：{exc}")
            continue
        if not isinstance(data, list):
            continue
        for o in data:
            alt_km = None
            try:
                n_rad_s = float(o["MEAN_MOTION"]) * 2 * math.pi / 86400.0
                alt_km = round((398600.4418 / n_rad_s ** 2) ** (1 / 3) - 6378.137, 1)
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                pass
            items.append({
                "name": o.get("OBJECT_NAME"), "object_id": o.get("OBJECT_ID"),
                "provisional_norad_cat_id": o.get("NORAD_CAT_ID"), "epoch": o.get("EPOCH"),
                "alt_km": alt_km, "inclination_deg": o.get("INCLINATION"),
                "data_source": o.get("DATA_SOURCE"),
            })
    def _piece_key(oid: str | None) -> tuple[str, int, str]:
        # COSPAR 發射片段序：A..Z, AA..AZ, BA.. — 依長度再依字母排（純字串排序 "AA" 會排在 "B" 之前）
        oid = oid or ""
        launch, _, piece = oid.rpartition("-")
        return (launch, len(piece), piece)

    items.sort(key=lambda x: _piece_key(x.get("object_id")))
    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "CelesTrak 補充檔（sup-gp.php；SpaceX 自行提供之軌道根數，非 Space-Track 官方編目）",
        "note": "暫定名單：NORAD 編號為 CelesTrak 暫用碼、非官方 SATCAT 號碼，不會寫入本系統資料庫。"
                "一旦 Space-Track 正式編目、TLE 進入每日管線，會自動改列入上方「已編目」清單"
                "（並改用真正的 NORAD 編號）。",
        "count": len(items), "items": items,
    }
    if errors and not items:
        result["error"] = "；".join(errors)
    _V3_PROVISIONAL_CACHE[key] = (now, result)
    return result


def count_v3_candidates() -> dict[str, Any]:
    """依國際編號精確辨識已正式編目的 Starlink V3，並附上尚未編目者的暫定名單。

    Space-Track／CelesTrak 的公開目錄不會標記衛星的硬體世代（v1.0／v1.5／v2 Mini／V3），
    但每次 Starship 部署一批 V3 都有固定的國際編號前綴（發射年＋當年度發射序號），一旦
    公開報導或 CelesTrak 補充檔確認即可精確比對——不必再靠「部署時程＋入軌殼層」這種
    容易受單次任務差異影響的啟發式（Flight 14 實際入軌高度就與舊版估計差了 200 km 以上）。
    比對用的國際編號直接取自 TLE line1，不依賴 sat_metadata.csv 的 intl_code 欄位（後者
    來源另有時間差，可能落後於 TLE 攝入）。新衛星須待 Space-Track 正式編目、TLE 進入本
    系統每日管線後才會計入 candidate_count／items；編目前的暫定名單見 provisional。
    """
    db = resolve_db()
    if db is None:
        return {"error": "資料庫不存在"}
    ids = _starlink_norad_ids()
    if not ids:
        return {"error": "sat_metadata.csv 內找不到任何 Starlink 衛星（檔案缺失或分類規則異常）"}
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            _register_starlink_ids(con, ids)
            sql = f"""
                WITH first_seen AS (
                    SELECT r.norad_id,
                           min(r.epoch_utc) AS first_epoch,
                           arg_min(r.sma_km, r.epoch_utc) - 6378.137 AS alt_km,
                           arg_min(r.line1, r.epoch_utc) AS first_line1
                    FROM {settings.RAW_TABLE} r
                    JOIN starlink_ids s ON s.norad_id = r.norad_id
                    WHERE r.line1 IS NOT NULL
                    GROUP BY r.norad_id
                )
                SELECT norad_id, first_epoch, alt_km
                FROM first_seen
                WHERE {_v3_intl_sql_filter("first_line1")}
                ORDER BY first_epoch
            """
            rows = con.execute(sql).fetchdf()
            latest = con.execute(
                f"SELECT max(epoch_utc) FROM {settings.RAW_TABLE}").fetchone()[0]
        items = []
        for _, r in rows.iterrows():
            nid = int(r["norad_id"])
            items.append({
                "norad_id": nid, "name": _starlink_name(nid) or f"NORAD-{nid}",
                "first_epoch": r["first_epoch"].isoformat() if hasattr(r["first_epoch"], "isoformat") else str(r["first_epoch"]),
                "alt_km": round(float(r["alt_km"]), 1),
            })
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "intl_launches": [f"{yr}-{num:03d}" for yr, num in V3_INTL_LAUNCHES],
            "db_latest_epoch": latest.isoformat() if hasattr(latest, "isoformat") else str(latest),
            "candidate_count": len(items),
            "items": items,
            "provisional": fetch_v3_provisional_roster(),
            "known_schedule_note": "公開報導（非本系統可獨立驗證）：Starship Flight 14 於 2026-09-28 "
                                   "12:46 UTC 發射，首度進入軌道並部署 26 顆 Starlink V3，SpaceX 確認全數建立聯繫。"
                                   "V3 單顆約 2,000 kg（報導引述之標稱值；V2 Mini 約 800 kg）；26 顆合計約 52 公噸"
                                   "為換算值，非官方實測。",
            "method": "以 TLE 國際編號（發射年＋當年度發射序號）精確比對已知 V3 部署批次清單"
                      "（見 intl_launches），非入軌高度或發射日期之啟發式猜測；"
                      "尚未正式編目者見下方 provisional 暫定名單。",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("count_v3_candidates 失敗：%s", exc)
        return {"error": str(exc)}


def estimate_reentry_detail(norad_id: int, days: float = 10.0, segmented: bool = True) -> dict[str, Any]:
    """單顆衛星再入估算，供離軌清單「詳細」按鈕使用：SGP4 逐圈外推近地點（零階，對照用）
    ＋ segmented=True 時附分段 M/A 校準結果（主要預測，即時計算約 2–6 s）。"""
    db = resolve_db()
    if db is None:
        return {"error": "資料庫不存在"}
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            actual = {r[0] for r in con.execute(f"DESCRIBE {settings.RAW_TABLE}").fetchall()}
            if "line1" not in actual or "line2" not in actual:
                return {"error": "本資料庫未保留 line1/line2，無法做 SGP4 外推"}
            row = con.execute(f"""
                SELECT line1, line2, epoch_utc
                FROM {settings.RAW_TABLE}
                WHERE norad_id = ? AND line1 IS NOT NULL AND line2 IS NOT NULL
                ORDER BY epoch_utc DESC LIMIT 1
            """, [norad_id]).fetchone()
        if row is None:
            return {"error": f"NORAD {norad_id} 查無可用 TLE"}
        line1, line2, _epoch_utc = row
        from .reentry_pass import reentry_estimate
        est = reentry_estimate(line1, line2, days=days)
        est["norad_id"] = norad_id
        est["name"] = _starlink_name(norad_id) or f"NORAD-{norad_id}"
        if segmented:
            from ..services.deorbit_forecast import forecast_norad
            seg = forecast_norad(norad_id)
            seg.pop("stats", None)
            for k in ("reentry_unix", "early_unix", "late_unix"):
                seg.pop(k, None)
            est["segmented"] = seg
        return est
    except Exception as exc:  # noqa: BLE001
        logger.warning("estimate_reentry_detail(%s) 失敗：%s", norad_id, exc)
        return {"error": str(exc)}
