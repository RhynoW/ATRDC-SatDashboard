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


def _prefer_local_db() -> None:
    """DB 優先序：優先採用 scenario-advanced01/DB/ 下之 *.duckdb（本機打包 DB）；
    若該目錄不存在或無 *.duckdb，則不設定、退化為 scenario04 既有 resolve_db() 機制。
    使用者以環境變數 DB_PATH 顯式指定時一律尊重、不覆寫。"""
    if os.getenv("DB_PATH"):
        return
    from pathlib import Path
    db_dir = Path(__file__).resolve().parent / "DB"
    if not db_dir.is_dir():
        return
    cands = sorted(
        (p for p in db_dir.iterdir() if p.is_file() and p.suffix.lower() == ".duckdb"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if cands:
        os.environ["DB_PATH"] = str(cands[0])
        logging.getLogger("scenario04.run").info(
            "DB 優先序：採用本機打包 DB %s（scenario-advanced01/DB，共 %d 個候選）",
            cands[0].name, len(cands))


_prefer_local_db()   # 須在匯入 scenario04（讀取 settings.DB_PATH）之前執行

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
        "RPO 3D 場景（神龍 58573×59884 相對接近 + Chan Pc）: http://%s:%d/rpo",
        settings.HOST, settings.PORT,
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
    # 正式模式：HF Space（平台注入 SPACE_ID）或 PRODUCTION=1 → waitress 多執行緒 WSGI、無 debug/reloader。
    # 原本正式環境也跑 Werkzeug 開發伺服器 + debug：HF 代理下 Cesium 大量並行素材請求頻繁 502，
    # reloader 父程序重複預熱索引，且互動除錯器不應對外。本機開發維持 debug 開發伺服器。
    _prod = bool(os.getenv("SPACE_ID")) or os.getenv("PRODUCTION", "").lower() in ("1", "true", "yes")
    # app.debug 在 app.run() 之前恆為 False，故先定義局部旗標
    # debug=True 時 Werkzeug 啟動 reloader parent + child；只在 child（WERKZEUG_RUN_MAIN=true）啟動執行緒
    _debug = not _prod
    if not _debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        get_cache().start(get_sat_index, interval=60)
        # 離軌 Starlink 再入預測（分段 M/A 校準）背景批次：每 6 小時一次，結果寫 DB/deorbit_forecast.json
        if os.getenv("DEORBIT_FORECAST_DISABLE", "").lower() not in ("1", "true", "yes"):
            from scenario04.services.deorbit_forecast import deorbit_forecast_service
            deorbit_forecast_service.start()
    if _prod:
        from waitress import serve
        _threads = int(os.getenv("WAITRESS_THREADS", "64"))   # SSE 長連線各占一執行緒，須留足
        logger.info("正式模式：waitress %s:%d（threads=%d）", settings.HOST, settings.PORT, _threads)
        # send_bytes=1：SSE 小封包立即送出，不等緩衝滿；channel_timeout 大於 SSE keepalive 間隔
        serve(app, host=settings.HOST, port=settings.PORT, threads=_threads,
              send_bytes=1, channel_timeout=300, connection_limit=500, ident="SatDashboard")
    else:
        # threaded=True：/api/broadcast/stream 為長連線 SSE，須並行處理其餘請求，否則會卡住整個開發伺服器
        app.run(host=settings.HOST, port=settings.PORT, debug=_debug, threaded=True)
