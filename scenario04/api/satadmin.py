"""衛星管理（線上版）：查詢、重複名稱、DECAY 狀態、星歷資料狀態與重抓。

線上版（HF Space）為公開網站且容器重啟會清空檔案，因此：
- 更名與群組只存在使用者自己的瀏覽器（web/static/js/satadmin.js 的 localStorage），伺服器不保存；
  本模組只提供唯讀查詢，DECAY 狀態以 POST 帶入 NORAD 清單查詢。
- 星歷「最後更新時間」公開顯示；「重新抓取」需 ADMIN_TOKEN（未設定時只限本機），避免訪客觸發導致 CelesTrak 封鎖。
- SATCAT（含 DECAY 再入日期）在需要時自動向 CelesTrak 下載，12 小時內只抓一次，存在暫存目錄。
對應 Rust 單機版 src/satadmin.rs、src/ephemeris.rs（單機版另有帳號權限與伺服器端存檔）。
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from flask import Blueprint, jsonify, render_template, request

from ..ingestion.db import get_db_info, resolve_db
from ..ingestion.index import get_sat_index, invalidate_index

logger = logging.getLogger(__name__)
bp = Blueprint("satadmin", __name__)

CELESTRAK = os.getenv("SATDASH_CELESTRAK_BASE", "https://celestrak.org")
UA = "ATRDC-SatDashboard (ephemeris refresh)"
SATCAT_TTL = 12 * 3600
GP_MIN_INTERVAL = int(os.getenv("SATDASH_EPHEM_MIN_INTERVAL_S", "7200"))
SATCAT_FILE = Path(tempfile.gettempdir()) / "atrdc_satcat.json"
META_FILE = Path(tempfile.gettempdir()) / "atrdc_ephem_meta.json"

_lock = threading.Lock()
_satcat: dict[int, dict] = {}
_satcat_at = 0.0
_satcat_err = ""
_job: dict[str, Any] = {"running": False, "log": []}


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def tle_epoch(line1: str) -> datetime | None:
    try:
        yy = int(line1[18:20]); doy = float(line1[20:32])
    except (ValueError, IndexError):
        return None
    return datetime(2000 + yy if yy < 57 else 1900 + yy, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)


def intl_code(line1: str) -> str:
    raw = line1[9:17].strip() if len(line1) >= 17 else ""
    if len(raw) < 5 or not raw[:2].isdigit():
        return raw
    yy = int(raw[:2])
    return f"{2000 + yy if yy < 57 else 1900 + yy}-{raw[2:]}"


def is_debris_name(name: str) -> bool:
    up = name.upper()
    if "R/B" in up:
        return True
    words = "".join(c if c.isalnum() else " " for c in up).split()
    return any(w in {"DEB", "COOLANT", "AKM", "PKM", "TANK", "PLATFORM", "SHROUD"} for w in words)


def _meta() -> dict:
    try:
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_meta(m: dict) -> None:
    try:
        META_FILE.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ── SATCAT ───────────────────────────────────────────────────────────────────

def _parse_satcat_csv(text: str) -> dict[int, dict]:
    out = {}
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        try:
            n = int(r.get("NORAD_CAT_ID", ""))
        except ValueError:
            continue
        out[n] = {"name": r.get("OBJECT_NAME", ""), "intl": r.get("OBJECT_ID", ""), "object_type": r.get("OBJECT_TYPE", ""),
                  "owner": r.get("OWNER", ""), "launch": r.get("LAUNCH_DATE", ""), "decay": (r.get("DECAY_DATE") or "").strip()}
    if len(out) < 1000:
        raise ValueError(f"satcat.csv 只有 {len(out)} 筆，疑似下載不完整")
    return out


def get_satcat(fetch: bool = True) -> dict[int, dict]:
    """記憶體 → 暫存檔 → CelesTrak（12 小時內只抓一次）"""
    global _satcat, _satcat_at, _satcat_err
    with _lock:
        if _satcat and time.time() - _satcat_at < SATCAT_TTL:
            return _satcat
        if SATCAT_FILE.exists() and time.time() - SATCAT_FILE.stat().st_mtime < SATCAT_TTL:
            try:
                _satcat = {int(k): v for k, v in json.loads(SATCAT_FILE.read_text(encoding="utf-8")).items()}
                _satcat_at = SATCAT_FILE.stat().st_mtime
                return _satcat
            except Exception:
                pass
        m = _meta()
        last = m.get("satcat_attempt", 0)
        if not fetch or time.time() - last < 600:  # 失敗後 10 分鐘內不重試
            return _satcat
        m["satcat_attempt"] = time.time(); _save_meta(m)
        try:
            r = requests.get(f"{CELESTRAK}/pub/satcat.csv", timeout=60, headers={"User-Agent": UA})
            r.raise_for_status()
            _satcat = _parse_satcat_csv(r.text)
            _satcat_at = time.time()
            _satcat_err = ""
            SATCAT_FILE.write_text(json.dumps(_satcat, ensure_ascii=False), encoding="utf-8")
            m["satcat"] = {"source": "celestrak", "fetched_at": _iso(datetime.now(timezone.utc)), "count": len(_satcat),
                           "decayed": sum(1 for v in _satcat.values() if v["decay"])}
            _save_meta(m)
            logger.info("SATCAT 下載完成：%d 筆", len(_satcat))
        except Exception as e:
            _satcat_err = f"SATCAT 下載失敗：{e}"
            logger.warning(_satcat_err)
        return _satcat


def _status(sc: dict[int, dict], line1: str | None, nid: int, stale_days: float) -> dict:
    r = sc.get(nid)
    ep = tle_epoch(line1) if line1 else None
    age = round((datetime.now(timezone.utc) - ep).total_seconds() / 86400, 1) if ep else None
    if r and r["decay"]:
        st = "decayed"
    elif age is not None and age > stale_days:
        st = "stale"
    elif sc and r is None:
        st = "no_satcat"
    else:
        st = "ok"
    dio = None
    if r and r["decay"] and r["launch"]:
        try:
            dio = (datetime.fromisoformat(r["decay"][:10]) - datetime.fromisoformat(r["launch"][:10])).days
        except ValueError:
            pass
    return {"status": st, "decay_date": (r or {}).get("decay") or None, "launch_date": (r or {}).get("launch") or None,
            "days_in_orbit": dio, "object_type": (r or {}).get("object_type"), "owner": (r or {}).get("owner"), "tle_age_days": age}


def _sat_row(idx: dict, sc: dict, nid: int, stale_days: float = 30.0) -> dict:
    s = idx.get(nid)
    row = {"norad_id": nid, "name": s["name"] if s else (sc.get(nid) or {}).get("name", ""), "in_catalog": s is not None,
           "intl_code": intl_code(s["line1"]) if s else (sc.get(nid) or {}).get("intl"),
           "tle_epoch": _iso(tle_epoch(s["line1"])) if s else None,
           "country": s.get("country") if s else None, "purpose": s.get("purpose") if s else None}
    row.update(_status(sc, s["line1"] if s else None, nid, stale_days))
    return row


# ── 頁面與 API ───────────────────────────────────────────────────────────────

@bp.get("/satadmin")
def page_satadmin():
    return render_template("satadmin.html")


@bp.get("/api/satadmin/lookup")
def api_lookup():
    idx = get_sat_index()
    sc = get_satcat(fetch=False)
    ids = [int(x) for x in request.args.get("norad", "").replace("，", ",").replace(",", " ").split() if x.isdigit()][:500]
    q = request.args.get("q", "").strip().upper()
    truncated = False
    if not ids and len(q) >= 2:
        for nid, s in idx.items():
            if q in s["name"].upper() or q in intl_code(s["line1"]):
                if len(ids) >= 200:
                    truncated = True
                    break
                ids.append(nid)
    rows = [_sat_row(idx, sc, n) for n in ids]
    return jsonify({"results": rows, "count": len(rows), "truncated": truncated})


@bp.get("/api/satadmin/duplicates")
def api_duplicates():
    idx = get_sat_index()
    no_deb = request.args.get("no_debris") == "1"
    m: dict[str, list[int]] = {}
    for nid, s in idx.items():
        if no_deb and is_debris_name(s["name"]):
            continue
        m.setdefault(s["name"].strip(), []).append(nid)
    v = sorted(((k, sorted(x)) for k, x in m.items() if len(x) >= 2), key=lambda kv: (-len(kv[1]), kv[0]))
    lim = min(int(request.args.get("limit", 300)), 2000)
    return jsonify({"results": [{"name": k, "count": len(x), "norad_ids": x} for k, x in v[:lim]], "total_names": len(v)})


@bp.post("/api/satadmin/status")
def api_status():
    """DECAY 狀態：body {norads:[...], stale_days}（群組存在瀏覽器，由前端帶入成員）"""
    b = request.get_json(silent=True) or {}
    ids = [int(x) for x in b.get("norads", []) if str(x).isdigit()][:20000]
    stale = float(b.get("stale_days", 30))
    idx = get_sat_index()
    sc = get_satcat()
    rows = [_sat_row(idx, sc, n, stale) for n in ids]
    m = _meta().get("satcat")
    return jsonify({"rows": rows, "satcat_loaded": bool(sc), "satcat": m, "satcat_error": _satcat_err or None})


@bp.get("/api/satadmin/recent_decay")
def api_recent_decay():
    days = max(1, min(int(request.args.get("days", 30)), 3650))
    sc = get_satcat()
    today = datetime.now(timezone.utc).date()
    rows = []
    for n, r in sc.items():
        if not r["decay"]:
            continue
        try:
            d = (today - datetime.fromisoformat(r["decay"][:10]).date()).days
        except ValueError:
            continue
        if 0 <= d <= days:
            row = {"norad_id": n, "name": r["name"], "intl_code": r["intl"]}
            row.update(_status(sc, None, n, 30))
            rows.append(row)
    rows.sort(key=lambda x: (x["decay_date"] or "", -x["norad_id"]), reverse=True)
    return jsonify({"rows": rows[:5000], "satcat_loaded": bool(sc), "satcat_error": _satcat_err or None})


# ── 星歷 ─────────────────────────────────────────────────────────────────────

def _index_epoch_stats() -> dict:
    idx = get_sat_index()
    now = datetime.now(timezone.utc)
    eps = [e for e in (tle_epoch(s["line1"]) for s in idx.values() if s.get("line1")) if e]
    ages = sorted((now - e).total_seconds() / 86400 for e in eps)
    return {"count": len(idx), "epoch_latest": _iso(max(eps)) if eps else None,
            "median_age_days": round(ages[len(ages) // 2], 1) if ages else None,
            "within_1d": sum(a <= 1 for a in ages), "within_3d": sum(a <= 3 for a in ages), "older_30d": sum(a > 30 for a in ages)}


@bp.get("/api/ephemeris/status")
def api_ephem_status():
    info = get_db_info()
    m = _meta()
    db_at = info.get("db_updated_at")
    gp = m.get("gp")
    cand = [x for x in (db_at, (gp or {}).get("fetched_at")) if x]
    last = max(cand, key=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))) if cand else None
    from .admin import _admin_allowed
    wait = max(0, int(GP_MIN_INTERVAL - (time.time() - m.get("gp_attempt", 0))))
    return jsonify({"last_updated": last, "db": {"name": info.get("db_name"), "updated_at": db_at}, "index": _index_epoch_stats(),
                    "gp": gp, "satcat": m.get("satcat"), "job": dict(_job), "can_refresh": _admin_allowed(), "wait_s": {"gp:celestrak": wait}})


def _run_refresh(by: str) -> None:
    def step(s: str):
        _job["step"] = s
        _job["log"].append(datetime.now().strftime("%H:%M:%S ") + s)
        logger.info("星歷重抓：%s", s)
    try:
        step("下載 CelesTrak active 群組…")
        r = requests.get(f"{CELESTRAK}/NORAD/elements/gp.php?GROUP=active&FORMAT=tle", timeout=120, headers={"User-Agent": UA})
        r.raise_for_status()
        lines = [l.rstrip() for l in r.text.splitlines() if l.strip()]
        tles: dict[int, dict] = {}
        for i, l in enumerate(lines):
            if l.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 ") and len(l) >= 69:
                try:
                    tles[int(l[2:7])] = {"line1": l, "line2": lines[i + 1]}
                except ValueError:
                    pass
        if len(tles) < 10:
            raise ValueError(f"只取得 {len(tles)} 筆 TLE，疑似來源異常")
        idx = get_sat_index()
        newer = sum(1 for n, t in tles.items() if n in idx and (tle_epoch(t["line1"]) or datetime.min.replace(tzinfo=timezone.utc)) > (tle_epoch(idx[n]["line1"]) or datetime.max.replace(tzinfo=timezone.utc)))
        added = sum(1 for n in tles if n not in idx)
        db = resolve_db()
        if db is None:
            raise RuntimeError("找不到資料庫")
        step(f"寫入資料庫 {len(tles)} 筆（比目前新 {newer} 顆、新增 {added} 顆）…")
        from ..ingestion.manual_tle import _bulk_insert_tles
        _bulk_insert_tles(tles, db)
        invalidate_index()
        try:
            from ..ingestion import db as dbmod
            dbmod._db_info_cache.clear()
            dbmod._db_info_loaded_at = 0.0
        except Exception:
            pass
        m = _meta()
        m["gp"] = {"source": "celestrak", "fetched_at": _iso(datetime.now(timezone.utc)), "count": len(tles), "newer": newer, "added": added, "by": by}
        _save_meta(m)
        step(f"完成：TLE {len(tles)} 筆（更新 {newer}、新增 {added}）")
        _job["ok"] = True
    except Exception as e:
        step(f"失敗：{e}")
        _job["ok"] = False
        if "503" in str(e) or "Connection" in type(e).__name__ or "Timeout" in type(e).__name__:
            m = _meta(); m["gp_attempt"] = time.time() - GP_MIN_INTERVAL + 600; _save_meta(m)
    finally:
        _job["running"] = False
        _job["finished_at"] = _iso(datetime.now(timezone.utc))


@bp.post("/api/ephemeris/refresh")
def api_ephem_refresh():
    from .admin import _admin_allowed
    if not _admin_allowed():
        return jsonify({"error": "重新抓取需管理權限（X-Admin-Token）"}), 403
    m = _meta()
    wait = GP_MIN_INTERVAL - (time.time() - m.get("gp_attempt", 0))
    if wait > 0:
        return jsonify({"error": f"依 CelesTrak 使用規範，{GP_MIN_INTERVAL // 60} 分鐘內只抓一次；請 {int(wait // 60) + 1} 分鐘後再試"}), 429
    with _lock:
        if _job.get("running"):
            return jsonify({"error": "已有重抓工作進行中"}), 409
        _job.clear(); _job.update({"running": True, "log": [], "source": "celestrak", "started_at": _iso(datetime.now(timezone.utc))})
    m["gp_attempt"] = time.time(); _save_meta(m)
    threading.Thread(target=_run_refresh, args=(request.remote_addr or "",), daemon=True).start()
    return jsonify({"ok": True})
