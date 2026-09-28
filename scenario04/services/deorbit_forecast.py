"""離軌 Starlink 再入預測之背景批次（分段 M/A 校準，physics.segmented_ma）。

清單頁不可在請求中對數十顆同步數值積分（每顆 2–6 s），故由背景執行緒每 INTERVAL_S 秒
重算一次清單上各衛星，結果原子寫入 settings.DB_DIR/deorbit_forecast.json；API 只讀快取。
「詳細」按鈕則以 forecast_norad() 即時計算單顆。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from ..config import settings

logger = logging.getLogger(__name__)

CACHE_FILE: Path = settings.DB_DIR / "deorbit_forecast.json"
INTERVAL_S = 6 * 3600
START_DELAY_S = 90            # 等 DB／索引就緒
CHECK_S = 1800
TLE_LOOKBACK_DAYS = 12
LIST_LIMIT = 100
WIDEN_FRAC = 0.25             # 區間至少 ±25% × 剩餘時數（38 顆 TIP 回測調得，待樣本外驗證）
SEG_KW = {"n_segments": 4, "verify": False, "max_days": 10.0, "stop_alt_km": 80.0}


def _iso(unix: float | None) -> str | None:
    if unix is None:
        return None
    return datetime.fromtimestamp(unix, tz=timezone.utc).isoformat(timespec="minutes").replace("+00:00", "Z")


def widen_window(res: dict[str, Any], frac: float = WIDEN_FRAC) -> dict[str, Any]:
    """區間取「彈道係數範圍」與「±frac × 距最後 TLE 之剩餘時數」兩者較寬者。"""
    tn, te, tl = res.get("reentry_unix"), res.get("early_unix"), res.get("late_unix")
    hrs = res.get("hours_from_last_tle")
    if tn is None or hrs is None:
        return res
    pad = frac * hrs * 3600.0
    te2 = min(te if te is not None else tn, tn - pad)
    tl2 = max(tl if tl is not None else tn, tn + pad)
    out = dict(res)
    out["window_early_utc"], out["window_late_utc"] = _iso(te2), _iso(tl2)
    out["early_unix"], out["late_unix"] = te2, tl2
    out["window_rule"] = f"max(B 範圍, ±{int(frac * 100)}% × 剩餘時數)"
    return out


def _recent_rows(con: duckdb.DuckDBPyConnection, norad_id: int) -> list[tuple[str, str]]:
    return con.execute(f"""
        SELECT line1, line2 FROM {settings.RAW_TABLE}
        WHERE norad_id = ? AND line1 IS NOT NULL AND line2 IS NOT NULL
          AND epoch_utc <= now()
          AND epoch_utc >= (SELECT max(epoch_utc) FROM {settings.RAW_TABLE}
                            WHERE norad_id = ? AND epoch_utc <= now()) - INTERVAL {TLE_LOOKBACK_DAYS} DAY
        ORDER BY epoch_utc
    """, [norad_id, norad_id]).fetchall()


def _compact(res: dict[str, Any]) -> dict[str, Any]:
    """清單用精簡欄位（逐段序列只留 B 與旗標，供前端小圖）。"""
    if res.get("error"):
        return {"error": res["error"]}
    return {
        "reentry_utc": res.get("reentry_utc"),
        "window_early_utc": res.get("window_early_utc"),
        "window_late_utc": res.get("window_late_utc"),
        "hours_from_last_tle": res.get("hours_from_last_tle"),
        "beyond_horizon": res.get("beyond_horizon"),
        "reliable": res.get("reliable"),
        "thrust_suspected": res.get("thrust_suspected"),
        "latest_segment_flag": res.get("latest_segment_flag"),
        "tle_last_epoch": res.get("tle_last_epoch"),
        "A_eff_used_m2": res.get("A_eff_used_m2"),
        "segments": [{"t": s.get("t_end") or s.get("t_start"), "B": s.get("B"), "flag": s.get("thrust_flag")}
                     for s in res.get("segments", [])],
        "space_weather_persisted_from": (res.get("space_weather") or {}).get("persisted_from"),
        "timing_s": (res.get("timing_s") or {}).get("total"),
    }


def forecast_norad(norad_id: int, con: duckdb.DuckDBPyConnection | None = None) -> dict[str, Any]:
    """單顆即時計算（完整結果，含逐段細節）。"""
    from ..ingestion.db import ensure_space_weather, resolve_db
    from ..physics.segmented_ma import predict_reentry
    ensure_space_weather()
    own = con is None
    if own:
        db = resolve_db()
        if db is None:
            return {"error": "資料庫不存在"}
        con = duckdb.connect(str(db), read_only=True)
    try:
        rows = _recent_rows(con, int(norad_id))
    finally:
        if own:
            con.close()
    if len(rows) < 2:
        return {"error": f"NORAD {norad_id} 近 {TLE_LOOKBACK_DAYS} 天可用 TLE 少於 2 筆"}
    try:
        return widen_window(predict_reentry(rows, **SEG_KW))
    except Exception as exc:  # noqa: BLE001
        logger.warning("分段校準 %s 失敗：%s", norad_id, exc)
        return {"error": str(exc)}


def compute_all(limit: int = LIST_LIMIT) -> dict[str, Any]:
    from ..ingestion.db import resolve_db
    from ..physics.starlink_census import list_deorbiting_starlinks
    t0 = time.time()
    lst = list_deorbiting_starlinks(limit=limit)
    if lst.get("error"):
        return {"error": lst["error"], "generated_at": datetime.now(timezone.utc).isoformat()}
    db = resolve_db()
    out: dict[str, Any] = {}
    with duckdb.connect(str(db), read_only=True) as con:
        for it in lst.get("items", []):
            nid = int(it["norad_id"])
            out[str(nid)] = _compact(forecast_norad(nid, con))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_s": round(time.time() - t0, 1),
        "n": len(out),
        "method": "segmented_ma_v1（Remis 2026 分段 M/A 校準；兩體+J2–J4+NRLMSISE-00，RK4）",
        "window_rule": f"max(B 範圍, ±{int(WIDEN_FRAC * 100)}% × 剩餘時數)",
        "forecasts": out,
    }


def _write_atomic(data: dict[str, Any], path: Path = CACHE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class DeorbitForecastService:
    def __init__(self) -> None:
        self._data: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False

    def get(self) -> dict[str, Any] | None:
        with self._lock:
            if self._data is not None:
                return self._data
        if CACHE_FILE.exists():
            try:
                data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
                with self._lock:
                    self._data = data
                return data
            except Exception as exc:  # noqa: BLE001
                logger.warning("讀取離軌預測快取失敗：%s", exc)
        return None

    def age_s(self) -> float | None:
        cached = self.get()
        try:
            gen = datetime.fromisoformat(cached["generated_at"]) if cached else None
        except (KeyError, ValueError):
            gen = None
        return None if gen is None else (datetime.now(timezone.utc) - gen).total_seconds()

    def refresh(self) -> dict[str, Any]:
        data = compute_all()
        if not data.get("error"):
            _write_atomic(data)
            with self._lock:
                self._data = data
            logger.info("離軌預測批次完成：%d 顆，%.0f s", data["n"], data["elapsed_s"])
        else:
            logger.warning("離軌預測批次失敗：%s", data["error"])
        return data

    def start(self, interval_s: int = INTERVAL_S, delay_s: int = START_DELAY_S) -> None:
        if self._running:
            return
        self._running = True

        def _loop() -> None:
            time.sleep(delay_s)
            while self._running:
                if self.age_s() is None or self.age_s() >= interval_s:
                    try:
                        self.refresh()
                    except Exception:  # noqa: BLE001
                        logger.exception("離軌預測批次例外")
                time.sleep(CHECK_S)

        self._thread = threading.Thread(target=_loop, name="deorbit_forecast", daemon=True)
        self._thread.start()


deorbit_forecast_service = DeorbitForecastService()
