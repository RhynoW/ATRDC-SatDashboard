"""manual_tle_downloads 啟動時自動匯入至 DuckDB。

run.py 啟動時掃描 MANUAL_TLE_DIR 下所有 *.tle，解析後批次寫入
space_db_slim.duckdb（upsert；相同 norad_id + epoch_utc 先刪後插），
完成後移至 processed/ 子目錄（以時間戳前綴避免重名）。
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from .db import upsert_tle_to_db
from .user_defined import _parse_tle_text

logger = logging.getLogger(__name__)


def ingest_manual_tles() -> dict[str, int]:
    """掃描 MANUAL_TLE_DIR/*.tle，解析並寫入 DB；完成後移至 processed/ 子目錄。

    Returns
    -------
    dict 含：
        files      : 成功處理的檔案數
        satellites : 寫入（或覆蓋）的衛星 TLE 筆數
        skipped    : 讀取或解析失敗的檔案數
    """
    d: Path = settings.MANUAL_TLE_DIR
    if not d.is_dir():
        return {"files": 0, "satellites": 0, "skipped": 0}

    files = sorted(d.glob("*.tle"))
    if not files:
        return {"files": 0, "satellites": 0, "skipped": 0}

    processed_dir = d / "processed"
    processed_dir.mkdir(exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    all_tles: dict[int, dict] = {}
    skipped = 0
    ok_files: list[Path] = []

    for f in files:
        try:
            parsed = _parse_tle_text(f.read_text(encoding="utf-8-sig"), f.name)
            if parsed:
                all_tles.update(parsed)
                ok_files.append(f)
                logger.info("manual_tle: %s — 解析 %d 顆", f.name, len(parsed))
            else:
                logger.warning("manual_tle: %s — 無有效 TLE，跳過", f.name)
                skipped += 1
        except Exception as exc:
            logger.error("manual_tle: 讀取失敗 %s: %s", f.name, exc)
            skipped += 1

    if all_tles:
        logger.info("manual_tle: 共 %d 顆 TLE，寫入 DB 中…", len(all_tles))
        upsert_tle_to_db(all_tles)
        logger.info("manual_tle: DB 寫入完成")

    # 成功的檔案移至 processed/
    for f in ok_files:
        dest = processed_dir / f"{ts}_{f.name}"
        try:
            shutil.move(str(f), dest)
        except Exception as exc:
            logger.warning("manual_tle: 移動 %s 失敗: %s", f.name, exc)

    return {"files": len(ok_files), "satellites": len(all_tles), "skipped": skipped}
