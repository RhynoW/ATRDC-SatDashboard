"""過頂預報 API（線上版）：多中心點、偵照角度、日照與日出日落、歷史 TLE 誤差估算，輸出 CSV。

群組與更名存在使用者瀏覽器，前端把選取的衛星（含群組路徑、感測器類型、偵照角度）與自訂名稱一併送來，
伺服器不保存。演算法見 physics/forecast.py（與 Rust 單機版 src/forecast.rs 同一套判定）。
結果暫存在記憶體（最多 5 筆），容器重啟即清除，請下載 CSV。
"""
from __future__ import annotations

import csv
import io
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import duckdb
from flask import Blueprint, Response, jsonify, render_template, request

from ..config import settings
from ..ingestion.db import resolve_db
from ..ingestion.index import get_sat_index
from ..physics import forecast as fc

logger = logging.getLogger(__name__)
bp = Blueprint("forecast", __name__)

MAX_DAYS = 62.0
MAX_SATS = int(os.getenv("FORECAST_MAX_SATS", "300"))
MAX_SITES = 30
_jobs: list[dict[str, Any]] = []
_lock = threading.Lock()


def _ut(t: datetime | None) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S") if t else ""


def _iso(t: datetime | None) -> str | None:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def _check_site(s: dict) -> str | None:
    name = str(s.get("name", "")).strip()
    try:
        lat, lon = float(s.get("lat")), float(s.get("lon"))
        r, alt = float(s.get("radius_km") or 0), float(s.get("alt_m") or 0)
    except (TypeError, ValueError):
        return f"「{name or '?'}」經緯度格式錯誤"
    if not name or len(name) > 64:
        return "中心點名稱需 1–64 字元"
    if not (-90 <= lat <= 90 and -180 <= lon <= 360):
        return f"「{name}」經緯度超出範圍"
    if not 0 <= r <= 3000:
        return f"「{name}」半徑需介於 0–3000 km"
    if not -500 <= alt <= 9000:
        return f"「{name}」高度需介於 −500–9000 m"
    return None


def _params(b: dict) -> dict:
    g = lambda k, d: b.get(k, d) if b.get(k) is not None else d
    return {"days": float(g("days", 31)), "mask_deg": float(g("mask_deg", 10)), "look_deg": float(g("look_deg", 30)),
            "look_override": bool(g("look_override", False)), "only_coverable": bool(g("only_coverable", True)),
            "daylight": str(g("daylight", "optical")), "sun_min_deg": float(g("sun_min_deg", 0)), "tz_hours": float(g("tz_hours", 8)),
            "error_estimate": bool(g("error_estimate", True)), "history_days": float(g("history_days", 60))}


def _validate(p: dict, sites: list) -> str | None:
    if not sites or len(sites) > MAX_SITES:
        return f"中心點需 1–{MAX_SITES} 個"
    for s in sites:
        e = _check_site(s)
        if e:
            return e
    if not 0 < p["days"] <= MAX_DAYS:
        return f"預報天數需介於 0–{MAX_DAYS:g} 天"
    if not -5 <= p["mask_deg"] <= 89:
        return "仰角門檻需介於 −5–89 度"
    if not 0 < p["look_deg"] <= 70:
        return "偵照角度需介於 0–70 度"
    if not -14 <= p["tz_hours"] <= 14:
        return "時區需介於 −14～+14"
    if not -18 <= p["sun_min_deg"] <= 60:
        return "太陽仰角門檻需介於 −18–60 度"
    if not 7 <= p["history_days"] <= 365:
        return "歷史天數需介於 7–365"
    if p["daylight"] not in ("none", "optical", "all"):
        return "日照篩選需為 none／optical／all"
    return None


OWNER_CODE = {"PRC": "中共", "US": "美國", "CIS": "俄羅斯", "FR": "法國", "JPN": "日本", "IND": "印度", "UK": "英國", "ESA": "歐洲太空總署", "GER": "德國", "IT": "義大利", "CA": "加拿大", "SPN": "西班牙", "SKOR": "南韓", "NKOR": "北韓", "ROC": "台灣", "ISRA": "以色列", "TURK": "土耳其", "AUS": "澳洲", "IRAN": "伊朗", "PAKI": "巴基斯坦"}


def owner_label(country: str | None, satcat_owner: str | None = "") -> str:
    """衛星隸屬預設值：先用系統國別分類（中國→中共）；分類為其他／不明（新發射、尚未收錄）時改用 SATCAT 擁有者代碼（與 Rust owner_label 相同）"""
    c = str(country or "").strip()
    if c == "中國":
        return "中共"
    if c not in ("", "不明", "—", "其他"):
        return c
    o = str(satcat_owner or "").strip()
    return OWNER_CODE.get(o, "" if o in ("", "TBD", "UNK") else o)


def _brief_map(b: dict, sats: list[dict]) -> dict[int, tuple[str, str]]:
    """簡要版輸出用：NORAD →（衛星隸屬, 解析度）。屬性表存在使用者瀏覽器，隨請求送來（attrs）；隸屬空白＝依 SATCAT 國別"""
    idx = get_sat_index()
    raw = b.get("attrs") or {}
    attrs = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if str(k).isdigit() and isinstance(v, dict):
                attrs[int(k)] = (str(v.get("owner") or "").strip()[:32], str(v.get("resolution") or "").strip()[:64])
    from .satadmin import get_satcat
    try:
        sc = get_satcat(fetch=False)
    except Exception:
        sc = {}
    out = {}
    for sj in sats:
        own, reso = attrs.get(sj["norad"], ("", ""))
        out[sj["norad"]] = (own or owner_label((idx.get(sj["norad"]) or {}).get("country"), (sc.get(sj["norad"]) or {}).get("owner")), reso)
    return out


SENSOR_RANK = {"電子偵察": 0, "光學": 1, "雷達": 2, "其他": 3}


def _brief_rows(r: dict) -> list[dict]:
    return [x for x in r["passes"] if x["coverable"] and x["img_start"] and x["img_end"]]


def brief_a_rows(r: dict) -> list[list]:
    """簡要版檔案 A：經過日期、衛星隸屬、衛星種類、國際名稱、解析度、開始／結束時間（可偵照時段，CSV 時區）——與 Rust brief_a_csv 相同"""
    tz, brief = r["tz"], r.get("brief", {})
    day = lambda t: t.astimezone(tz).strftime("%Y-%m-%d")
    rows = sorted(_brief_rows(r), key=lambda x: (x["site"], day(x["img_start"]), SENSOR_RANK.get(x["sensor"], 4), x["img_start"], x["norad_id"]))
    out = []
    for x in rows:
        own, reso = brief.get(x["norad_id"], ("", ""))
        out.append([day(x["img_start"]), own, x["sensor"], x["name"], reso, x["img_start"].astimezone(tz).strftime("%H:%M:%S"),
                    x["img_end"].astimezone(tz).strftime("%H:%M:%S"), x["norad_id"], x["site"]])
    return out


def brief_b_rows(r: dict) -> list[list]:
    """簡要版檔案 B：經過日期（M月D日）、國際名稱、過頂時間（HHMM-HHMM）、種類，依時間排序"""
    tz = r["tz"]
    out = []
    for x in sorted(_brief_rows(r), key=lambda x: (x["site"], x["img_start"], x["norad_id"])):
        a, b = x["img_start"].astimezone(tz), x["img_end"].astimezone(tz)
        out.append([f"{a.month}月{a.day}日", x["name"], f"{a:%H%M}-{b:%H%M}", x["sensor"], x["norad_id"], x["site"]])
    return out


BRIEF_A_HDR = ["經過日期", "衛星隸屬", "衛星種類", "國際名稱", "解析度", "開始時間", "結束時間", "NORAD", "中心點"]
BRIEF_B_HDR = ["經過日期", "國際名稱", "過頂時間", "種類", "NORAD", "中心點"]


def _select(b: dict, p: dict) -> tuple[list[dict], list[int]]:
    idx = get_sat_index()
    names = {int(k): str(v) for k, v in (b.get("names") or {}).items() if str(k).isdigit()}
    out: dict[int, dict] = {}
    missing = []
    items = list(b.get("sats") or []) + [{"norad": n} for n in (b.get("norads") or [])]
    for it in items:
        try:
            n = int(it.get("norad"))
        except (TypeError, ValueError):
            continue
        if n in out:
            continue
        s = idx.get(n)
        if not s or not s.get("line1"):
            missing.append(n)
            continue
        look = it.get("look_deg")
        try:
            look = float(look) if look is not None else None
        except (TypeError, ValueError):
            look = None
        if look is not None and not 0 < look <= 70:
            look = None
        use_g = look is not None and not p["look_override"]
        sensor = str(it.get("sensor") or "")
        out[n] = {"norad": n, "name": names.get(n) or s["name"], "orig": s["name"] if n in names else "",
                  "group": str(it.get("group") or "")[:200], "sensor": sensor if sensor in fc.SENSOR_LABEL else "",
                  "look": look if use_g else p["look_deg"], "look_from": "群組" if use_g else "預設",
                  "line1": s["line1"], "line2": s["line2"]}
    return list(out.values()), sorted(set(missing))


def _history(ids: list[int], days: float) -> dict[int, list[tuple[datetime, str, str]]]:
    db = resolve_db()
    if db is None:
        return {}
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out: dict[int, list] = {}
    with duckdb.connect(str(db), read_only=True) as con:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = con.execute(
                f"SELECT norad_id, line1, line2 FROM {settings.RAW_TABLE} WHERE norad_id IN ({','.join(map(str, chunk))}) "
                f"AND epoch_utc >= ? AND line1 IS NOT NULL AND line2 IS NOT NULL ORDER BY norad_id, epoch_utc", [since]).fetchall()
            for n, l1, l2 in rows:
                ep = fc.tle_epoch(l1)
                if ep is None:
                    continue
                v = out.setdefault(int(n), [])
                if v and (ep - v[-1][0]).total_seconds() < 43200:
                    continue
                v.append((ep, l1.strip(), l2.strip()))
    return out


def _run(job: dict, sats: list[dict], sites: list[dict], p: dict, start: datetime, brief: dict | None = None) -> None:
    try:
        span = p["days"] * 86400
        tz = timezone(timedelta(hours=p["tz_hours"]))
        targets = [fc.Target(name=str(s["name"]).strip(), lat=float(s["lat"]), lon=float(s["lon"]),
                             alt_m=float(s.get("alt_m") or 0), radius_km=float(s.get("radius_km") or 0)) for s in sites]
        job["stage"] = f"計算過頂（{len(sats)} 顆 × {len(targets)} 個中心點 × {p['days']:g} 天）"
        passes = []
        for sj in sats:
            passes.extend(fc.sat_passes(sj, targets, start, span, p, fc.sun_elevation))
            job["done"] += 1
        passes.sort(key=lambda x: (x["site"], x["tca"], x["norad_id"]))
        errors, fleet = [], []
        if p["error_estimate"] and sats:
            job["stage"] = f"歷史誤差估算（近 {p['history_days']:g} 天 TLE）"
            hist = _history([s["norad"] for s in sats], p["history_days"])
            allsmp, curves = [], {}
            for s in sats:
                smp = fc.sat_errors(hist.get(s["norad"], []))
                c = fc.summarize(smp)
                for e in c:
                    errors.append((s["norad"], s["name"], e))
                if c:
                    curves[s["norad"]] = c
                allsmp.extend(smp)
            fleet = fc.summarize(allsmp)
            errors.extend((0, "全部（合併）", e) for e in fleet)
            for x in passes:
                c = curves.get(x["norad_id"])
                c = c if c and len(c) >= 2 else fleet
                if x["lead_days"] is not None:
                    v = fc.est_err(c, max(x["lead_days"], 0.0))
                    x["est_err_s"] = round(v, 1) if v is not None else None
        job["stage"] = "計算日出日落"
        sun = []
        for tg in targets:
            for d in fc.local_days(start, span, tz):
                day0 = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc)
                r, s_, note = fc.sun_events(tg, day0)
                dl = (s_ - r).total_seconds() / 3600 if r and s_ and s_ > r else (24.0 if note == "極晝" else 0.0)
                sun.append({"site": tg.name, "date": d.isoformat(), "sunrise": r, "sunset": s_, "daylight_h": round(dl, 2), "note": note})
        per: dict[str, list[int]] = {}
        for x in passes:
            e = per.setdefault(x["site"], [0, 0, 0])
            e[0] += 1; e[1] += x["coverable"]; e[2] += x["sunlit"]
        job["result"] = {"passes": passes, "sun": sun, "errors": errors, "tz": tz, "brief": brief or {}, "summary": {
            "start": _iso(start), "end": _iso(start + timedelta(seconds=span)), "days": p["days"], "sats": len(sats), "sites": len(targets),
            "passes": len(passes), "per_site": [{"site": k, "passes": v[0], "coverable": v[1], "sunlit": v[2]} for k, v in sorted(per.items())],
            "not_in_catalog": job["missing"], "fleet_error": fleet}}
        job["state"] = "done"
        logger.info("過頂預報 %s 完成：%d 筆，%.1f s", job["id"], len(passes), time.time() - job["t0"])
    except Exception as e:
        logger.exception("過頂預報失敗")
        job["state"], job["error"] = "error", str(e)


@bp.get("/forecast")
def page_forecast():
    return render_template("forecast.html")


@bp.get("/api/forecast/sites")
def api_sites():
    """預設中心點（常用中心點存在瀏覽器）"""
    return jsonify({"sites": [{"name": "台北", "lat": settings.TAIPEI_LAT, "lon": settings.TAIPEI_LON, "alt_m": 10, "radius_km": 0, "note": ""}]})


@bp.post("/api/forecast/run")
def api_run():
    b = request.get_json(silent=True) or {}
    try:
        p = _params(b)
    except (TypeError, ValueError):
        return jsonify({"error": "參數格式錯誤"}), 400
    sites = b.get("sites") or []
    err = _validate(p, sites)
    if err:
        return jsonify({"error": err}), 400
    if not b.get("sats") and not b.get("norads"):
        return jsonify({"error": "請選擇群組或輸入 NORAD ID"}), 400
    sats, missing = _select(b, p)
    if not sats:
        return jsonify({"error": "選取的衛星都不在目前目錄中"}), 400
    if len(sats) > MAX_SATS:
        return jsonify({"error": f"線上版一次最多 {MAX_SATS} 顆衛星（目前 {len(sats)} 顆）；大量計算請用單機版"}), 400
    try:
        start = datetime.fromisoformat(str(b["start"]).replace("Z", "+00:00")).astimezone(timezone.utc) if b.get("start") else datetime.now(timezone.utc).replace(microsecond=0)
    except ValueError:
        return jsonify({"error": "開始時間格式錯誤"}), 400
    with _lock:
        if any(j["state"] == "running" for j in _jobs):
            return jsonify({"error": "已有預報在計算中，請稍候"}), 409
        job = {"id": "f" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:-3], "state": "running", "stage": "準備中",
               "done": 0, "total": len(sats), "error": None, "result": None, "missing": missing, "t0": time.time(),
               "created": datetime.now(timezone.utc), "params": dict(p, sites=[s["name"] for s in sites])}
        _jobs.append(job)
        del _jobs[:-5]
    threading.Thread(target=_run, args=(job, sats, sites, p, start, _brief_map(b, sats)), daemon=True).start()
    return jsonify({"ok": True, "job_id": job["id"], "sats": len(sats)})


def _row_json(x: dict) -> dict:
    return {k: (_iso(v) if isinstance(v, datetime) else v) for k, v in x.items()}


def _find(jid: str) -> dict | None:
    with _lock:
        return next((j for j in _jobs if j["id"] == jid), None)


@bp.get("/api/forecast/jobs/<jid>")
def api_job(jid: str):
    j = _find(jid)
    if not j:
        return jsonify({"error": "找不到此預報（服務重新啟動後會清除，請重新計算）"}), 404
    out = {k: j[k] for k in ("id", "state", "stage", "done", "total", "error", "params")}
    if j["result"]:
        r = j["result"]
        out["summary"] = r["summary"]
        out["preview"] = [_row_json(x) for x in r["passes"][:300]]
    return jsonify(out)


def _csv(rows: list[list], header: list[str], name: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.get("/api/forecast/jobs/<jid>/<fname>")
def api_job_file(jid: str, fname: str):
    j = _find(jid)
    if not j:
        return jsonify({"error": "找不到此預報"}), 404
    r = j["result"]
    if not r:
        return jsonify({"error": "預報尚未完成"}), 409
    tz = r["tz"]
    off = f"{tz.utcoffset(None).total_seconds() / 3600:+g}"
    lt = lambda t: t.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S") if t else ""
    hm = lambda t, f="%H:%M": t.astimezone(tz).strftime(f) if t else ""
    stamp = j["created"].astimezone(tz).strftime("%Y%m%d_%H%M")
    if fname == "passes.csv":
        sunmap = {(s["site"], s["date"]): s for s in r["sun"]}
        rows = []
        for x in r["passes"]:
            s = sunmap.get((x["site"], x["tca"].astimezone(tz).date().isoformat()), {})
            rows.append([x["site"], x["norad_id"], x["name"], x["orig_name"], x["group"], x["sensor"], lt(x["start"]), lt(x["end"]), lt(x["tca"]),
                         _ut(x["tca"]), x["duration_s"], x["max_el_deg"], x["az_tca_deg"], x["range_km"], x["min_off_nadir_deg"], x["look_deg"],
                         x["look_from"], x["swath_radius_km"], "是" if x["coverable"] else "否", lt(x["img_start"]), lt(x["img_end"]),
                         x["img_duration_s"], x["ground_dist_km"], x["sub_lat"], x["sub_lon"], x["alt_km"], x["direction"], x["sun_el_deg"],
                         "日照" if x["sunlit"] else "夜間", hm(s.get("sunrise")), hm(s.get("sunset")), _ut(x["tle_epoch"]),
                         "" if x["lead_days"] is None else x["lead_days"], "" if x["est_err_s"] is None else x["est_err_s"]])
        hdr = ["中心點", "NORAD", "名稱", "Space-Track原名", "群組", "感測器", f"開始(UTC{off})", f"結束(UTC{off})", f"最高點時間TCA(UTC{off})", "TCA(UTC)",
               "持續秒數", "最高仰角", "TCA方位角", "斜距km", "最小離天底角", "偵照角度", "偵照角度來源", "偵照地面半徑km", "可偵照",
               f"可偵照開始(UTC{off})", f"可偵照結束(UTC{off})", "可偵照秒數", "星下點距中心km", "星下點緯度", "星下點經度", "高度km", "升降軌",
               "太陽仰角", "日照", "當日日出", "當日日落", "TLE曆元(UTC)", "距TLE曆元天數", "預估時間誤差秒(p90)"]
        return _csv(rows, hdr, f"passes_{stamp}.csv")
    if fname == "sun.csv":
        rows = [[s["site"], s["date"], hm(s["sunrise"], "%H:%M:%S"), hm(s["sunset"], "%H:%M:%S"), s["daylight_h"], s["note"]] for s in r["sun"]]
        return _csv(rows, ["中心點", f"日期(UTC{off})", "日出", "日落", "日照時數", "備註"], f"sun_{stamp}.csv")
    if fname == "errors.csv":
        rows = [["ALL" if n == 0 else n, name, e["lead_days"], e["n"], e["pos_med_km"], e["pos_p90_km"], e["time_med_s"], e["time_p90_s"]]
                for n, name, e in r["errors"]]
        return _csv(rows, ["NORAD", "名稱", "提前天數", "樣本數", "位置誤差中位數km", "位置誤差p90km", "時間誤差中位數秒", "時間誤差p90秒"], f"tle_error_{stamp}.csv")
    if fname == "brief_a.csv":
        return _csv(brief_a_rows(r), BRIEF_A_HDR, f"brief_A_{stamp}.csv")
    if fname == "brief_b.csv":
        return _csv(brief_b_rows(r), BRIEF_B_HDR, f"brief_B_{stamp}.csv")
    return jsonify({"error": "檔案需為 passes.csv／sun.csv／errors.csv／brief_a.csv／brief_b.csv"}), 404
