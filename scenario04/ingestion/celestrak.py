"""CelesTrak GP 最新 TLE 抓取（免帳號）；有 Space-Track 憑證時優先走 spacetrack.py。

fetch_latest(norad) → {"norad","name","line1","line2","epoch","source"} | None
refresh_to_db([norads]) → 寫回 raw_tle_archive（僅當 epoch 比庫內新）
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from .db import parse_tle_epoch, resolve_db, upsert_tle_to_db

logger = logging.getLogger(__name__)
CT_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={n}&FORMAT=TLE"


ST_BASE = "https://www.space-track.org"
_st_session: requests.Session | None = None


def st_session() -> requests.Session | None:
    """最小 Space-Track 客戶端：以 settings 之帳密登入（SPACETRACK_USER/PASS 或 SPACE_TRACK_IDENTITY/PASSWORD）。"""
    global _st_session
    from ..config import settings
    if not (settings.SPACETRACK_USER and settings.SPACETRACK_PASS):
        return None
    if _st_session is not None:
        return _st_session
    s = requests.Session()
    r = s.post(f"{ST_BASE}/ajaxauth/login", data={"identity": settings.SPACETRACK_USER,
                                                   "password": settings.SPACETRACK_PASS}, timeout=30)
    if r.status_code != 200 or "Failed" in r.text[:200]:
        logger.warning("Space-Track 登入失敗（HTTP %s）", r.status_code)
        return None
    _st_session = s
    return s


def st_latest_gp(norad: int) -> dict | None:
    s = st_session()
    if s is None:
        return None
    r = s.get(f"{ST_BASE}/basicspacedata/query/class/gp/NORAD_CAT_ID/{norad}/orderby/EPOCH%20desc/limit/1/format/json", timeout=60)
    if r.status_code != 200 or not r.text.strip().startswith("["):
        return None
    rows = r.json()
    if not rows:
        return None
    g = rows[0]
    return {"norad": norad, "name": g.get("OBJECT_NAME", ""), "line1": g["TLE_LINE1"], "line2": g["TLE_LINE2"],
            "epoch": g["EPOCH"].replace(" ", "T") + ("" if "+" in g["EPOCH"] else "+00:00"), "source": "Space-Track"}


def st_decay_forecast(norad: int) -> list[dict]:
    """Space-Track TIP（Tracking and Impact Prediction）與 decay 類別之預報（若有）。"""
    s = st_session()
    if s is None:
        return []
    out = []
    for cls, order in (("tip", "MSG_EPOCH"), ("decay", "DECAY_EPOCH")):
        try:
            r = s.get(f"{ST_BASE}/basicspacedata/query/class/{cls}/NORAD_CAT_ID/{norad}/orderby/{order}%20desc/limit/5/format/json", timeout=60)
            if r.status_code == 200 and r.text.strip().startswith("["):
                for row in r.json():
                    row["_class"] = cls
                    out.append(row)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Space-Track %s 查詢失敗 %s: %s", cls, norad, exc)
    return out


def fetch_latest(norad: int, timeout: float = 30.0) -> dict | None:
    """優先 Space-Track（若已配置帳密），否則 CelesTrak GP。"""
    try:
        t = st_latest_gp(norad)
        if t:
            return t
    except Exception as exc:  # noqa: BLE001
        logger.debug("Space-Track 抓取失敗 %s: %s", norad, exc)
    try:
        r = requests.get(CT_URL.format(n=norad), timeout=timeout)
        if r.status_code != 200:
            return None
        lines = [x.strip() for x in r.text.strip().splitlines() if x.strip()]
        if len(lines) < 2:
            return None
        name = lines[0] if len(lines) == 3 else ""
        l1, l2 = lines[-2], lines[-1]
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            return None
        return {"norad": norad, "name": name, "line1": l1, "line2": l2,
                "epoch": parse_tle_epoch(l1[18:32]).isoformat(), "source": "CelesTrak"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("CelesTrak 抓取失敗 %s: %s", norad, exc)
        return None


def db_latest_epoch(norad: int) -> datetime | None:
    import duckdb
    db = resolve_db()
    if db is None:
        return None
    with duckdb.connect(str(db), read_only=True) as con:
        v = con.execute("SELECT max(epoch_utc) FROM raw_tle_archive WHERE norad_id=?", [norad]).fetchone()[0]
    if v is None:
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def refresh_to_db(norads: list[int]) -> dict[int, dict]:
    """抓最新 TLE，若比庫內新則 upsert；回傳 {norad: {epoch, source, updated: bool}}。"""
    out: dict[int, dict] = {}
    new: dict[int, dict] = {}
    for n in norads:
        t = fetch_latest(n)
        if not t:
            out[n] = {"updated": False, "reason": "no data"}
            continue
        ep = datetime.fromisoformat(t["epoch"])
        ep = ep if ep.tzinfo else ep.replace(tzinfo=timezone.utc)
        cur = db_latest_epoch(n)
        newer = cur is None or ep > cur + __import__("datetime").timedelta(seconds=1)
        out[n] = {"updated": newer, "epoch": ep.isoformat(), "db_epoch": cur.isoformat() if cur else None,
                  "source": t["source"], "line1": t["line1"], "line2": t["line2"]}
        if newer:
            new[n] = {"line1": t["line1"], "line2": t["line2"], "epoch": ep.isoformat()}
    if new:
        upsert_tle_to_db(new)
    return out
