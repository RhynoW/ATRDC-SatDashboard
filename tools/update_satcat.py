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

每日管線由 tools/merge_storymap_tle.py 結束時呼叫（失敗只警告、不中斷管線）。

用法：
  python tools/update_satcat.py                     # 目標＝scenario-advanced01/DB/space_db_slim.duckdb
  python tools/update_satcat.py --target scenario04/DB/space_db_slim.duckdb
  python tools/update_satcat.py --source celestrak  # 強制來源（auto／spacetrack／celestrak）
  python tools/update_satcat.py --max-age-hours 0   # 忽略快取（仍請遵守每天一次）
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import requests

APP = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = APP / "DB" / "space_db_slim.duckdb"
CACHE = Path(tempfile.gettempdir()) / "atrdc_satcat_pipeline.json"
ST = os.getenv("SATDASH_SPACETRACK_BASE", "https://www.space-track.org")
CT = os.getenv("SATDASH_CELESTRAK_BASE", "https://celestrak.org")
UA = "ATRDC-SatDashboard pipeline (satcat)"
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


def run(target: Path, source: str = "auto", max_age_h: float = 20.0, gcat: bool = True) -> None:
    t0 = time.monotonic()
    rows, src, at = get_satcat(source, max_age_h)
    write_db(target, rows, src, at)
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
    args = ap.parse_args()
    target = Path(args.target)
    if not target.exists():
        print(f"❌ 目標 DB 不存在：{target}")
        return 1
    try:
        run(target, args.source, args.max_age_hours, gcat=not args.no_gcat)
    except Exception as e:
        print(f"❌ {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
