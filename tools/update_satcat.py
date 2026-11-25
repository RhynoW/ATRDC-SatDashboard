#!/usr/bin/env python3
"""
update_satcat.py — 把 SATCAT（含 DECAY 再入日期）寫進打包 DB，跟著資料庫一起發布
================================================================================
線上版（HF Space）連不到 CelesTrak，因此改在本機每日管線下載 SATCAT、寫入 slim DB 的
`satcat`／`satcat_meta` 資料表；線上版（api/satadmin.py）與 Rust 單機版（src/ephemeris.rs）優先讀這兩張表。

來源：Space-Track class/satcat（帳號取自父專案 .env 的 SPACE_TRACK_IDENTITY／SPACE_TRACK_PASSWORD，
或 SPACETRACK_USER／SPACETRACK_PASS）；沒有帳號或失敗時改用 CelesTrak satcat.csv。
依 Space-Track 規範 SATCAT 每天只抓一次：下載結果快取在暫存目錄，20 小時內重複執行直接用快取。

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


def run(target: Path, source: str = "auto", max_age_h: float = 20.0) -> None:
    t0 = time.monotonic()
    rows, src, at = get_satcat(source, max_age_h)
    write_db(target, rows, src, at)
    print(f"  耗時 {time.monotonic() - t0:.0f} s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default=str(DEFAULT_TARGET))
    ap.add_argument("--source", default="auto", choices=["auto", "spacetrack", "celestrak"])
    ap.add_argument("--max-age-hours", type=float, default=20.0)
    args = ap.parse_args()
    target = Path(args.target)
    if not target.exists():
        print(f"❌ 目標 DB 不存在：{target}")
        return 1
    try:
        run(target, args.source, args.max_age_hours)
    except Exception as e:
        print(f"❌ {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
