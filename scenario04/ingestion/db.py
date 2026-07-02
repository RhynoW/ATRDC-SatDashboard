"""DuckDB 存取層（Phase 1.2）：DB 解析、TLE 查詢 SQL、TLE 回寫、DB 資訊。"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

from .. import cache
from ..config import settings

logger = logging.getLogger(__name__)

RAW_TABLE  = settings.RAW_TABLE
META_TABLE = settings.META_TABLE


def resolve_db() -> Path | None:
    """依序解析資料庫位置：
    1. DB_PATH（預設 scenario04/DB/space_db.duckdb，可用環境變數覆寫）
    2. 同目錄的 full/slim 替代檔名
    3. 舊位置（專案根目錄）的 full/slim
    """
    if settings.DB_PATH.exists():
        return settings.DB_PATH
    alt_name = ("space_db_slim.duckdb"
                if settings.DB_PATH.name == "space_db.duckdb" else "space_db.duckdb")
    alt = settings.DB_PATH.parent / alt_name
    if alt.exists():
        logger.warning("DB %s 不存在，改用 %s", settings.DB_PATH.name, alt.name)
        return alt
    for legacy in (settings.LEGACY_DB_DIR / settings.DB_PATH.name,
                   settings.LEGACY_DB_DIR / alt_name):
        if legacy.exists():
            logger.warning("DB 目錄 %s 無資料庫，回退舊位置 %s",
                           settings.DB_PATH.parent, legacy)
            return legacy
    logger.error("找不到資料庫: %s（含舊位置 %s）",
                 settings.DB_PATH, settings.LEGACY_DB_DIR)
    return None


def tle_select_sql(con: duckdb.DuckDBPyConnection, extra_where: str = "") -> str:
    """動態偵測 raw_tle_archive 欄位，生成相容 slim/full DB 的查詢 SQL。"""
    actual = {r[0] for r in con.execute(f"DESCRIBE {RAW_TABLE}").fetchall()}
    obj_expr = (
        "COALESCE(r.object_name, 'NORAD-' || CAST(r.norad_id AS VARCHAR))"
        if "object_name" in actual
        else "'NORAD-' || CAST(r.norad_id AS VARCHAR)"
    )
    has_lines = "line1" in actual and "line2" in actual
    line_sel   = "r.line1, r.line2," if has_lines else "NULL AS line1, NULL AS line2,"
    line_where = "r.line1 IS NOT NULL AND r.line2 IS NOT NULL" if has_lines else "1=1"

    parts = [line_where]
    if extra_where:
        parts.append(extra_where)

    return f"""
        SELECT
            r.norad_id,
            {obj_expr} AS object_name,
            {line_sel}
            m.source_code, m.launch_date, m.intl_code
        FROM {RAW_TABLE} r
        LEFT JOIN {META_TABLE} m ON r.norad_id = m.norad_id
        WHERE {" AND ".join(parts)}
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY r.norad_id ORDER BY r.epoch_utc DESC
        ) = 1
    """


def parse_tle_epoch(epoch_str: str) -> datetime:
    """解析 TLE line1 中兩位數年 + day-of-year 格式的 epoch。"""
    epoch_str = epoch_str.strip()
    yy = int(epoch_str[:2])
    year = 2000 + yy if yy < 57 else 1900 + yy
    day_frac = float(epoch_str[2:])
    day_int = int(day_frac)
    frac = day_frac - day_int
    return (datetime(year, 1, 1, tzinfo=timezone.utc)
            + timedelta(days=day_int - 1 + frac))


def upsert_tle_to_db(tles: dict[int, dict]) -> None:
    """將從 Space-Track 取得的 TLE 寫回 raw_tle_archive（先刪後插）。"""
    if not tles:
        return
    db = resolve_db()
    if db is None:
        return
    try:
        with duckdb.connect(str(db)) as con:
            actual = {r[0] for r in con.execute(f"DESCRIBE {RAW_TABLE}").fetchall()}
            if "line1" not in actual or "line2" not in actual:
                logger.warning("upsert_tle_to_db: 資料庫無 line1/line2 欄位，跳過")
                return
            written = 0
            for nid, tle in tles.items():
                l1 = (tle.get("line1") or "").strip()
                l2 = (tle.get("line2") or "").strip()
                if not l1 or not l2:
                    continue
                epoch_utc: datetime
                epoch_iso = tle.get("epoch", "")
                if epoch_iso:
                    try:
                        ep = datetime.fromisoformat(str(epoch_iso).replace("Z", "+00:00"))
                        epoch_utc = ep if ep.tzinfo else ep.replace(tzinfo=timezone.utc)
                    except ValueError:
                        epoch_utc = datetime.now(timezone.utc)
                else:
                    try:
                        epoch_utc = parse_tle_epoch(l1[18:32])
                    except Exception:
                        epoch_utc = datetime.now(timezone.utc)
                con.execute(
                    f"DELETE FROM {RAW_TABLE} WHERE norad_id = ? AND epoch_utc = ?",
                    [nid, epoch_utc],
                )
                con.execute(
                    f"INSERT INTO {RAW_TABLE} (norad_id, epoch_utc, line1, line2)"
                    " VALUES (?, ?, ?, ?)",
                    [nid, epoch_utc, l1, l2],
                )
                written += 1
        logger.info("upsert_tle_to_db: 寫入 %d 筆 TLE", written)
    except Exception as exc:
        logger.warning("upsert_tle_to_db 失敗: %s", exc)


# ── DB 資訊（TTL 快取）────────────────────────────────────────────────────────
_db_info_cache:     dict[str, Any] = {}
_db_info_loaded_at: float = 0.0


def get_db_info() -> dict[str, Any]:
    global _db_info_cache, _db_info_loaded_at
    cached = cache.cache_get("db_info")
    if cached:
        return cached
    if _db_info_cache and (time.monotonic() - _db_info_loaded_at) < settings.DB_INFO_TTL:
        return _db_info_cache

    db = resolve_db()
    if db is None:
        return {"error": "資料庫不存在"}

    try:
        mtime_ts = db.stat().st_mtime
        db_updated_at = datetime.fromtimestamp(mtime_ts, tz=timezone.utc).isoformat()
        db_size_mb = round(db.stat().st_size / 1024**2, 1)

        with duckdb.connect(str(db), read_only=True) as con:
            actual_cols = {r[0] for r in con.execute(f"DESCRIBE {RAW_TABLE}").fetchall()}
            has_lines = "line1" in actual_cols and "line2" in actual_cols

            where = "WHERE line1 IS NOT NULL AND line2 IS NOT NULL" if has_lines else ""
            row = con.execute(f"""
                SELECT
                    COUNT(*)                 AS total_records,
                    COUNT(DISTINCT norad_id) AS valid_sat_count,
                    MIN(epoch_utc)           AS epoch_min,
                    MAX(epoch_utc)           AS epoch_max
                FROM {RAW_TABLE} {where}
            """).fetchone()

        def _iso(v: Any) -> str | None:
            if v is None:
                return None
            if hasattr(v, "isoformat"):
                return v.isoformat()
            return str(v)

        result: dict[str, Any] = {
            "db_name":         db.name,
            "db_size_mb":      db_size_mb,
            "db_updated_at":   db_updated_at,
            "has_tle_lines":   has_lines,
            "total_records":   int(row[0]) if row and row[0] else 0,
            "valid_sat_count": int(row[1]) if row and row[1] else 0,
            "epoch_min":       _iso(row[2]) if row else None,
            "epoch_max":       _iso(row[3]) if row else None,
        }
    except Exception as exc:
        result = {"error": str(exc), "db_name": db.name}

    _db_info_cache = result
    _db_info_loaded_at = time.monotonic()
    cache.cache_set("db_info", result, ttl=settings.DB_INFO_TTL)
    return result
