"""Starlink V3 Flight 14：直接內插 SpaceX 官方 MEME 72 小時預估位置（TEME）。

不經過 SGP4/TLE：這批衛星在部署後 36-48 小時內開始執行變軌爬升，單次擬合的
SGP4 TLE 已無法可靠外推（見 2026-09-30 session 記錄，驗證誤差從 ~15 km 惡化到
500+ km）。改直接對 starlink_ephemeris/meme_to_teme_samples.py 寫入的原始
MEME 樣本點（已轉 TEME，見該模組）做線性內插，誠實標示為官方 72 小時預估，
而非本系統的軌道模型。

Table `v3_meme_teme_samples`（norad_id, name, t_utc, x_km, y_km, z_km）由
父倉庫的批次腳本產生後寫入同一個 slim DuckDB；找不到表格或請求時刻超出樣本
涵蓋範圍（表示資料已過期，需要重新下載/轉換）時，回傳 None，呼叫端應退回
既有 TLE 估測路徑。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from ..ingestion.db import resolve_db

logger = logging.getLogger(__name__)

TABLE = "v3_meme_teme_samples"
_CACHE_TTL_S = 300
_cache: tuple[float, dict[int, dict[str, np.ndarray]]] | None = None


def _load_samples() -> dict[int, dict[str, np.ndarray]]:
    """讀出整張表，依 norad_id 分組並依時間排序；快取 5 分鐘。"""
    global _cache
    now = time.monotonic()
    if _cache and (now - _cache[0]) < _CACHE_TTL_S:
        return _cache[1]

    import duckdb
    by_id: dict[int, dict[str, np.ndarray]] = {}
    db = resolve_db()
    if db is None:
        _cache = (now, by_id)
        return by_id
    try:
        con = duckdb.connect(str(db), read_only=True)
        try:
            tables = {r[0] for r in con.execute(
                "SELECT table_name FROM information_schema.tables").fetchall()}
            if TABLE not in tables:
                _cache = (now, by_id)
                return by_id
            df = con.execute(
                f"SELECT norad_id, t_utc, x_km, y_km, z_km FROM {TABLE} ORDER BY norad_id, t_utc"
            ).df()
        finally:
            con.close()
    except Exception:
        logger.warning("v3_meme_teme_samples 讀取失敗", exc_info=True)
        _cache = (now, by_id)
        return by_id

    for nid, g in df.groupby("norad_id"):
        t = g["t_utc"].to_numpy("datetime64[ns]").astype("int64") / 1e9  # unix seconds
        by_id[int(nid)] = {
            "t":   t,
            "xyz": g[["x_km", "y_km", "z_km"]].to_numpy(dtype=float),
        }
    _cache = (now, by_id)
    return by_id


def meme_positions_eci(nids: list[int], t: datetime) -> dict[int, np.ndarray]:
    """在時刻 t（UTC）線性內插每顆衛星的 TEME 位置（km）。
    超出該衛星樣本涵蓋範圍（資料過期，或請求時刻早於樣本起點）的一律略過，
    不做外推——這條路徑的價值就在於「誠實只涵蓋官方預估範圍」。
    """
    samples = _load_samples()
    if not samples:
        return {}
    t_unix = t.replace(tzinfo=timezone.utc).timestamp() if t.tzinfo is None else t.timestamp()

    out: dict[int, np.ndarray] = {}
    for nid in nids:
        s = samples.get(nid)
        if s is None:
            continue
        ts, xyz = s["t"], s["xyz"]
        if t_unix < ts[0] or t_unix > ts[-1]:
            continue
        x = np.interp(t_unix, ts, xyz[:, 0])
        y = np.interp(t_unix, ts, xyz[:, 1])
        z = np.interp(t_unix, ts, xyz[:, 2])
        out[nid] = np.array([x, y, z])
    return out


def coverage_info() -> dict[str, Any]:
    """供 API 回應揭露：這批資料實際涵蓋到什麼時候（供「已過期」判斷/顯示用）。"""
    samples = _load_samples()
    if not samples:
        return {"available": False}
    all_max = max(float(s["t"][-1]) for s in samples.values())
    all_min = min(float(s["t"][0]) for s in samples.values())
    return {
        "available": True,
        "satellites": len(samples),
        "valid_from": datetime.fromtimestamp(all_min, tz=timezone.utc).isoformat(timespec="seconds"),
        "valid_until": datetime.fromtimestamp(all_max, tz=timezone.utc).isoformat(timespec="seconds"),
    }
