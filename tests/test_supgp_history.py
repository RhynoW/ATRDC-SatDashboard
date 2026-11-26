"""SupGP 歷史存檔（tools/update_satcat.py archive_supgp）：Alpha-5 編號、曆元解析、同顆同曆元去重。"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "tools"))
import update_satcat as u  # noqa: E402

L1 = "1 60931U 24154A   26329.50000000  .00010000  00000+0  50000-3 0  9990"
L2 = "2 60931  43.0000 100.0000 0001000  90.0000 270.0000 15.30000000 10000"


def test_norad5_and_epoch():
    assert u._norad5("60931") == 60931
    assert u._norad5("A0001") == 100001
    assert u._norad5("Z9999") == 339999
    assert u._norad5("I0001") is None
    assert u._tle_epoch(L1) == datetime(2026, 11, 25, 12, tzinfo=timezone.utc)


def test_archive_dedup(tmp_path):
    p = tmp_path / "h.duckdb"
    l1b = L1.replace("26329.50000000", "26330.00000000")
    assert u.archive_supgp([["starlink", "STARLINK-11204", L1, L2]], "2026-11-26T00:00:00Z", p) == (1, 1)
    # 同一曆元再存一次不重複；新曆元新增
    assert u.archive_supgp([["starlink", "STARLINK-11204", L1, L2], ["starlink", "STARLINK-11204", l1b, L2]], "2026-11-26T08:00:00Z", p) == (1, 2)
    con = duckdb.connect(str(p), read_only=True)
    assert con.execute("select fetched_at from supgp_hist order by epoch_utc").fetchall()[0][0] == "2026-11-26T00:00:00Z"
    con.close()
