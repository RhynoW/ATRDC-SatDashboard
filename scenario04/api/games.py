"""太空小遊戲（線上版）：碎片連鎖。

GET  /game                     頁面（Cesium 地球＋碎片連鎖面板，中／EN／日）
POST /api/games/breakup        解體與碎片連鎖模擬（請求與回應格式同 Rust 單機版）
GET  /api/games/crowd/catalog  目錄背景點用的 TLE 清單 [norad, 群組, line1, line2]
GET  /api/games/mega/background 百萬顆長期風險：各 25 km 高度格的運作中／失效／碎片顆數（計算核心在前端 games_mega.js，兩版共用；
                               分類規則與 Rust 單機版 src/games_mega.rs 相同）

計算核心是 Rust 單機版的 debris_engine.rs，編成 debris_cli（Dockerfile 的建置階段；
本機測試用環境變數 DEBRIS_CLI 指定執行檔）。目錄沿用本站的衛星索引，依 NORAD 排序寫成 JSONL 給 CLI 讀。

資源保護與單機版相同：同時只算一個模擬（其餘回 429＋Retry-After），相同參數 5 分鐘內直接回快取。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path

from flask import Blueprint, Response, render_template, request

from . import json_response
from ..ingestion.index import get_sat_index

bp = Blueprint("games", __name__)
logger = logging.getLogger(__name__)

CACHE_SECS = 300
CACHE_MAX = 6
CLI_TIMEOUT_S = 180
_BUSY = threading.Lock()
_CACHE: OrderedDict[str, tuple[int, bytes]] = OrderedDict()
_CAT_LOCK = threading.Lock()
_CAT: dict = {"key": None, "path": None, "tle": None}


def _cli_path() -> Path | None:
    env = os.getenv("DEBRIS_CLI")
    cands = [Path(env)] if env else []
    cands.append(Path(__file__).resolve().parents[2] / "bin" / "debris_cli")
    return next((p for p in cands if p.is_file()), None)


def _catalog() -> tuple[str, str, list]:
    """(索引識別, JSONL 檔路徑, 背景點清單)；索引重建後重新產生。"""
    idx = get_sat_index()
    key = f"{id(idx)}:{len(idx)}"
    with _CAT_LOCK:
        if _CAT["key"] != key:
            rows = [(nid, idx[nid]) for nid in sorted(idx) if idx[nid].get("line1") and idx[nid].get("line2")]
            fd, path = tempfile.mkstemp(prefix="debris_catalog_", suffix=".jsonl")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for nid, i in rows:
                    f.write(json.dumps([nid, i.get("name") or f"NORAD-{nid}", i.get("purpose") or "", i["line1"], i["line2"]],
                                       ensure_ascii=False) + "\n")
            old = _CAT["path"]
            _CAT.update(key=key, path=path, tle=[[nid, 0, i["line1"], i["line2"]] for nid, i in rows])
            if old:
                try:
                    os.remove(old)
                except OSError:
                    pass
            logger.info("碎片連鎖目錄：%d 筆 → %s", len(rows), path)
        return _CAT["key"], _CAT["path"], _CAT["tle"]


def _error(msg: str, code: int = 400, **extra) -> Response:
    resp = json_response({"error": msg, **extra})
    resp.status_code = code
    return resp


def _raw(body: bytes) -> Response:
    """CLI 輸出的 JSON 原樣回傳（碎片多時有數 MB，不再重新序列化）"""
    return Response(body, content_type="application/json; charset=utf-8")


@bp.get("/game")
def game_page():
    return render_template("game.html")


MEGA_H0, MEGA_DH, MEGA_NB, MEGA_ACTIVE_DAYS = 200.0, 25.0, 72, 2922


def mega_classify(object_type: str, launch: str, apogee, perigee, cutoff: str):
    """（高度格, 類別 0 運作中／1 失效／2 碎片）；與 Rust games_mega::classify 相同"""
    if apogee is None or perigee is None:
        return None
    alt = (float(apogee) + float(perigee)) / 2.0
    if not (alt == alt) or alt < MEGA_H0 or alt >= MEGA_H0 + MEGA_DH * MEGA_NB:
        return None
    b = int((alt - MEGA_H0) // MEGA_DH)
    t = (object_type or "").strip().upper()
    if t == "DEBRIS":
        k = 2
    elif t == "PAYLOAD":
        ln = (launch or "").strip()
        k = 0 if ln and ln >= cutoff else 1
    else:
        k = 1
    return b, k


def mega_background(db_path, cutoff: str) -> dict:
    import duckdb
    counts = [[0] * MEGA_NB for _ in range(3)]
    total = 0
    with duckdb.connect(str(db_path), read_only=True) as con:
        rows = con.execute("SELECT coalesce(object_type, ''), coalesce(launch, ''), apogee, perigee FROM satcat WHERE coalesce(decay, '') = ''").fetchall()
        try:
            src = con.execute("SELECT coalesce(source, '') || '，' || coalesce(fetched_at, '') FROM satcat_meta LIMIT 1").fetchone()[0]
        except Exception:  # noqa: BLE001
            src = "SATCAT"
    for ot, ln, ap, pe in rows:
        r = mega_classify(ot, ln, ap, pe, cutoff)
        if r:
            counts[r[1]][r[0]] += 1
            total += 1
    return {"h0": MEGA_H0, "dh": MEGA_DH, "n": MEGA_NB, "cutoff": cutoff,
            "active": counts[0], "derelict": counts[1], "debris": counts[2], "total": total, "source": f"SATCAT {src}",
            "rule": "高度＝(遠地點＋近地點)/2；DEBRIS→碎片；PAYLOAD 發射 8 年內→運作中、其餘→失效；ROCKET BODY、UNKNOWN→失效"}


@bp.get("/api/games/mega/background")
def mega_background_api():
    from datetime import datetime, timedelta, timezone
    from ..ingestion.db import resolve_db
    db = resolve_db()
    if db is None:
        return _error("找不到資料庫", 503)
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=MEGA_ACTIVE_DAYS)).isoformat()
    try:
        return json_response(mega_background(db, cutoff))
    except Exception as e:  # noqa: BLE001
        logger.exception("百萬顆背景族群失敗")
        return _error(f"讀取 SATCAT 失敗：{e}", 503)


@bp.get("/api/games/crowd/catalog")
def crowd_catalog():
    _, _, tle = _catalog()
    return json_response({"count": len(tle), "sats": tle}, max_age=600)


@bp.post("/api/games/breakup")
def breakup():
    rq = request.get_json(silent=True)
    if not isinstance(rq, dict):
        return _error("請求需為 JSON 物件")
    exe = _cli_path()
    if exe is None:
        return _error("伺服器未安裝碎片連鎖計算核心（debris_cli）", 503)
    cat_key, cat_path, _ = _catalog()
    key = cat_key + "|" + json.dumps(rq, sort_keys=True, ensure_ascii=False)
    bucket = int(time.time()) // CACHE_SECS
    hit = _CACHE.get(key)
    if hit and hit[0] == bucket:
        _CACHE.move_to_end(key)
        return _raw(hit[1].replace(b'"cached":false', b'"cached":true', 1))
    if not _BUSY.acquire(blocking=False):
        resp = _error("另一個碎片模擬正在計算", 429, retry_after=3)
        resp.headers["Retry-After"] = "3"
        return resp
    try:
        t = time.time()
        p = subprocess.run([str(exe)], input=json.dumps({"req": rq, "catalog": cat_path}).encode("utf-8"),
                           capture_output=True, timeout=CLI_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return _error("計算逾時，請減少碎片數或模擬年數", 503)
    finally:
        _BUSY.release()
    out = p.stdout.decode("utf-8", "replace").strip()
    try:
        d = json.loads(out)
    except ValueError:
        logger.error("debris_cli 輸出無法解析（結束碼 %s）：%s %s", p.returncode, out[:200], p.stderr[:300])
        return _error("計算失敗", 500)
    if p.returncode != 0 or "error" in d:
        return _error(d.get("error", "計算失敗"))
    s = d.get("summary", {})
    logger.info("碎片連鎖：母體 %s 碎片 %s %s 年 ×%s → 碎片 %s 碰撞 %s 期末 %s（%.1f 秒）",
                d.get("parent", {}).get("name"), rq.get("n_frag"), rq.get("years"), rq.get("accel"),
                s.get("fragments_total"), s.get("collisions"), s.get("alive_end"), time.time() - t)
    for k in [k for k, v in _CACHE.items() if v[0] != bucket or k == key]:
        del _CACHE[k]
    if len(_CACHE) >= CACHE_MAX:
        _CACHE.popitem(last=False)
    _CACHE[key] = (bucket, out.encode("utf-8"))
    return _raw(_CACHE[key][1])
