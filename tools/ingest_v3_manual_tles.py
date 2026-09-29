"""One-off: ingest starlink_ephemeris/meme_to_tle_batch.py's output into the LOCAL
scenario-advanced01/DB/space_db_slim.duckdb (the file tools/publish_db_to_dataset.py
publishes) -- without booting the full Flask app.

Mirrors run.py's _prefer_local_db(): forces DB_PATH to the local slim DB before
importing scenario04.config.settings, so this writes into the file that actually
gets published, not the parent project's full research database.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
DB_PATH = APP_DIR / "DB" / "space_db_slim.duckdb"

if not DB_PATH.exists():
    print(f"ERROR: {DB_PATH} not found", file=sys.stderr)
    sys.exit(1)

os.environ["DB_PATH"] = str(DB_PATH)
sys.path.insert(0, str(APP_DIR))

from scenario04.ingestion.manual_tle import ingest_manual_tles  # noqa: E402
from scenario04.ingestion.db import resolve_db  # noqa: E402

resolved = resolve_db()
print(f"resolved DB: {resolved}")
assert resolved == DB_PATH, f"DB_PATH override didn't take effect: got {resolved}"

result = ingest_manual_tles()
print(f"ingest result: {result}")

import duckdb  # noqa: E402

con = duckdb.connect(str(DB_PATH), read_only=True)
n = con.execute(
    "select count(*) from raw_tle_archive where norad_id between 339974 and 339999"
).fetchone()[0]
rows = con.execute(
    "select norad_id, epoch_utc from raw_tle_archive "
    "where norad_id between 339974 and 339999 order by norad_id"
).fetchall()
con.close()
print(f"rows now in 339974-339999: {n}")
for nid, ep in rows:
    print(f"  {nid}  {ep}")
