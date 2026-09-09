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
  count_v3_candidates()       疑似 Starlink V3（新一代）部署數量之啟發式估計。
  estimate_reentry_detail()   單顆衛星之零階再入估算（SGP4 逐圈外推近地點）。
"""
from __future__ import annotations

import json
import logging
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


def list_deorbiting_starlinks(limit: int = 60) -> dict[str, Any]:
    """近期仍有 TLE、半長軸快速下降的 Starlink（離軌候選），依目前高度由低到高排序。

    「估計剩餘天數」為粗略線性外推（以近 7 天平均每日下降速率、外推目前高度歸零所需天數），
    未考慮阻力隨高度下降而指數增強的真實物理，因此對真正接近再入者會**低估**剩餘天數
    （即實際會比外推值更快發生）；如需較可靠的個別衛星估計，另呼叫
    estimate_reentry_detail() 用 SGP4 逐圈外推近地點高度。
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
                LIMIT {int(limit)}
            """
            rows = con.execute(sql).fetchdf()

            total_sql = f"""
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
                SELECT COUNT(*) FROM cur JOIN past30 USING (norad_id)
                WHERE cur.epoch_utc >= now() - INTERVAL {DEORBIT_FRESH_DAYS} DAY
                  AND (past30.alt_km_30d_ago - cur.alt_km) >= {DEORBIT_DA30_KM}
                  AND cur.alt_km < {DEORBIT_ALT_MAX_KM}
            """
            total_n = int(con.execute(total_sql).fetchone()[0])

        items = []
        for _, r in rows.iterrows():
            da7 = float(r["da_7d_km"])
            alt = float(r["alt_km"])
            daily_rate = da7 / 7.0  # 負值＝下降
            est_days = round(alt / abs(daily_rate), 1) if daily_rate < -0.05 else None
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
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "criteria": {
                "fresh_days": DEORBIT_FRESH_DAYS, "da30_km_min": DEORBIT_DA30_KM,
                "alt_km_max": DEORBIT_ALT_MAX_KM,
            },
            "total_matching": total_n,
            "shown": len(items),
            "items": items,
            "method": "est_days_to_reentry_rough 為近 7 天平均下降速率之線性外推歸零天數，"
                      "未計入阻力隨高度指數增強，對接近再入者會低估剩餘天數（實際更快）；"
                      "僅供排序參考，個別精確估計請用「詳細」按鈕（SGP4 逐圈外推近地點）。",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_deorbiting_starlinks 失敗：%s", exc)
        return {"error": str(exc)}


# ── Starlink V3 部署統計（啟發式，見 count_v3_candidates() 說明）──────────────
# V3 規格與部署時程為公開報導/申請文件之已知資訊（非本系統可獨立驗證），截至本檔案
# 撰寫時 Starship Flight 14（首次嘗試搭載約 20 顆 V3 進入正式軌道）尚未發射，目標日期
# 2026-09-15（SpaceX 未正式官宣，可能因天氣/技術/法規因素延後）。
V3_ERA_START = "2026-09-15"
# 已知規劃之初始部署殼層（km），各留 ±5 km 容許範圍
V3_ALT_BANDS_KM = [(323.0, 327.5), (473.0, 477.5)]
V3_ALT_TOLERANCE_KM = 5.0


def count_v3_candidates(era_start: str = V3_ERA_START) -> dict[str, Any]:
    """啟發式估計「疑似 Starlink V3」部署數量——即時查詢，非官方分類。

    Space-Track／CelesTrak 的公開目錄不會標記衛星的硬體世代（v1.0／v1.5／v2 Mini／V3），
    因此本函式只能用「公開已知的部署時程與初始入軌殼層」這兩個間接線索去猜：
      1. 該 NORAD 在本系統資料庫中最早出現的 TLE epoch ≥ era_start（V3 開始部署的已知目標日期）；
      2. 該筆最早 TLE 的高度落在已知規劃的初始部署殼層（323–327.5 km 或 473–477.5 km，±5 km）內。
    這是不精確的代理指標：無法排除同一時期剛好也在類似高度部署的其他世代衛星（若仍有的話），
    也可能因為早期軌道尚未穩定而誤判；隨著更多可靠的公開資料（例如官方確認的發射批次
    COSPAR ID 清單）出現，應該用那些取代這裡的高度啟發式。
    """
    db = resolve_db()
    if db is None:
        return {"error": "資料庫不存在"}
    ids = _starlink_norad_ids()
    if not ids:
        return {"error": "sat_metadata.csv 內找不到任何 Starlink 衛星（檔案缺失或分類規則異常）"}
    try:
        era_dt = datetime.fromisoformat(era_start).replace(tzinfo=timezone.utc)
    except ValueError:
        era_dt = datetime.fromisoformat(V3_ERA_START).replace(tzinfo=timezone.utc)

    band_sql = " OR ".join(
        f"(alt_km BETWEEN {lo - V3_ALT_TOLERANCE_KM} AND {hi + V3_ALT_TOLERANCE_KM})"
        for lo, hi in V3_ALT_BANDS_KM
    )
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            _register_starlink_ids(con, ids)
            sql = f"""
                WITH first_seen AS (
                    SELECT r.norad_id,
                           min(r.epoch_utc) AS first_epoch,
                           arg_min(r.sma_km, r.epoch_utc) - 6378.137 AS alt_km
                    FROM {settings.RAW_TABLE} r
                    JOIN starlink_ids s ON s.norad_id = r.norad_id
                    GROUP BY r.norad_id
                )
                SELECT norad_id, first_epoch, alt_km
                FROM first_seen
                WHERE first_epoch >= TIMESTAMP '{era_dt.strftime('%Y-%m-%d %H:%M:%S')}'
                  AND ({band_sql})
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
            "era_start": era_dt.date().isoformat(),
            "alt_bands_km": V3_ALT_BANDS_KM,
            "db_latest_epoch": latest.isoformat() if hasattr(latest, "isoformat") else str(latest),
            "candidate_count": len(items),
            "items": items,
            "known_schedule_note": "已知排程（公開報導/申請文件，非本系統可獨立驗證）：Starship Flight 14 "
                                   "目標於 2026-09-15 前後嘗試首次正式軌道飛行，預計搭載約 20 顆 Starlink V3 "
                                   "營運衛星進入 ~323–327.5 km 或 ~473–477.5 km 軌道層；SpaceX 尚未正式官宣確切"
                                   "日期，實際發射可能延後。V3 單顆約 2,500 kg（V2 Mini 約 800 kg）。",
            "method": "啟發式：以公開已知的部署時程（≥ era_start）與初始入軌殼層（±5 km 容許）"
                      "間接推測，Space-Track/CelesTrak 目錄本身不含硬體世代標記，故本統計非官方分類，"
                      "僅供追蹤部署進度參考。",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("count_v3_candidates 失敗：%s", exc)
        return {"error": str(exc)}


def estimate_reentry_detail(norad_id: int, days: float = 10.0) -> dict[str, Any]:
    """單顆衛星之零階再入估算（SGP4 逐圈外推近地點高度），供離軌清單「詳細」按鈕使用。"""
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
        return est
    except Exception as exc:  # noqa: BLE001
        logger.warning("estimate_reentry_detail(%s) 失敗：%s", norad_id, exc)
        return {"error": str(exc)}
