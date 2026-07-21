"""背景傳播快取（慢層）：每 SLOW_INTERVAL 秒在獨立執行緒重算全星座位置。

設計原則
--------
- API 端點只讀快取，不在請求路徑內執行 SGP4（避免高頻請求觸發重計算）。
- 快取過期（>120 s）或尚未就緒時，coords 端點自動退回即時傳播（fallback）。
- 快速端點（/api/positions/active, ≤100 顆）不使用此快取，直接即時計算（<5 ms）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

SLOW_INTERVAL = 60.0   # 全星座重算間隔（秒）；可由呼叫端覆寫


class PropagatorCache:
    def __init__(self) -> None:
        self._cache: dict[int, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._computed_at: float = 0.0
        self._get_idx: Callable | None = None
        self._interval: float = SLOW_INTERVAL
        self._worker: threading.Thread | None = None
        self._running = False

    # ── 生命週期 ────────────────────────────────────────────────────────────

    def start(self, get_idx_fn: Callable, interval: float = SLOW_INTERVAL) -> None:
        """啟動背景執行緒（可重入：已在執行則直接返回）。"""
        if self._worker and self._worker.is_alive():
            return
        self._get_idx = get_idx_fn
        self._interval = interval
        self._running = True
        self._worker = threading.Thread(
            target=self._loop, name="propagator-cache", daemon=True
        )
        self._worker.start()
        logger.info("PropagatorCache 已啟動（慢層每 %.0f s 全星座重算）", interval)

    def stop(self) -> None:
        self._running = False

    # ── 背景執行緒 ──────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                self._recompute()
            except Exception as exc:
                logger.error("PropagatorCache._recompute 失敗: %s", exc)
            time.sleep(self._interval)

    def _recompute(self) -> None:
        from .propagate import propagate_batch   # 延遲匯入避免循環依賴

        idx = (self._get_idx or (lambda: {}))()
        if not idx:
            return

        nids = list(idx.keys())
        t0 = time.monotonic()
        positions = propagate_batch(nids, idx)
        elapsed = time.monotonic() - t0

        fresh: dict[int, dict[str, Any]] = {}
        for nid, pos in zip(nids, positions):
            if pos is not None:
                lat, lon, alt = pos
                fresh[nid] = {
                    "lat":    round(lat, 4),
                    "lon":    round(lon, 4),
                    "alt_km": round(alt, 1),
                }

        with self._lock:
            self._cache = fresh
            self._computed_at = time.monotonic()

        logger.info(
            "PropagatorCache: %d 顆重算完成，耗時 %.2f s", len(fresh), elapsed
        )

    # ── 讀取介面 ────────────────────────────────────────────────────────────

    def get_snapshot(self, norad_ids: list[int]) -> dict[int, dict[str, Any]]:
        """回傳指定 NORAD ID 的最新快取位置（執行緒安全）。"""
        with self._lock:
            return {nid: self._cache[nid] for nid in norad_ids if nid in self._cache}

    @property
    def ready(self) -> bool:
        """快取至少有一次成功重算。"""
        with self._lock:
            return bool(self._cache)

    @property
    def age_seconds(self) -> float:
        """距離上次重算的秒數；尚未重算回傳 inf。"""
        ct = self._computed_at
        return (time.monotonic() - ct) if ct else float("inf")

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)


# ── 全域單例 ────────────────────────────────────────────────────────────────
_cache = PropagatorCache()


def get_cache() -> PropagatorCache:
    """取得全域 PropagatorCache 單例。"""
    return _cache
