"""衛星索引與統計（Phase 1.2）：現行索引、歷史索引、統計快取。"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import duckdb

from .. import cache
from ..config import settings
from . import spacetrack
from .db import resolve_db, tle_select_sql, upsert_tle_to_db
from .metadata import (
    classify_constellation,
    classify_country,
    classify_era,
    classify_purpose,
    load_sat_metadata_csv,
)
from .user_defined import load_user_catalogue, load_user_tles

logger = logging.getLogger(__name__)

_stats_cache:             dict[str, Any] = {}
_stats_loaded_at:         float = 0.0
_stats_payload_cache:     dict[str, Any] = {}
_stats_payload_loaded_at: float = 0.0
_sat_index:               dict[int, dict[str, Any]] = {}
_index_loaded_at:         float = 0.0


def _rows_to_index(rows: list[tuple]) -> dict[int, dict[str, Any]]:
    """DB 查詢列 + sat_metadata.csv 覆寫 → 衛星索引 dict。"""
    csv_meta = load_sat_metadata_csv()
    idx: dict[int, dict[str, Any]] = {}
    for norad_id, raw_name, l1, l2, db_src, db_launch, db_intl in rows:
        nid  = int(norad_id)
        name = (raw_name or "").strip().lstrip("0 ") or f"OBJECT {nid}"
        ov   = csv_meta.get(nid, {})
        final_name = ov.get("name_en")     or name
        final_src  = ov.get("source_code") or db_src
        final_intl = ov.get("intl_code")   or db_intl
        csv_date_str = ov.get("launch_date", "")
        if csv_date_str:
            try:
                final_launch: datetime | None = datetime.strptime(
                    csv_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                final_launch = db_launch
        else:
            final_launch = db_launch
        idx[nid] = {
            "name":          final_name,
            "line1":         l1.strip() if l1 else "",
            "line2":         l2.strip() if l2 else "",
            "country":       classify_country(final_src),
            "purpose":       ov.get("purpose") or classify_purpose(final_name),
            "era":           classify_era(final_launch, final_intl),
            "constellation": ov.get("constellation") or classify_constellation(final_name),
        }
    return idx


def _merge_user_defined(idx: dict[int, dict[str, Any]]) -> None:
    """把使用者自訂 TLE 與衛星目錄併入索引（就地修改）。

    - user TLE：NORAD 已存在 → 覆蓋 line1/line2（與名稱，若有）；
                不存在 → 新增條目。條目標記 user_defined=True。
    - user catalogue：覆寫名稱（中文優先）/國家/用途/星座/年代等中繼資料。
    """
    user_tles = load_user_tles()
    for nid, tle in user_tles.items():
        entry = idx.get(nid)
        if entry is None:
            name = tle["name"] or f"USER-{nid}"
            entry = {
                "name":          name,
                "line1":         tle["line1"],
                "line2":         tle["line2"],
                "country":       "不明",
                "purpose":       classify_purpose(name),
                "era":           "不明",
                "constellation": classify_constellation(name),
            }
            idx[nid] = entry
        else:
            entry["line1"] = tle["line1"]
            entry["line2"] = tle["line2"]
            if tle["name"]:
                entry["name"] = tle["name"]
        entry["user_defined"] = True
        entry["user_tle_file"] = tle["source_file"]

    for nid, row in load_user_catalogue().items():
        entry = idx.get(nid)
        if entry is None:
            # 目錄有、但既無 DB TLE 也無 user TLE → 無法傳播定位，僅記錄
            logger.warning("user catalogue: NORAD %d 無 TLE 可用，僅目錄資料（不顯示）", nid)
            continue
        name_zh = row.get("name_zh", "")
        name_en = row.get("name_en", "")
        if name_zh or name_en:
            entry["name"] = name_zh or name_en
        if name_zh:
            entry["name_zh"] = name_zh
        if name_en:
            entry["name_en"] = name_en
        if row.get("country"):
            entry["country"] = row["country"]
        if row.get("purpose"):
            entry["purpose"] = row["purpose"]
        if row.get("constellation"):
            entry["constellation"] = row["constellation"]
        if row.get("operator"):
            entry["operator"] = row["operator"]
        if row.get("notes"):
            entry["notes"] = row["notes"]
        launch_date = None
        if row.get("launch_date"):
            try:
                launch_date = datetime.strptime(
                    row["launch_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                logger.warning("user catalogue: NORAD %d launch_date 格式應為 YYYY-MM-DD: %r",
                               nid, row["launch_date"])
        if launch_date or row.get("intl_code"):
            entry["era"] = classify_era(launch_date, row.get("intl_code") or None)
        entry["user_catalogue"] = True


def invalidate_index() -> None:
    """強制下次 get_sat_index() 重建（使用者自訂檔變更後由 admin API 呼叫）。"""
    global _sat_index, _index_loaded_at, _stats_cache, _stats_loaded_at
    global _stats_payload_cache, _stats_payload_loaded_at
    _sat_index = {}
    _index_loaded_at = 0.0
    _stats_cache = {}
    _stats_loaded_at = 0.0
    _stats_payload_cache = {}
    _stats_payload_loaded_at = 0.0


def build_sat_index() -> dict[int, dict[str, Any]]:
    db = resolve_db()
    if db is None:
        idx: dict[int, dict[str, Any]] = {}
        _merge_user_defined(idx)   # DB 缺失時使用者自訂 TLE 仍可顯示
        return idx
    logger.info("建立衛星索引中…")
    t0 = time.monotonic()
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            rows = con.execute(tle_select_sql(con)).fetchall()
    except Exception as exc:
        logger.error("建立索引失敗: %s", exc)
        idx = {}
        _merge_user_defined(idx)
        return idx

    idx = _rows_to_index(rows)
    elapsed = time.monotonic() - t0
    logger.info("衛星索引完成: %d 筆，耗時 %.1f s", len(idx), elapsed)

    # Space-Track 補抓缺漏 TLE
    if spacetrack.ST_ENABLED:
        missing = {nid: info["name"] for nid, info in idx.items()
                   if not info.get("line1") or not info.get("line2")}
        if missing:
            logger.info("Space-Track 補抓 %d 顆缺失 TLE …", len(missing))
            try:
                fetched = spacetrack.fetch_latest_tle_batch(list(missing.keys()), missing)
                for nid, tle in fetched.items():
                    if nid in idx:
                        idx[nid]["line1"] = tle["line1"]
                        idx[nid]["line2"] = tle["line2"]
                if fetched:
                    upsert_tle_to_db(fetched)
                    logger.info("Space-Track TLE 補抓成功 %d 顆", len(fetched))
            except Exception as exc:
                logger.warning("Space-Track TLE 補抓失敗: %s", exc)

    _merge_user_defined(idx)
    return idx


def get_sat_index() -> dict[int, dict[str, Any]]:
    global _sat_index, _index_loaded_at
    if not _sat_index or (time.monotonic() - _index_loaded_at) > settings.INDEX_TTL:
        _sat_index = build_sat_index()
        _index_loaded_at = time.monotonic()
    return _sat_index


def get_index_for_time(ts: datetime) -> dict[int, dict[str, Any]]:
    """
    回傳適用於 ts 時刻的衛星 TLE 索引。
    - ts >= now-1h  → 直接用現行索引（最快）
    - ts 在過去     → 查詢 DB 中 epoch_utc <= ts 的最新 TLE
    """
    now = datetime.now(timezone.utc)
    if ts >= now - timedelta(hours=1):
        return get_sat_index()

    db = resolve_db()
    if db is None:
        return get_sat_index()

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")

    logger.info("歷史 TLE 查詢：%s", ts_str)
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            sql = tle_select_sql(con, extra_where=f"r.epoch_utc <= TIMESTAMP '{ts_str}'")
            rows = con.execute(sql).fetchall()
    except Exception as exc:
        logger.warning("歷史 TLE 查詢失敗 (%s)，退回現行索引: %s", ts_str, exc)
        return get_sat_index()

    if not rows:
        logger.warning("歷史 TLE 查詢無結果 (%s)", ts_str)
        return get_sat_index()

    idx = _rows_to_index(rows)
    logger.info("歷史 TLE 索引完成: %d 筆 (%s)", len(idx), ts_str)
    return idx


# ── 統計 ─────────────────────────────────────────────────────────────────────

def build_stats(idx: dict[int, dict[str, Any]], *, payload_only: bool = False) -> dict[str, Any]:
    EXCLUDE = {"碎片", "火箭體"} if payload_only else set()
    country: dict[str, int] = {}
    purpose: dict[str, int] = {}
    era:     dict[str, int] = {}
    constel: dict[str, int] = {}
    for info in idx.values():
        if info["purpose"] in EXCLUDE:
            continue
        country[info["country"]] = country.get(info["country"], 0) + 1
        purpose[info["purpose"]] = purpose.get(info["purpose"], 0) + 1
        era[info["era"]]         = era.get(info["era"], 0) + 1
        c = info["constellation"] or "其他衛星"
        constel[c] = constel.get(c, 0) + 1

    def _sorted(d: dict[str, int]) -> list[dict]:
        return [{"label": k, "count": v} for k, v in sorted(d.items(), key=lambda x: -x[1])]

    era_order = ["< 1 年", "1–5 年", "5–10 年", "> 10 年", "不明"]
    era_sorted = sorted(era.items(),
                        key=lambda x: era_order.index(x[0]) if x[0] in era_order else 99)
    return {
        "total":         len(idx),
        "country":       _sorted(country),
        "purpose":       _sorted(purpose),
        "era":           [{"label": k, "count": v} for k, v in era_sorted],
        "constellation": _sorted(constel),
        "updated_at":    datetime.now(timezone.utc).isoformat(),
    }


def get_stats(*, payload_only: bool = False) -> dict[str, Any]:
    global _stats_cache, _stats_loaded_at, _stats_payload_cache, _stats_payload_loaded_at
    cache_key = "stats_payload" if payload_only else "stats"
    cached = cache.cache_get(cache_key)
    if cached:
        return cached
    need_rebuild = (
        payload_only and (not _stats_payload_cache or
                          (time.monotonic() - _stats_payload_loaded_at) > settings.STATS_TTL)
    ) or (
        not payload_only and (not _stats_cache or
                              (time.monotonic() - _stats_loaded_at) > settings.STATS_TTL)
    )
    if need_rebuild:
        idx = get_sat_index()
        result = build_stats(idx, payload_only=payload_only)
        if payload_only:
            _stats_payload_cache = result
            _stats_payload_loaded_at = time.monotonic()
        else:
            _stats_cache = result
            _stats_loaded_at = time.monotonic()
    else:
        result = _stats_payload_cache if payload_only else _stats_cache
    cache.cache_set(cache_key, result, ttl=settings.STATS_TTL)
    return result
