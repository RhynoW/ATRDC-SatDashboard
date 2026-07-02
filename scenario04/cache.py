"""可選 Redis 快取後端（多容器/K8s 環境下跨 Pod 共享快取）。

Redis 未配置或不可用時，cache_get() 一律回 None、cache_set() 靜默略過，
呼叫端自行退回 process-level 記憶體快取。
"""
from __future__ import annotations

import json
import logging

from .config import settings

logger = logging.getLogger(__name__)

_USE_REDIS    = False
_redis_client = None

if settings.REDIS_URL:
    try:
        import redis as _redis_lib
        _redis_client = _redis_lib.from_url(
            settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        _redis_client.ping()
        _USE_REDIS = True
        logger.info("Redis 快取已啟用: %s", settings.REDIS_URL)
    except Exception as _redis_exc:
        logger.warning("Redis 不可用，退回 process-level 記憶體快取: %s", _redis_exc)
        _redis_client = None


def cache_get(key: str) -> dict | None:
    """從 Redis（若啟用）取快取；Redis 不可用時直接回傳 None。"""
    if _USE_REDIS and _redis_client:
        try:
            raw = _redis_client.get(key)
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.debug("Redis get 失敗（%s）: %s", key, exc)
    return None


def cache_set(key: str, value: dict, ttl: int = settings.STATS_TTL) -> None:
    """寫入 Redis（若啟用）；失敗時靜默降級。"""
    if _USE_REDIS and _redis_client:
        try:
            _redis_client.setex(key, ttl, json.dumps(value, default=str))
        except Exception as exc:
            logger.debug("Redis set 失敗（%s）: %s", key, exc)
