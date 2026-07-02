"""過頂預報非同步服務（Phase 1.3）。

原始碼中的 _passes_executor / _passes_future_cache / _passes_result_cache /
_passes_lock 模組層級共享狀態，改以 PassesService 類別封裝：
大量衛星矩陣運算交由背景執行緒，避免阻塞 Flask main thread。
"""
from __future__ import annotations

import concurrent.futures
import threading
from typing import Any

from ..physics.coverage import predict_taipei_passes


class PassesService:
    """狀態機：submit → computing（HTTP 202）→ ready（結果快取，永久有效）。"""

    def __init__(self, max_workers: int = 2) -> None:
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="passes_worker")
        self._futures: dict[str, concurrent.futures.Future] = {}
        self._results: dict[str, dict] = {}
        self._lock = threading.Lock()

    @staticmethod
    def cache_key(hours: float, step_sec: float, mask_deg: float) -> str:
        return f"{hours:.1f}_{step_sec:.0f}_{mask_deg:.1f}"

    def get_or_submit(
        self, hours: float, step_sec: float, mask_deg: float,
    ) -> tuple[str, dict[str, Any] | None]:
        """回傳 (status, payload)：
        - ("ready", result)      結果可用（快取命中時 payload 含 from_cache=True）
        - ("computing", None)    運算已提交或進行中
        - ("error", {"error"})   背景運算拋出例外
        """
        key = self.cache_key(hours, step_sec, mask_deg)
        with self._lock:
            if key in self._results:
                data = dict(self._results[key])
                data["from_cache"] = True
                return "ready", data

            fut = self._futures.get(key)
            if fut is not None:
                if not fut.done():
                    return "computing", None
                try:
                    result = fut.result()
                    self._results[key] = result
                    del self._futures[key]
                    return "ready", result
                except Exception as exc:
                    del self._futures[key]
                    return "error", {"error": str(exc)}

            self._futures[key] = self._executor.submit(
                predict_taipei_passes, hours, step_sec, mask_deg)
        return "computing", None


passes_service = PassesService()
