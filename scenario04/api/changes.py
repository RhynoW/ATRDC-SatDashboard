"""SATCAT 每日異動：讀 DB 內 satcat_change／satcat_change_meta 表（本機每日管線 tools/update_satcat.py 寫入、跟著 DB 發布）。

來源：Space-Track class/satcat_change（名稱／國際編號／國家／發射日／DECAY 的異動與 CHANGE_MADE 時間）為準，
再以前後兩份 SATCAT 快照比對補 OBJECT_TYPE 變更。類別（管線端分好）：
new 新編目、named OBJECT x → 正式名稱、renamed 更名、decayed 再入、decay_fix 再入日期修正、type 類型變更、other 其他欄位。
對應 Rust 單機版 src/changes.rs。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, jsonify, request

from ..ingestion.db import resolve_db
from ..ingestion.index import get_sat_index

logger = logging.getLogger(__name__)
bp = Blueprint("changes", __name__)

KINDS = ("new", "named", "renamed", "decayed", "decay_fix", "type", "other")
NONAME_RE = re.compile(r"^(OBJECT [A-Z]{1,3}|TBA.*|UNKNOWN|)$")
_c: dict[str, Any] = {"mtime": None, "rows": [], "meta": None}


def load() -> dict[str, Any]:
    """DB 的 satcat_change 表（依 DB mtime 快取）"""
    db = resolve_db()
    if db is None:
        return _c
    try:
        mt = db.stat().st_mtime
    except OSError:
        return _c
    if mt == _c["mtime"]:
        return _c
    import duckdb
    rows, meta = [], None
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            tabs = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
            if "satcat_change" in tabs:
                cols = ["norad_id", "change_made", "kind", "field", "previous", "current", "name", "intl", "source"]
                for r in con.execute(f"SELECT {', '.join(cols)} FROM satcat_change ORDER BY change_made DESC, norad_id DESC").fetchall():
                    d = dict(zip(cols, r))
                    d["norad_id"] = int(d["norad_id"])
                    for k in cols[1:]:
                        d[k] = d[k] or ""
                    rows.append(d)
            if "satcat_change_meta" in tabs:
                r = con.execute("SELECT fetched_at, days, count, spacetrack, diff FROM satcat_change_meta LIMIT 1").fetchone()
                if r:
                    meta = {"fetched_at": r[0], "days": r[1], "count": r[2], "spacetrack": r[3], "diff": r[4]}
    except Exception as e:
        logger.warning("讀取 satcat_change 表失敗：%s", e)
    _c.update(mtime=mt, rows=rows, meta=meta)
    return _c


def query(rows: list[dict], idx: dict, days: int, kinds: set[str] | None, norads: set[int] | None, q: str,
          now: datetime | None = None, limit: int = 5000) -> dict:
    now = now or datetime.now(timezone.utc)
    cut = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    sel = [r for r in rows if r["change_made"] >= cut]
    counts = {k: 0 for k in KINDS}
    for r in sel:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    ql = q.strip().upper()
    out = []
    for r in sel:
        if kinds and r["kind"] not in kinds:
            continue
        if norads is not None and r["norad_id"] not in norads:
            continue
        if ql and not (ql == str(r["norad_id"]) or any(ql in (r[k] or "").upper() for k in ("name", "intl", "previous", "current"))):
            continue
        s = idx.get(r["norad_id"])
        cat = s["name"].strip() if s else None
        out.append(dict(r, catalog_name=cat, catalog_stale=bool(r["kind"] == "named" and cat is not None and NONAME_RE.match(cat.upper()))))
    return {"total": len(out), "results": out[:limit], "truncated": len(out) > limit, "counts": counts, "days": days}


@bp.get("/api/satadmin/changes")
def api_changes():
    """?days=30（1–90）&kind=named,decayed&norad=1,2&q=關鍵字"""
    c = load()
    try:
        days = max(1, min(int(request.args.get("days", 30)), 90))
    except ValueError:
        days = 30
    kinds = {k for k in request.args.get("kind", "").split(",") if k in KINDS} or None
    nr = request.args.get("norad", "")
    norads = {int(x) for x in nr.replace(",", " ").split() if x.isdigit()} if nr.strip() else None
    res = query(c["rows"], get_sat_index(), days, kinds, norads, request.args.get("q", ""))
    res["meta"] = c["meta"]
    return jsonify(res)
