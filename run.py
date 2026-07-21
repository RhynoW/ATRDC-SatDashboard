#!/usr/bin/env python3
"""scenario04 模組化版啟動入口 — Port 5013。

用法：
    cd scenario-advanced01
    python run.py

功能與原 scenario04-Cesium-advanced04.py 相同（向量化 SGP4 + 近距離掃描 +
搜尋 + 台北覆蓋 + 時間軸 + Space-Track），資料檔仍讀取上層專案根目錄。
"""
from __future__ import annotations

import logging
import os

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

from scenario04 import create_app                       # noqa: E402
from scenario04.config import settings                  # noqa: E402
from scenario04.ingestion.index import get_sat_index, get_stats  # noqa: E402
from scenario04.ingestion.manual_tle import ingest_manual_tles  # noqa: E402
from scenario04.physics.propagator_cache import get_cache        # noqa: E402
from scenario04.physics.conjunction import HAS_KDTREE   # noqa: E402
from scenario04.physics.propagate import HAS_SATREC_ARRAY  # noqa: E402

logger = logging.getLogger("scenario04.run")

app = create_app()

if __name__ == "__main__":
    logger.info(
        "Scenario 04（模組化版）啟動 — http://%s:%d  台北覆蓋(時間軸): http://%s:%d/taipei",
        settings.HOST, settings.PORT, settings.HOST, settings.PORT,
    )
    logger.info(
        "SatrecArray=%s  KD-tree=%s  接近閾值=%.0f km  快取 TTL=%d s",
        HAS_SATREC_ARRAY, HAS_KDTREE, settings.CONJ_THRESHOLD_KM, settings.CONJ_TTL,
    )
    logger.info("掃描 manual_tle_downloads/ …")
    result = ingest_manual_tles()
    if result["files"]:
        logger.info(
            "manual_tle 匯入完成: %d 檔 / %d 顆衛星 TLE（%d 檔跳過）",
            result["files"], result["satellites"], result["skipped"],
        )
    else:
        logger.info("manual_tle_downloads/ 無待處理 *.tle 檔")

    logger.info("預熱衛星索引…")
    get_sat_index()
    get_stats()
    logger.info("啟動背景傳播快取（慢層：每 60 s 全星座重算）…")
    # app.debug 在 app.run() 之前恆為 False，故先定義局部旗標
    # debug=True 時 Werkzeug 啟動 reloader parent + child；只在 child（WERKZEUG_RUN_MAIN=true）啟動執行緒
    _debug = True
    if not _debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        get_cache().start(get_sat_index, interval=60)
    app.run(host=settings.HOST, port=settings.PORT, debug=_debug)
