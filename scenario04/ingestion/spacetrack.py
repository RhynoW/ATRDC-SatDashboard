"""Space-Track 整合（可選）。

spacetrack_client.py 位於專案根目錄（BASE_DIR）；此模組負責 sys.path
處理與缺件降級：未安裝或未配置帳密時 ST_ENABLED=False，呼叫端自行跳過。
"""
from __future__ import annotations

import logging
import sys

from ..config import settings

logger = logging.getLogger(__name__)

if str(settings.BASE_DIR) not in sys.path:
    sys.path.insert(0, str(settings.BASE_DIR))

HAS_SPACETRACK = False
try:
    from spacetrack_client import (          # noqa: F401
        fetch_latest_tle_batch,
        fetch_cdm_for_satellite,
        fetch_cdm_batch,
        fetch_decay_prediction,
        fetch_satcat_info,
    )
    HAS_SPACETRACK = True
except ImportError:
    fetch_latest_tle_batch = None   # type: ignore[assignment]
    fetch_cdm_for_satellite = None  # type: ignore[assignment]
    fetch_cdm_batch = None          # type: ignore[assignment]
    fetch_decay_prediction = None   # type: ignore[assignment]
    fetch_satcat_info = None        # type: ignore[assignment]

ST_ENABLED = (
    HAS_SPACETRACK
    and bool(settings.SPACETRACK_USER)
    and bool(settings.SPACETRACK_PASS)
)

if not ST_ENABLED:
    logger.info("Space-Track 未啟用（模組=%s, 帳密=%s）",
                HAS_SPACETRACK, bool(settings.SPACETRACK_USER))
