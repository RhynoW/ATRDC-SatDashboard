#!/usr/bin/env python3
"""
merge_storymap_tle.py — 把 StoryMap 所需之 TLE 併入打包 DB
================================================================
StoryMap（/story）用到的衛星集合：
  1. story.GROUPS 六個展示群組（GPS／北斗／Starlink／OneWeb／大陸 ISR／大陸通訊）
  2. config/stories/*.json 內明列之 NORAD（ids／norads／norad 欄位）
  3. user_defined_SaTCatalogue／user_defined_tracking_NORAD 之 NORAD

來源：父專案全量 `space_db.duckdb`（每日管線更新）；以 DuckDB ATTACH 差異寫入
（僅補目標 DB 尚無之 (norad_id, epoch_utc) 列），並補齊 sat_n2yo_metadata。

用法：
  python tools/merge_storymap_tle.py                       # 目標＝scenario-advanced01/DB/space_db_slim.duckdb
  python tools/merge_storymap_tle.py --target scenario04/DB/space_db_slim.duckdb
  python tools/merge_storymap_tle.py --history-days 30 --dry-run
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from scenario04.api.story import GROUPS, group_members          # noqa: E402
from scenario04.ingestion.db import tle_select_sql               # noqa: E402
from scenario04.ingestion.index import _rows_to_index            # noqa: E402

FULL_DB = APP.parent / "space_db.duckdb"
DEFAULT_TARGET = APP / "DB" / "space_db_slim.duckdb"
STORIES_DIR = APP / "scenario04" / "config" / "stories"
USER_DIRS = [APP / "scenario04" / "user_defined_SaTCatalogue",
             APP / "scenario04" / "user_defined_tracking_NORAD"]
TLE_COLS = ["norad_id", "epoch_utc", "sma_km", "eccentricity", "inclination_deg",
            "raan_deg", "argp_deg", "mean_anomaly_deg", "mean_motion", "bstar",
            "line1", "line2"]


def story_json_ids() -> set[int]:
    ids: set[int] = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("ids", "norads") and isinstance(v, list):
                    ids.update(int(x) for x in v if str(x).isdigit())
                elif k == "norad" and str(v).isdigit():
                    ids.add(int(v))
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    for f in STORIES_DIR.glob("*.json"):
        walk(json.loads(f.read_text(encoding="utf-8")))
    return ids


def user_defined_ids() -> set[int]:
    ids: set[int] = set()
    for d in USER_DIRS:
        for f in glob.glob(str(d / "*.csv")):
            with open(f, encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    v = (row.get("norad_id") or "").strip()
                    if v.isdigit():
                        ids.add(int(v))
    return ids


def group_ids(src: duckdb.DuckDBPyConnection, active_days: int) -> dict[str, list[int]]:
    """以來源 DB 近 active_days 天有 TLE 之衛星建索引，套 story.GROUPS 規則。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=active_days)).strftime("%Y-%m-%d")
    sql = tle_select_sql(src, extra_where=f"r.epoch_utc >= TIMESTAMP '{cutoff}'")
    rows = src.execute(sql).fetchall()
    idx = _rows_to_index(rows)
    return {k: group_members(idx, k) for k in GROUPS}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=str(FULL_DB), help="來源全量 DB（預設父專案 space_db.duckdb）")
    ap.add_argument("--target", default=str(DEFAULT_TARGET), help="目標 slim DB")
    ap.add_argument("--history-days", type=int, default=14,
                    help="每顆衛星保留最近 N 天 TLE 歷史（預設 14）")
    ap.add_argument("--active-days", type=int, default=30,
                    help="群組成員判定：近 N 天有 TLE 者（預設 30）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    source, target = Path(args.source), Path(args.target)
    if not source.exists():
        print(f"❌ 來源 DB 不存在：{source}")
        return 1
    if not target.exists():
        print(f"❌ 目標 DB 不存在：{target}")
        return 1
    t0 = time.monotonic()

    src = duckdb.connect(str(source), read_only=True)
    groups = group_ids(src, args.active_days)
    ids: set[int] = set()
    for k, m in groups.items():
        print(f"  群組 {k:10s} {GROUPS[k]['label']:<22s} n={len(m):6d}")
        ids.update(m)
    sj, ud = story_json_ids(), user_defined_ids()
    print(f"  故事 JSON 明列 {len(sj)} 顆、使用者目錄／追蹤清單 {len(ud)} 顆")
    ids |= sj | ud
    print(f"  StoryMap 所需衛星合計 {len(ids)} 顆")
    src.close()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.history_days)
    con = duckdb.connect(str(target), read_only=args.dry_run)
    con.execute(f"ATTACH '{source.as_posix()}' AS src (READ_ONLY)")
    # full_hist=1：故事 JSON 明列之 NORAD（/orbit 單星時序需完整歷史），不受 history_days 限制
    con.execute("CREATE TEMP TABLE want(norad_id BIGINT, full_hist BOOLEAN)")
    con.executemany("INSERT INTO want VALUES (?, ?)", [(i, i in sj) for i in sorted(ids)])

    before = con.execute("SELECT count(*), count(DISTINCT norad_id) FROM raw_tle_archive").fetchone()
    cols = ", ".join(TLE_COLS)
    src_cols = ", ".join(f"s.{c}" for c in TLE_COLS)
    # 候選：所需衛星之近 N 天歷史；若該衛星近 N 天無 TLE，至少補其最新一筆
    con.execute(f"""
        CREATE TEMP TABLE cand AS
        WITH recent AS (
            SELECT {src_cols} FROM src.raw_tle_archive s
            JOIN want w USING (norad_id)
            WHERE s.line1 IS NOT NULL AND s.line2 IS NOT NULL
              AND (s.epoch_utc >= ? OR w.full_hist)
        ), latest AS (
            SELECT {src_cols} FROM src.raw_tle_archive s
            JOIN want w USING (norad_id)
            WHERE s.line1 IS NOT NULL AND s.line2 IS NOT NULL
              AND w.norad_id NOT IN (SELECT norad_id FROM recent)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY s.norad_id ORDER BY s.epoch_utc DESC) = 1
        )
        SELECT * FROM recent UNION ALL SELECT * FROM latest
    """, [cutoff])
    n_new = con.execute("""
        SELECT count(*), count(DISTINCT c.norad_id) FROM cand c
        WHERE NOT EXISTS (SELECT 1 FROM raw_tle_archive t
                          WHERE t.norad_id = c.norad_id AND t.epoch_utc = c.epoch_utc)
    """).fetchone()
    n_missing_sat = con.execute("""
        SELECT count(*) FROM want w
        WHERE NOT EXISTS (SELECT 1 FROM raw_tle_archive t WHERE t.norad_id = w.norad_id)
    """).fetchone()[0]
    n_nosrc = con.execute("""
        SELECT count(*) FROM want w
        WHERE NOT EXISTS (SELECT 1 FROM cand c WHERE c.norad_id = w.norad_id)
    """).fetchone()[0]
    n_meta = con.execute("""
        SELECT count(*) FROM src.sat_n2yo_metadata s JOIN want w USING (norad_id)
        WHERE NOT EXISTS (SELECT 1 FROM sat_n2yo_metadata t WHERE t.norad_id = s.norad_id)
    """).fetchone()[0]
    print(f"  目標 DB 原有：{before[0]:,} 列 / {before[1]:,} 顆；所需衛星中目標缺 {n_missing_sat} 顆、"
          f"來源亦無 TLE {n_nosrc} 顆")
    print(f"  待寫入：TLE {n_new[0]:,} 列（{n_new[1]:,} 顆）、metadata {n_meta} 列")
    if args.dry_run:
        print("  （dry-run，未寫入）")
        return 0

    con.execute(f"""
        INSERT INTO raw_tle_archive ({cols})
        SELECT {cols} FROM cand c
        WHERE NOT EXISTS (SELECT 1 FROM raw_tle_archive t
                          WHERE t.norad_id = c.norad_id AND t.epoch_utc = c.epoch_utc)
    """)
    meta_cols = [r[0] for r in con.execute("DESCRIBE sat_n2yo_metadata").fetchall()]
    mc = ", ".join(meta_cols)
    con.execute(f"""
        INSERT INTO sat_n2yo_metadata ({mc})
        SELECT {mc} FROM src.sat_n2yo_metadata s
        WHERE s.norad_id IN (SELECT norad_id FROM want)
          AND NOT EXISTS (SELECT 1 FROM sat_n2yo_metadata t WHERE t.norad_id = s.norad_id)
    """)
    after = con.execute(
        "SELECT count(*), count(DISTINCT norad_id), max(epoch_utc) FROM raw_tle_archive").fetchone()
    con.execute("DETACH src")
    con.execute("CHECKPOINT")
    con.close()
    print(f"✅ 完成：{after[0]:,} 列 / {after[1]:,} 顆，最新 epoch {after[2]}；"
          f"檔案 {target.stat().st_size/1e6:.1f} MB，耗時 {time.monotonic()-t0:.0f} s → {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
