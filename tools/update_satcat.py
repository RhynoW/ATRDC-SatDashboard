#!/usr/bin/env python3
"""
update_satcat.py — 把 SATCAT（含 DECAY 再入日期）與 GCAT（建議名稱）寫進打包 DB，跟著資料庫一起發布
================================================================================
線上版（HF Space）連不到 CelesTrak，因此改在本機每日管線下載 SATCAT、寫入 slim DB 的
`satcat`／`satcat_meta` 資料表；線上版（api/satadmin.py）與 Rust 單機版（src/ephemeris.rs）優先讀這兩張表。

來源：Space-Track class/satcat（帳號取自父專案 .env 的 SPACE_TRACK_IDENTITY／SPACE_TRACK_PASSWORD，
或 SPACETRACK_USER／SPACETRACK_PASS）；沒有帳號或失敗時改用 CelesTrak satcat.csv。
依 Space-Track 規範 SATCAT 每天只抓一次：下載結果快取在暫存目錄，20 小時內重複執行直接用快取。

GCAT（Jonathan McDowell 的 General Catalog of Artificial Space Objects，planet4589.org，CC-BY）提供逐顆名稱與
各次發射的任務名稱，寫入 gcat_sat／gcat_launch／gcat_meta 表，供「建議名稱」辨識 OBJECT A/B/C；同樣每天一次。

SATCAT 每日異動（satcat_change／satcat_change_meta 表）：① Space-Track class/satcat_change（近 90 天的名稱／國際編號／
國家／發射日／DECAY 異動與 CHANGE_MADE 時間，每天一次）；② 新抓的 SATCAT 與前一份快照逐筆比對（補 OBJECT_TYPE 變更；
沒有 Space-Track 帳號時也能以 CelesTrak 比對），比對結果累積在暫存目錄的歷史檔（slim DB 每天重建，不能存在 DB 裡）。
每筆分類為 new（新編目）／named（OBJECT x → 正式名稱）／renamed／decayed（再入）／decay_fix（再入日期修正）／type／other。

SupGP（supgp／supgp_meta 表）：CelesTrak 營運商補充星曆（Starlink、OneWeb、Planet、Kuiper 等），供一箭多星身分比對；
每檔每天一次（CelesTrak 規範同一檔 2 小時內只能抓一次），失敗沿用 7 天內快取。

每日管線由 tools/merge_storymap_tle.py 結束時呼叫（失敗只警告、不中斷管線）。

用法：
  python tools/update_satcat.py                     # 目標＝scenario-advanced01/DB/space_db_slim.duckdb
  python tools/update_satcat.py --target scenario04/DB/space_db_slim.duckdb
  python tools/update_satcat.py --source celestrak  # 強制來源（auto／spacetrack／celestrak）
  python tools/update_satcat.py --max-age-hours 0   # 忽略快取（仍請遵守每天一次）
  python tools/update_satcat.py --no-gcat --no-changes
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import requests

APP = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = APP / "DB" / "space_db_slim.duckdb"
CACHE = Path(tempfile.gettempdir()) / "atrdc_satcat_pipeline.json"
ST = os.getenv("SATDASH_SPACETRACK_BASE", "https://www.space-track.org")
CT = os.getenv("SATDASH_CELESTRAK_BASE", "https://celestrak.org")
UA = "ATRDC-SatDashboard pipeline (satcat)"
CHG_CACHE = Path(tempfile.gettempdir()) / "atrdc_satcat_change_pipeline.json"   # Space-Track satcat_change 快取
DIFF_HIST = Path(tempfile.gettempdir()) / "atrdc_satcat_diff_history.json"      # 快照比對累積（slim DB 每天重建，另存）
CHANGE_DAYS = 90
COLS = ["norad_id", "name", "intl", "object_type", "ops_status", "owner", "launch", "decay", "period", "incl", "apogee", "perigee"]


def _num(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _creds() -> tuple[str, str] | None:
    try:
        from dotenv import load_dotenv
        load_dotenv(APP.parent / ".env")
    except ImportError:
        pass
    u = os.getenv("SPACE_TRACK_IDENTITY") or os.getenv("SPACETRACK_USER")
    p = os.getenv("SPACE_TRACK_PASSWORD") or os.getenv("SPACETRACK_PASS")
    return (u, p) if u and p else None


def fetch_spacetrack(user: str, pw: str) -> list[dict]:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.post(f"{ST}/ajaxauth/login", data={"identity": user, "password": pw}, timeout=60)
    if r.status_code != 200 or '"Failed"' in r.text:
        raise RuntimeError(f"Space-Track 登入失敗（HTTP {r.status_code}）")
    try:
        r = s.get(f"{ST}/basicspacedata/query/class/satcat/orderby/NORAD_CAT_ID%20asc/format/json", timeout=300)
        r.raise_for_status()
        rows = r.json()
    finally:
        try:
            s.get(f"{ST}/ajaxauth/logout", timeout=30)
        except Exception:
            pass
    out = []
    for m in rows:
        try:
            n = int(m.get("NORAD_CAT_ID"))
        except (TypeError, ValueError):
            continue
        out.append({"norad_id": n, "name": m.get("SATNAME") or m.get("OBJECT_NAME") or "", "intl": m.get("INTLDES") or m.get("OBJECT_ID") or "",
                    "object_type": m.get("OBJECT_TYPE") or "", "ops_status": "", "owner": m.get("COUNTRY") or "",
                    "launch": m.get("LAUNCH") or "", "decay": m.get("DECAY") or "", "period": _num(m.get("PERIOD")),
                    "incl": _num(m.get("INCLINATION")), "apogee": _num(m.get("APOGEE")), "perigee": _num(m.get("PERIGEE"))})
    return out


def fetch_celestrak() -> list[dict]:
    r = requests.get(f"{CT}/pub/satcat.csv", timeout=120, headers={"User-Agent": UA})
    r.raise_for_status()
    out = []
    for m in csv.DictReader(io.StringIO(r.text.lstrip("﻿"))):
        try:
            n = int(m.get("NORAD_CAT_ID", ""))
        except ValueError:
            continue
        out.append({"norad_id": n, "name": m.get("OBJECT_NAME", ""), "intl": m.get("OBJECT_ID", ""), "object_type": m.get("OBJECT_TYPE", ""),
                    "ops_status": m.get("OPS_STATUS_CODE", ""), "owner": m.get("OWNER", ""), "launch": m.get("LAUNCH_DATE", ""),
                    "decay": (m.get("DECAY_DATE") or "").strip(), "period": _num(m.get("PERIOD")), "incl": _num(m.get("INCLINATION")),
                    "apogee": _num(m.get("APOGEE")), "perigee": _num(m.get("PERIGEE"))})
    return out


def get_satcat(source: str, max_age_h: float) -> tuple[list[dict], str, str]:
    """回傳（資料, 來源, 抓取時間 ISO）；20 小時內用快取"""
    if max_age_h > 0 and CACHE.exists():
        try:
            c = json.loads(CACHE.read_text(encoding="utf-8"))
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(c["fetched_at"].replace("Z", "+00:00"))).total_seconds() / 3600
            if age < max_age_h and (source == "auto" or c["source"] == source) and len(c["rows"]) >= 1000:
                print(f"  使用快取（{c['source']}，{age:.1f} 小時前，{len(c['rows']):,} 筆）")
                return c["rows"], c["source"], c["fetched_at"]
        except Exception:
            pass
    errs = []
    order = ["spacetrack", "celestrak"] if source == "auto" else [source]
    for src in order:
        try:
            if src == "spacetrack":
                cr = _creds()
                if not cr:
                    errs.append("Space-Track：未設定帳號"); continue
                print("  下載 Space-Track SATCAT …")
                rows = fetch_spacetrack(*cr)
            else:
                print("  下載 CelesTrak satcat.csv …")
                rows = fetch_celestrak()
            if len(rows) < 1000:
                raise RuntimeError(f"只有 {len(rows)} 筆，疑似不完整")
            at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                record_diff(src, rows, at)
            except Exception as e:
                print(f"  ⚠ SATCAT 快照比對失敗（不影響寫入）：{e}")
            try:
                CACHE.write_text(json.dumps({"source": src, "fetched_at": at, "rows": rows}, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
            return rows, src, at
        except Exception as e:
            errs.append(f"{src}：{e}")
    # 下載都失敗：slim DB 每天重建，沒寫就會整天沒有 SATCAT → 退回 7 天內的舊快取
    try:
        c = json.loads(CACHE.read_text(encoding="utf-8"))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(c["fetched_at"].replace("Z", "+00:00"))).total_seconds() / 3600
        if age < 168 and len(c["rows"]) >= 1000:
            print(f"  ⚠ 下載失敗（{'；'.join(errs)}），改用 {age:.0f} 小時前的快取")
            return c["rows"], c["source"], c["fetched_at"]
    except Exception:
        pass
    raise RuntimeError("SATCAT 下載失敗：" + "；".join(errs))


def write_db(target: Path, rows: list[dict], source: str, fetched_at: str) -> None:
    con = duckdb.connect(str(target))
    try:
        con.begin()
        con.execute("""CREATE OR REPLACE TABLE satcat (norad_id INTEGER PRIMARY KEY, name VARCHAR, intl VARCHAR, object_type VARCHAR,
                       ops_status VARCHAR, owner VARCHAR, launch VARCHAR, decay VARCHAR, period DOUBLE, incl DOUBLE, apogee DOUBLE, perigee DOUBLE)""")
        seen, data = set(), []
        for r in rows:
            if r["norad_id"] in seen:
                continue
            seen.add(r["norad_id"])
            data.append([r.get(c) for c in COLS])
        con.executemany(f"INSERT INTO satcat VALUES ({','.join('?' * len(COLS))})", data)
        decayed = sum(1 for r in data if r[7])
        con.execute("CREATE OR REPLACE TABLE satcat_meta (source VARCHAR, fetched_at VARCHAR, count INTEGER, decayed INTEGER)")
        con.execute("INSERT INTO satcat_meta VALUES (?, ?, ?, ?)", [source, fetched_at, len(data), decayed])
        con.commit()
        con.execute("CHECKPOINT")
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    print(f"✅ SATCAT 寫入 {target.name}：{len(data):,} 筆（已再入 {decayed:,}），來源 {source}，{fetched_at}")


# ── SATCAT 每日異動 ────────────────────────────────────────────────────────────
NONAME_RE = re.compile(r"^(OBJECT [A-Z]{1,3}|TBA.*|UNKNOWN|)$")
FIELDS = [("NAME", "name"), ("INTLDES", "intl"), ("COUNTRY", "owner"), ("LAUNCH", "launch"), ("DECAY", "decay")]
CHG_COLS = ["norad_id", "change_made", "kind", "field", "previous", "current", "name", "intl", "source"]


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def classify(field: str, prev: str, cur: str) -> str:
    """一筆欄位異動的類別"""
    if field == "catalog":
        return "new"
    if field == "name":
        return "named" if NONAME_RE.match(prev.upper()) and not NONAME_RE.match(cur.upper()) else "renamed"
    if field == "decay":
        return "decayed" if not prev and cur else "decay_fix"
    if field == "object_type":
        return "type"
    return "other"


def _chg(n: int, at: str, field: str, prev: str, cur: str, name: str, intl: str, src: str) -> dict:
    return {"norad_id": n, "change_made": at, "kind": classify(field, prev, cur), "field": field, "previous": prev,
            "current": cur, "name": name, "intl": intl, "source": src}


def from_spacetrack(raw: list[dict]) -> list[dict]:
    """class/satcat_change 一列 → 每個有變動的欄位一筆；PREVIOUS_NAME 與 PREVIOUS_INTLDES 皆空＝新編目"""
    out = []
    for m in raw:
        try:
            n = int(m.get("NORAD_CAT_ID"))
        except (TypeError, ValueError):
            continue
        at = _s(m.get("CHANGE_MADE"))[:19]
        name, intl = _s(m.get("CURRENT_NAME")), _s(m.get("CURRENT_INTLDES"))
        if m.get("PREVIOUS_NAME") is None and m.get("PREVIOUS_INTLDES") is None:
            out.append(_chg(n, at, "catalog", "", name, name, intl, "spacetrack"))
            continue
        for k, f in FIELDS:
            a, b = _s(m.get("PREVIOUS_" + k)), _s(m.get("CURRENT_" + k))
            if a != b:
                out.append(_chg(n, at, f, a, b, name, intl, "spacetrack"))
    return out


def diff_snapshots(old: list[dict], new: list[dict], at: str, src: str) -> list[dict]:
    """前一份 SATCAT 快照 vs 新快照（同一來源）→ 異動清單"""
    o = {r["norad_id"]: r for r in old}
    at = at.replace("T", " ").replace("Z", "")[:19]
    out = []
    for r in new:
        n = r["norad_id"]
        p = o.get(n)
        nm, intl = _s(r.get("name")), _s(r.get("intl"))
        if p is None:
            out.append(_chg(n, at, "catalog", "", nm, nm, intl, "diff:" + src))
            continue
        for f in ("name", "intl", "owner", "launch", "decay", "object_type"):
            a, b = _s(p.get(f)), _s(r.get(f))
            if a != b:
                out.append(_chg(n, at, f, a, b, nm, intl, "diff:" + src))
    return out


def _load_hist() -> list[dict]:
    try:
        return json.loads(DIFF_HIST.read_text(encoding="utf-8"))
    except Exception:
        return []


def _cutoff(days: int) -> str:
    return datetime.fromtimestamp(time.time() - days * 86400, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def record_diff(src: str, rows: list[dict], at: str) -> int:
    """新下載的 SATCAT 與前一份快取比對，累積到 DIFF_HIST（只比同一來源；異動過多視為格式改變而略過）"""
    try:
        c = json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if c.get("source") != src or len(c.get("rows") or []) < 1000:
        return 0
    ch = diff_snapshots(c["rows"], rows, at, src)
    if len(ch) > 20000:
        print(f"  ⚠ 快照比對出 {len(ch):,} 筆異動，疑似格式改變，略過")
        return 0
    cut = _cutoff(CHANGE_DAYS)
    hist = [h for h in _load_hist() if h.get("change_made", "") >= cut] + ch
    DIFF_HIST.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    print(f"  SATCAT 快照比對：{len(ch):,} 筆異動（{c['fetched_at']} → {at}）")
    return len(ch)


def fetch_satcat_change(user: str, pw: str, days: int) -> list[dict]:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.post(f"{ST}/ajaxauth/login", data={"identity": user, "password": pw}, timeout=60)
    if r.status_code != 200 or '"Failed"' in r.text:
        raise RuntimeError(f"Space-Track 登入失敗（HTTP {r.status_code}）")
    try:
        r = s.get(f"{ST}/basicspacedata/query/class/satcat_change/CHANGE_MADE/%3Enow-{days}/orderby/CHANGE_MADE%20desc/format/json", timeout=300)
        r.raise_for_status()
        return r.json()
    finally:
        try:
            s.get(f"{ST}/ajaxauth/logout", timeout=30)
        except Exception:
            pass


def get_satcat_change(max_age_h: float, days: int = CHANGE_DAYS) -> tuple[list[dict], str | None]:
    """Space-Track satcat_change（快取 20 小時；失敗退回 7 天內快取；沒帳號回傳空清單）"""
    try:
        c = json.loads(CHG_CACHE.read_text(encoding="utf-8"))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(c["fetched_at"].replace("Z", "+00:00"))).total_seconds() / 3600
    except Exception:
        c, age = None, 1e9
    if c and age < max_age_h:
        print(f"  satcat_change 使用快取（{age:.1f} 小時前，{len(c['rows']):,} 筆）")
        return c["rows"], c["fetched_at"]
    cr = _creds()
    if not cr:
        print("  satcat_change：未設定 Space-Track 帳號，只用快照比對")
        return [], None
    try:
        print("  下載 Space-Track satcat_change …")
        rows = fetch_satcat_change(*cr, days)
        at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            CHG_CACHE.write_text(json.dumps({"fetched_at": at, "rows": rows}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return rows, at
    except Exception as e:
        if c and age < 168:
            print(f"  ⚠ satcat_change 下載失敗（{e}），改用 {age:.0f} 小時前的快取")
            return c["rows"], c["fetched_at"]
        raise


def merge_changes(st_rows: list[dict], diff_rows: list[dict], days: int = CHANGE_DAYS) -> list[dict]:
    """Space-Track 為準；快照比對只補 Space-Track 沒有的（OBJECT_TYPE 等）"""
    cut = _cutoff(days)
    key = lambda r: (r["norad_id"], r["field"], r["current"])
    have = {key(r) for r in st_rows}
    seen, out = set(), []
    for r in st_rows + [r for r in diff_rows if key(r) not in have]:
        k = key(r) + (r["previous"],)
        if r["change_made"] < cut or k in seen:
            continue
        seen.add(k)
        out.append(r)
    out.sort(key=lambda r: (r["change_made"], r["norad_id"]), reverse=True)
    return out


def update_changes(target: Path, satcat_rows: list[dict], max_age_h: float = 20.0) -> None:
    raw, at = get_satcat_change(max_age_h)
    st = from_spacetrack(raw)
    names = {r["norad_id"]: (_s(r.get("name")), _s(r.get("intl"))) for r in satcat_rows}
    for r in st:   # 以最新 SATCAT 名稱顯示（同一顆之後可能又改名）
        nm = names.get(r["norad_id"])
        if nm:
            r["name"], r["intl"] = nm[0] or r["name"], nm[1] or r["intl"]
    rows = merge_changes(st, _load_hist())
    n_st = sum(1 for r in rows if r["source"] == "spacetrack")
    con = duckdb.connect(str(target))
    try:
        con.begin()
        con.execute("""CREATE OR REPLACE TABLE satcat_change (norad_id INTEGER, change_made VARCHAR, kind VARCHAR, field VARCHAR,
                       previous VARCHAR, current VARCHAR, name VARCHAR, intl VARCHAR, source VARCHAR)""")
        con.executemany(f"INSERT INTO satcat_change VALUES ({','.join('?' * len(CHG_COLS))})", [[r[c] for c in CHG_COLS] for r in rows])
        con.execute("CREATE OR REPLACE TABLE satcat_change_meta (fetched_at VARCHAR, days INTEGER, count INTEGER, spacetrack INTEGER, diff INTEGER)")
        con.execute("INSERT INTO satcat_change_meta VALUES (?,?,?,?,?)",
                    [at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), CHANGE_DAYS, len(rows), n_st, len(rows) - n_st])
        con.commit()
        con.execute("CHECKPOINT")
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    kc: dict[str, int] = {}
    for r in rows:
        kc[r["kind"]] = kc.get(r["kind"], 0) + 1
    print(f"✅ SATCAT 異動寫入 {target.name}：{len(rows):,} 筆（Space-Track {n_st:,}、快照比對 {len(rows) - n_st:,}）："
          + "、".join(f"{k} {v}" for k, v in sorted(kc.items(), key=lambda x: -x[1])))


# ── GCAT（Jonathan McDowell, planet4589.org，CC-BY）：OBJECT A/B/C 的建議名稱來源 ──────────
GCAT = os.getenv("SATDASH_GCAT_BASE", "https://planet4589.org/space/gcat/tsv")
GCAT_CACHE = Path(tempfile.gettempdir()) / "atrdc_gcat_pipeline"


def _gcat_file(name: str, sub: str, max_age_h: float) -> tuple[str, bool]:
    """下載 GCAT TSV（快取 20 小時；失敗退回 7 天內快取）→（內容, 是否來自快取）"""
    GCAT_CACHE.mkdir(exist_ok=True)
    p = GCAT_CACHE / name
    age = (time.time() - p.stat().st_mtime) / 3600 if p.exists() else 1e9
    if age < max_age_h:
        return p.read_text(encoding="utf-8"), True
    try:
        print(f"  下載 GCAT {name} …")
        r = requests.get(f"{GCAT}/{sub}/{name}", timeout=180, headers={"User-Agent": UA})
        r.raise_for_status()
        r.encoding = "utf-8"
        if len(r.text) < 100_000:
            raise RuntimeError(f"{name} 只有 {len(r.text)} bytes")
        p.write_text(r.text, encoding="utf-8")
        return r.text, False
    except Exception as e:
        if age < 168:
            print(f"  ⚠ GCAT {name} 下載失敗（{e}），改用 {age:.0f} 小時前的快取")
            return p.read_text(encoding="utf-8"), True
        raise


def _tsv(text: str) -> tuple[list[str], list[list[str]], str]:
    lines = text.splitlines()
    head = lines[0].lstrip("#").split("\t")
    updated = next((l[1:].strip() for l in lines[1:3] if l.startswith("#")), "")
    rows = [[c.strip() for c in l.split("\t")] for l in lines if l and not l.startswith("#")]
    return [h.strip() for h in head], rows, updated


def update_gcat(target: Path, max_age_h: float = 20.0) -> None:
    sat_txt, _ = _gcat_file("satcat.tsv", "cat", max_age_h)
    lau_txt, _ = _gcat_file("launch.tsv", "launch", max_age_h)
    h, rows, sat_upd = _tsv(sat_txt)
    ix = {k: h.index(k) for k in ("Satcat", "Launch_Tag", "Piece", "Type", "Name", "PLName", "Owner")}
    sats, seen = [], set()
    for r in rows:
        try:
            n = int(r[ix["Satcat"]])
        except (ValueError, IndexError):
            continue
        if n in seen:
            continue
        seen.add(n)
        g = lambda k: r[ix[k]] if ix[k] < len(r) and r[ix[k]] != "-" else ""
        sats.append([n, g("Launch_Tag"), g("Piece"), g("Type"), g("Name"), g("PLName"), g("Owner")])
    h, rows, lau_upd = _tsv(lau_txt)
    ix = {k: h.index(k) for k in ("Launch_Tag", "Launch_Date", "LV_Type", "Flight", "Mission", "Agency")}
    launches = []
    for r in rows:
        g = lambda k: r[ix[k]] if ix[k] < len(r) and r[ix[k]] != "-" else ""
        if g("Launch_Tag"):
            launches.append([g("Launch_Tag"), g("Launch_Date"), g("LV_Type"), g("Flight"), g("Mission"), g("Agency")])
    con = duckdb.connect(str(target))
    try:
        con.begin()
        con.execute("CREATE OR REPLACE TABLE gcat_sat (norad_id INTEGER, launch_tag VARCHAR, piece VARCHAR, type VARCHAR, name VARCHAR, plname VARCHAR, owner VARCHAR)")
        con.executemany("INSERT INTO gcat_sat VALUES (?,?,?,?,?,?,?)", sats)
        con.execute("CREATE OR REPLACE TABLE gcat_launch (launch_tag VARCHAR, ldate VARCHAR, lv VARCHAR, flight VARCHAR, mission VARCHAR, agency VARCHAR)")
        con.executemany("INSERT INTO gcat_launch VALUES (?,?,?,?,?,?)", launches)
        con.execute("CREATE OR REPLACE TABLE gcat_meta (fetched_at VARCHAR, sat_count INTEGER, launch_count INTEGER, sat_updated VARCHAR, launch_updated VARCHAR)")
        con.execute("INSERT INTO gcat_meta VALUES (?,?,?,?,?)",
                    [datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), len(sats), len(launches), sat_upd, lau_upd])
        con.commit()
        con.execute("CHECKPOINT")
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    print(f"✅ GCAT 寫入 {target.name}：衛星 {len(sats):,} 筆（{sat_upd}）、發射 {len(launches):,} 筆（{lau_upd}）")


# ── SupGP：CelesTrak 營運商補充星曆（一箭多星身分比對用）────────────────────────
SUPGP_FILES = ["starlink", "oneweb", "planet", "kuiper", "iridium", "orbcomm", "ses", "intelsat", "telesat", "iss", "css", "gps", "glonass", "ast"]
SUPGP_CACHE = Path(tempfile.gettempdir()) / "atrdc_supgp_pipeline"
SUPGP_HIST = APP / "DB" / "supgp_history.duckdb"   # 歷史存檔（不隨 slim DB 發布、不被每日重建覆蓋；機動標註用）
_A5 = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # Alpha-5：A=10 … Z=33（無 I、O）


def _norad5(f: str) -> int | None:
    f = f.strip()
    if f.isdigit():
        return int(f)
    if len(f) == 5 and f[0] in _A5 and f[1:].isdigit():
        return (_A5.index(f[0]) + 10) * 10000 + int(f[1:])
    return None


def _tle_epoch(l1: str) -> datetime | None:
    try:
        yy, doy = int(l1[18:20]), float(l1[20:32])
    except ValueError:
        return None
    y = 2000 + yy if yy < 57 else 1900 + yy
    return datetime(y, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)


def archive_supgp(data: list[list[str]], fetched_at: str, path: Path | None = None) -> tuple[int, int]:
    """SupGP 附加寫入歷史檔（同一顆同一曆元只留第一筆）→（新增筆數, 總筆數）"""
    path = path or SUPGP_HIST
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for f, name, l1, l2 in data:
        n, ep = _norad5(l1[2:7]), _tle_epoch(l1)
        if n is not None and ep is not None:
            rows.append((n, ep.replace(tzinfo=None), f, name, l1, l2, fetched_at))
    con = duckdb.connect(str(path))
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS supgp_hist (norad_id INTEGER, epoch_utc TIMESTAMP, file VARCHAR, name VARCHAR,
                       line1 VARCHAR, line2 VARCHAR, fetched_at VARCHAR, PRIMARY KEY (norad_id, epoch_utc))""")
        before = con.execute("SELECT count(*) FROM supgp_hist").fetchone()[0]
        if rows:
            con.executemany("INSERT OR IGNORE INTO supgp_hist VALUES (?,?,?,?,?,?,?)", rows)
        total = con.execute("SELECT count(*) FROM supgp_hist").fetchone()[0]
        con.execute("CHECKPOINT")
    finally:
        con.close()
    return total - before, total


def _parse_tle3(text: str) -> list[tuple[str, str, str]]:
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    out = []
    for i in range(len(lines) - 1):
        if lines[i].startswith("1 ") and lines[i + 1].startswith("2 ") and len(lines[i]) >= 68 and len(lines[i + 1]) >= 68:
            name = lines[i - 1].strip() if i > 0 and not lines[i - 1].startswith(("1 ", "2 ")) else ""
            out.append((name, lines[i], lines[i + 1]))
    return out


def _supgp_file(f: str, max_age_h: float) -> tuple[list[tuple[str, str, str]], str]:
    """下載一個 SupGP 檔（CelesTrak 規範：同一檔 2 小時內只能抓一次；本管線快取 20 小時，失敗退回 7 天內快取）→（資料, 狀態）"""
    SUPGP_CACHE.mkdir(exist_ok=True)
    p = SUPGP_CACHE / f"{f}.tle"
    age = (time.time() - p.stat().st_mtime) / 3600 if p.exists() else 1e9
    cached = _parse_tle3(p.read_text(encoding="utf-8")) if p.exists() else []
    if age < max_age_h and cached:
        return cached, "快取"
    try:
        r = requests.get(f"{CT}/NORAD/elements/supplemental/sup-gp.php", params={"FILE": f, "FORMAT": "tle"}, timeout=120, headers={"User-Agent": UA})
        r.raise_for_status()
        rows = _parse_tle3(r.text)
        if rows:
            p.write_text(r.text, encoding="utf-8")
            return rows, "下載"
        msg = r.text.strip().splitlines()[0][:80] if r.text.strip() else "空白"
    except Exception as e:
        msg = str(e)[:80]
    if cached and age < 168:
        return cached, f"沿用 {age:.0f} 小時前快取（{msg}）"
    return [], f"無資料（{msg}）"


def update_supgp(target: Path, max_age_h: float = 20.0) -> None:
    data, used = [], []
    for f in SUPGP_FILES:
        rows, st = _supgp_file(f, max_age_h)
        print(f"  SupGP {f}：{len(rows):,} 筆（{st}）")
        if rows:
            used.append(f)
            data.extend([f, n, l1, l2] for n, l1, l2 in rows)
    con = duckdb.connect(str(target))
    try:
        con.begin()
        con.execute("CREATE OR REPLACE TABLE supgp (file VARCHAR, name VARCHAR, line1 VARCHAR, line2 VARCHAR)")
        if data:
            con.executemany("INSERT INTO supgp VALUES (?,?,?,?)", data)
        con.execute("CREATE OR REPLACE TABLE supgp_meta (fetched_at VARCHAR, files VARCHAR, count INTEGER)")
        con.execute("INSERT INTO supgp_meta VALUES (?,?,?)", [datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), ",".join(used), len(data)])
        con.commit()
        con.execute("CHECKPOINT")
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    print(f"✅ SupGP 寫入 {target.name}：{len(data):,} 筆（{', '.join(used) or '無'}）")
    if data:
        try:
            add, total = archive_supgp(data, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            print(f"  SupGP 歷史存檔 {SUPGP_HIST.name}：新增 {add:,} 筆、累計 {total:,} 筆")
        except Exception as e:
            print(f"⚠ SupGP 歷史存檔失敗（不影響 DB）：{e}")


def run(target: Path, source: str = "auto", max_age_h: float = 20.0, gcat: bool = True, changes: bool = True, supgp: bool = True) -> None:
    t0 = time.monotonic()
    rows, src, at = get_satcat(source, max_age_h)
    write_db(target, rows, src, at)
    if supgp:
        try:
            update_supgp(target, max_age_h)
        except Exception as e:
            print(f"⚠ SupGP 更新失敗（不影響 SATCAT）：{e}")
    if changes:
        try:
            update_changes(target, rows, max_age_h)
        except Exception as e:
            print(f"⚠ SATCAT 異動更新失敗（不影響 SATCAT）：{e}")
    if gcat:
        try:
            update_gcat(target, max_age_h)
        except Exception as e:
            print(f"⚠ GCAT 更新失敗（不影響 SATCAT）：{e}")
    print(f"  耗時 {time.monotonic() - t0:.0f} s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default=str(DEFAULT_TARGET))
    ap.add_argument("--source", default="auto", choices=["auto", "spacetrack", "celestrak"])
    ap.add_argument("--max-age-hours", type=float, default=20.0)
    ap.add_argument("--no-gcat", action="store_true", help="不更新 GCAT 建議名稱資料")
    ap.add_argument("--no-changes", action="store_true", help="不更新 SATCAT 每日異動")
    ap.add_argument("--no-supgp", action="store_true", help="不更新 SupGP 營運商補充星曆")
    args = ap.parse_args()
    target = Path(args.target)
    if not target.exists():
        print(f"❌ 目標 DB 不存在：{target}")
        return 1
    try:
        run(target, args.source, args.max_age_hours, gcat=not args.no_gcat, changes=not args.no_changes, supgp=not args.no_supgp)
    except Exception as e:
        print(f"❌ {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
