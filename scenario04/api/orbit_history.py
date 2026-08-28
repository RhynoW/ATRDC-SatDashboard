"""軌道要素歷史 API：供 /orbit 頁 Spiral Polar 與 SMA 圓形圖使用。

資料源（合併）：
  1. raw_tle_archive（slim DB；多數衛星僅近 ~10 天）
  2. orbit_cache.duckdb（本模組維護之逐日快取；DB/ 目錄已 gitignore）
  3. 覆蓋不足時按需向 Space-Track gp_history 補抓（需帳密；失敗則優雅降級，
     回傳既有資料並附 note）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, timedelta

import duckdb
from flask import Blueprint, request

from . import json_response
from ..config import settings
from ..ingestion.db import resolve_db
from ..ingestion.index import get_sat_index
from ..ingestion.user_defined import load_user_catalogue

bp = Blueprint("orbit_history_api", __name__)
logger = logging.getLogger(__name__)

_ELEMS = ("sma_km", "inclination_deg", "raan_deg", "argp_deg")
_CACHE_DB = settings.PACKAGE_DIR / "DB" / "orbit_cache.duckdb"
_FETCH_COOLDOWN_S = 600          # 同一 norad 補抓失敗後的冷卻
_COVERAGE_FETCH_THRESHOLD = 0.5  # 覆蓋率低於此值才嘗試補抓
_last_fetch: dict[int, float] = {}
_lock = threading.Lock()


def _st_credentials() -> tuple[str, str] | None:
    user = settings.SPACETRACK_USER or os.getenv("SPACE_TRACK_IDENTITY", "")
    pw = settings.SPACETRACK_PASS or os.getenv("SPACE_TRACK_PASSWORD", "")
    return (user, pw) if user and pw else None


def _cache_con() -> duckdb.DuckDBPyConnection:
    _CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(_CACHE_DB))
    con.execute("""
        CREATE TABLE IF NOT EXISTS orbit_daily (
            norad_id INTEGER, d DATE,
            sma_km DOUBLE, inclination_deg DOUBLE,
            raan_deg DOUBLE, argp_deg DOUBLE,
            PRIMARY KEY (norad_id, d))
    """)
    return con


def _resolve_history_db():
    """本地端優先讀完整 space_db.duckdb（專案根），無則退回 slim。

    優先序：
      1. 環境變數 ORBIT_HISTORY_DB
      2. BASE_DIR 之上層（主專案根）space_db.duckdb —— 本地開發環境
      3. resolve_db()（scenario04/DB 之 slim 快照）—— HF 部署環境
    """
    env = os.getenv("ORBIT_HISTORY_DB", "")
    # BASE_DIR 即主專案根（Sat_TraingDataExtension），完整 archive 在其下
    candidates = ([env] if env else []) + \
        [str(settings.BASE_DIR / "space_db.duckdb")]
    for c in candidates:
        p = settings.BASE_DIR / c if not os.path.isabs(c) else c
        if os.path.isfile(str(p)):
            return str(p)
    db = resolve_db()
    return str(db) if db else None


def _daily_from_archive(norad: int, start_d: date, end_d: date) -> dict[str, tuple]:
    db = _resolve_history_db()
    if db is None:
        return {}
    sql = f"""
        SELECT CAST(epoch_utc AS DATE) AS d, {", ".join(_ELEMS)}
        FROM (
            SELECT *, row_number() OVER (
                PARTITION BY CAST(epoch_utc AS DATE) ORDER BY epoch_utc DESC) AS rn
            FROM raw_tle_archive
            WHERE norad_id = ? AND epoch_utc >= ? AND epoch_utc < ?
        ) WHERE rn = 1 ORDER BY d
    """
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            rows = con.execute(
                sql, [norad, start_d.isoformat(), (end_d + timedelta(days=1)).isoformat()]
            ).fetchall()
    except duckdb.Error as exc:
        # 完整 DB 可能被其他程式鎖定 → 退回 slim
        logger.warning("開啟 %s 失敗（%s），退回 slim DB", db, exc)
        fallback = resolve_db()
        if fallback is None or str(fallback) == str(db):
            return {}
        with duckdb.connect(str(fallback), read_only=True) as con:
            rows = con.execute(
                sql, [norad, start_d.isoformat(), (end_d + timedelta(days=1)).isoformat()]
            ).fetchall()
    return {r[0].isoformat(): tuple(float(v) for v in r[1:]) for r in rows}


def _daily_from_cache(norad: int, start_d: date, end_d: date) -> dict[str, tuple]:
    with _cache_con() as con:
        rows = con.execute(
            f"SELECT d, {', '.join(_ELEMS)} FROM orbit_daily "
            "WHERE norad_id = ? AND d BETWEEN ? AND ? ORDER BY d",
            [norad, start_d.isoformat(), end_d.isoformat()]).fetchall()
    return {r[0].isoformat(): tuple(float(v) for v in r[1:]) for r in rows}


def _fetch_spacetrack(norad: int, start_d: date, end_d: date) -> dict[str, tuple] | str:
    """gp_history 補抓 → 逐日（每日最後一筆）→ 寫入快取。回傳 dict 或錯誤字串。"""
    creds = _st_credentials()
    if creds is None:
        return "Space-Track 帳密未設定，僅顯示本地資料"
    now = time.time()
    with _lock:
        if now - _last_fetch.get(norad, 0) < _FETCH_COOLDOWN_S:
            return "近期已嘗試補抓（冷卻中），僅顯示本地資料"
        _last_fetch[norad] = now

    import requests
    try:
        s = requests.Session()
        r = s.post("https://www.space-track.org/ajaxauth/login",
                   data={"identity": creds[0], "password": creds[1]}, timeout=30)
        r.raise_for_status()
        url = ("https://www.space-track.org/basicspacedata/query/class/gp_history"
               f"/NORAD_CAT_ID/{norad}"
               f"/EPOCH/{start_d.isoformat()}--{(end_d + timedelta(days=1)).isoformat()}"
               "/orderby/EPOCH asc/format/json")
        r = s.get(url, timeout=120)
        r.raise_for_status()
        recs = r.json()
    except Exception as exc:  # noqa: BLE001 — 網路錯誤一律降級
        logger.warning("Space-Track 補抓失敗 norad=%s: %s", norad, exc)
        return f"Space-Track 補抓失敗（{type(exc).__name__}），僅顯示本地資料"

    daily: dict[str, tuple] = {}
    for rec in recs:  # EPOCH 升冪 → 後者覆蓋前者 = 每日最後一筆
        try:
            d_key = rec["EPOCH"][:10]
            daily[d_key] = (float(rec["SEMIMAJOR_AXIS"]),
                            float(rec["INCLINATION"]),
                            float(rec["RA_OF_ASC_NODE"]),
                            float(rec["ARG_OF_PERICENTER"]))
        except (KeyError, TypeError, ValueError):
            continue
    if daily:
        with _cache_con() as con:
            con.executemany(
                "INSERT OR REPLACE INTO orbit_daily VALUES (?, ?, ?, ?, ?, ?)",
                [(norad, d, *v) for d, v in daily.items()])
    logger.info("Space-Track 補抓 norad=%s：%s 筆 → %s 日", norad, len(recs), len(daily))
    return daily


@bp.get("/api/orbit/history")
def orbit_history():
    """
    Query params:
        norad (int)  必填
        start (date) 預設 end − 365 天
        end   (date) 預設今日
    Returns: dates[] 與四要素逐日序列 + 統計 + coverage/note
    """
    try:
        norad = int(request.args.get("norad", ""))
    except ValueError:
        return json_response({"error": "norad 參數必填且需為整數"}), 400

    try:
        end_d = date.fromisoformat(request.args.get("end", "")) \
            if request.args.get("end") else date.today()
        start_d = date.fromisoformat(request.args.get("start", "")) \
            if request.args.get("start") else end_d - timedelta(days=365)
    except ValueError:
        return json_response({"error": "start/end 需為 YYYY-MM-DD"}), 400
    if start_d >= end_d:
        return json_response({"error": "start 需早於 end"}), 400

    # 合併：快取 ← archive 覆蓋（archive 為權威）
    daily = _daily_from_cache(norad, start_d, end_d)
    daily.update(_daily_from_archive(norad, start_d, end_d))

    span_days = (end_d - start_d).days + 1
    note = None
    need_fetch = len(daily) / span_days < _COVERAGE_FETCH_THRESHOLD
    if not need_fetch and len(daily) > 1:
        # 整體覆蓋率夠，但仍檢查內部空窗（≥7 天即補抓；曾因合併路徑造成假空窗）
        ds = sorted(date.fromisoformat(x) for x in daily)
        max_gap = max((b - a).days for a, b in zip(ds, ds[1:]))
        need_fetch = max_gap >= 7
    if need_fetch:
        fetched = _fetch_spacetrack(norad, start_d, end_d)
        if isinstance(fetched, str):
            note = fetched
        else:
            fetched.update(daily)   # archive/既有快取優先
            daily = fetched

    if not daily:
        return json_response(
            {"error": f"NORAD {norad} 於 {start_d} ~ {end_d} 無 TLE 資料"
                      + (f"；{note}" if note else ""),
             "norad": norad}), 404

    dates = sorted(daily)
    series = {e: [round(daily[d][i], 6) for d in dates] for i, e in enumerate(_ELEMS)}

    stats = {}
    for e in _ELEMS:
        v = series[e]
        n = len(v)
        mean = sum(v) / n
        std = (sum((x - mean) ** 2 for x in v) / n) ** 0.5 if n > 1 else 0.0
        stats[e] = {"min": round(min(v), 4), "max": round(max(v), 4),
                    "range": round(max(v) - min(v), 4),
                    "mean": round(mean, 4), "std": round(std, 5)}

    info = get_sat_index().get(norad, {})
    cat = load_user_catalogue().get(norad, {})
    l1 = info.get("line1", "")
    intl_raw = l1[9:17].strip() if len(l1) >= 17 else ""
    if intl_raw[:2].isdigit() and len(intl_raw) >= 5:
        century = "19" if int(intl_raw[:2]) >= 57 else "20"
        intl_raw = f"{century}{intl_raw[:2]}-{intl_raw[2:]}"
    sat_info = {
        "name": info.get("name", f"NORAD {norad}"),
        "name_zh": info.get("name_zh", ""),
        "name_en": info.get("name_en", ""),
        "country": info.get("country", ""),
        "purpose": info.get("purpose", ""),
        "constellation": info.get("constellation", ""),
        "era": info.get("era", ""),
        "operator": info.get("operator", ""),
        "launch_date": cat.get("launch_date", ""),
        "intl_code": cat.get("intl_code", "") or intl_raw,
        "notes": info.get("notes", ""),
    }
    return json_response({
        "norad": norad,
        "name": info.get("name", f"NORAD {norad}"),
        "info": sat_info,
        "start": start_d.isoformat(), "end": end_d.isoformat(),
        "n_days": len(dates),
        "coverage": round(len(dates) / span_days, 3),
        "note": note,
        "dates": dates,
        **series,
        "stats": stats,
    })
