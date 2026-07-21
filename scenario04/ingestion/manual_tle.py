"""manual_tle_downloads 啟動時自動匯入至 DuckDB。

run.py 啟動時掃描 MANUAL_TLE_DIR 下所有 *.tle，解析後用
單一事務 + 批次 INSERT + 明確 CHECKPOINT 寫入 space_db_slim.duckdb，
完成後移至 processed/ 子目錄（以時間戳前綴避免重名）。

注意：不呼叫 upsert_tle_to_db（設計給小批量 Space-Track 用），
      改用 _bulk_insert_tles 以避免 5 萬筆個別 auto-commit 產生巨大 WAL。
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from ..config import settings
from .db import RAW_TABLE, parse_tle_epoch, resolve_db
from .user_defined import _parse_tle_text

logger = logging.getLogger(__name__)


def _bulk_insert_tles(tles: dict[int, dict], db_path: Path) -> int:
    """單一事務批次 INSERT TLE 至 DB，最後執行 CHECKPOINT。

    流程：
    1. 建立 TEMP TABLE 並用 executemany 批次寫入（一次 SQL round-trip）
    2. DELETE 相同 (norad_id, epoch_utc) 的舊行（避免重複）
    3. INSERT … SELECT 從 TEMP TABLE 批次寫入主表
    4. COMMIT + CHECKPOINT（清除 WAL，確保後續 read-only 可立即讀到）

    Returns
    -------
    int : 實際寫入的行數
    """
    rows: list[tuple] = []
    for nid, tle in tles.items():
        l1 = (tle.get("line1") or "").strip()
        l2 = (tle.get("line2") or "").strip()
        if not l1 or not l2:
            continue
        try:
            epoch = parse_tle_epoch(l1[18:32])
        except Exception:
            epoch = datetime.now(timezone.utc)
        rows.append((nid, epoch, l1, l2))

    if not rows:
        return 0

    con = duckdb.connect(str(db_path))
    try:
        actual = {r[0] for r in con.execute(f"DESCRIBE {RAW_TABLE}").fetchall()}
        if "line1" not in actual or "line2" not in actual:
            logger.warning("manual_tle: DB 缺少 line1/line2 欄位，跳過寫入")
            return 0

        con.begin()
        con.execute("""
            CREATE TEMP TABLE _tle_ingest (
                norad_id  INTEGER,
                epoch_utc TIMESTAMPTZ,
                line1     VARCHAR,
                line2     VARCHAR
            )
        """)
        con.executemany("INSERT INTO _tle_ingest VALUES (?, ?, ?, ?)", rows)

        # 刪除重複舊行（相同 norad_id + epoch_utc），再整批插入
        con.execute(f"""
            DELETE FROM {RAW_TABLE}
            WHERE (norad_id, epoch_utc) IN (
                SELECT norad_id, epoch_utc FROM _tle_ingest
            )
        """)
        con.execute(f"""
            INSERT INTO {RAW_TABLE} (norad_id, epoch_utc, line1, line2)
            SELECT norad_id, epoch_utc, line1, line2 FROM _tle_ingest
        """)
        con.commit()
        con.execute("CHECKPOINT")
        logger.info("manual_tle: CHECKPOINT 完成，WAL 已清除")
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()

    return len(rows)


def ingest_manual_tles() -> dict[str, int]:
    """掃描 MANUAL_TLE_DIR/*.tle，解析並批次寫入 DB；完成後移至 processed/ 子目錄。

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
        db = resolve_db()
        if db is None:
            logger.error("manual_tle: 找不到 DB，無法寫入")
            return {"files": 0, "satellites": 0, "skipped": len(files)}

        logger.info("manual_tle: 共 %d 顆 TLE，批次寫入 DB 中…", len(all_tles))
        try:
            written = _bulk_insert_tles(all_tles, db)
            logger.info("manual_tle: DB 批次寫入完成，%d 筆", written)
        except Exception as exc:
            logger.error("manual_tle: DB 寫入失敗: %s", exc)
            return {"files": 0, "satellites": 0, "skipped": len(files)}

    # 成功的檔案移至 processed/
    for f in ok_files:
        dest = processed_dir / f"{ts}_{f.name}"
        try:
            shutil.move(str(f), dest)
        except Exception as exc:
            logger.warning("manual_tle: 移動 %s 失敗: %s", f.name, exc)

    return {"files": len(ok_files), "satellites": len(all_tles), "skipped": skipped}
