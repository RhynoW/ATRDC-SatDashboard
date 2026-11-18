"""批次物件 CSV 匯入與結果 CSV 匯出（Python 與 Rust 以 testdata/batch_secondaries.csv 共用案例比對）。

匯入欄位（首列為表頭，不分大小寫；至少需 norad_id 或 line1＋line2）：
  norad_id, name, line1, line2, hbr_m, sigma_r_km, sigma_t_km, sigma_n_km
錯誤代碼：CSV_EMPTY、CSV_NO_ID_COLUMN、CSV_UNKNOWN_COLUMN、CSV_MISSING_ID_OR_TLE、CSV_BAD_NUMBER、
          CSV_BAD_TLE、CSV_DUPLICATE、CSV_TOO_MANY_ROWS
"""
from __future__ import annotations

import csv
import io

COLUMNS = ("norad_id", "name", "line1", "line2", "hbr_m", "sigma_r_km", "sigma_t_km", "sigma_n_km")
MAX_ROWS = 500


def parse_secondaries(text: str) -> dict:
    rows, errors = [], []
    lines = [ln for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    reader = csv.reader(lines)
    header = None
    seen = set()
    for lineno, rec in enumerate(reader, start=1):
        if not rec or all(not c.strip() for c in rec) or rec[0].lstrip().startswith("#"):
            continue
        if header is None:
            header = [c.strip().lower() for c in rec]
            unknown = [c for c in header if c not in COLUMNS]
            for c in unknown:
                errors.append({"line": lineno, "code": "CSV_UNKNOWN_COLUMN", "message": f"未知欄位 {c}（已忽略）"})
            if "norad_id" not in header and not {"line1", "line2"} <= set(header):
                errors.append({"line": lineno, "code": "CSV_NO_ID_COLUMN", "message": "需要 norad_id 欄或 line1＋line2 欄"})
                return {"rows": [], "errors": errors}
            continue
        if len(rows) >= MAX_ROWS:
            errors.append({"line": lineno, "code": "CSV_TOO_MANY_ROWS", "message": f"超過 {MAX_ROWS} 列，其後略過"})
            break
        d = {header[i]: rec[i].strip() for i in range(min(len(header), len(rec))) if header[i] in COLUMNS}
        row: dict = {}
        bad = False
        if d.get("norad_id"):
            try:
                row["norad_id"] = int(d["norad_id"])
            except ValueError:
                errors.append({"line": lineno, "code": "CSV_BAD_NUMBER", "message": f"norad_id 非整數：{d['norad_id']}"})
                bad = True
        l1, l2 = d.get("line1", ""), d.get("line2", "")
        if l1 or l2:
            if len(l1) != 69 or len(l2) != 69 or not l1.startswith("1 ") or not l2.startswith("2 "):
                errors.append({"line": lineno, "code": "CSV_BAD_TLE", "message": "TLE 兩行須各 69 字元，且以 '1 '、'2 ' 開頭"})
                bad = True
            else:
                row["line1"], row["line2"] = l1, l2
                if "norad_id" not in row:
                    try:
                        row["norad_id"] = int(l1[2:7])
                    except ValueError:
                        pass
        if not bad and "norad_id" not in row and "line1" not in row:
            errors.append({"line": lineno, "code": "CSV_MISSING_ID_OR_TLE", "message": "需要 norad_id 或 TLE"})
            bad = True
        for k in ("hbr_m", "sigma_r_km", "sigma_t_km", "sigma_n_km"):
            if d.get(k):
                try:
                    x = float(d[k])
                    if not x > 0:
                        raise ValueError
                    row[k] = x
                except ValueError:
                    errors.append({"line": lineno, "code": "CSV_BAD_NUMBER", "message": f"{k} 須為正數：{d[k]}"})
                    bad = True
        if d.get("name"):
            row["name"] = d["name"]
        if bad:
            continue
        key = row.get("norad_id"), row.get("line1")
        if key in seen:
            errors.append({"line": lineno, "code": "CSV_DUPLICATE", "message": f"重複物件 {row.get('norad_id')}（已略過）"})
            continue
        seen.add(key)
        row["line"] = lineno
        rows.append(row)
    if header is None:
        errors.append({"line": 0, "code": "CSV_EMPTY", "message": "沒有資料"})
    return {"rows": rows, "errors": errors}


EVENT_COLUMNS = ("primary_norad", "secondary_norad", "secondary_name", "tca_utc", "miss_km", "v_rel_kms",
                 "miss_r_km", "miss_t_km", "miss_n_km", "approach_angle_deg", "pc_foster", "pc_chan",
                 "applicability", "flag", "covariance_mode")


def events_to_csv(events: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(EVENT_COLUMNS)
    for e in events:
        pc = e.get("pc") or {}
        rtn = e.get("miss_rtn_km") or [None, None, None]
        w.writerow([e.get("primary_norad"), e.get("secondary_norad"), e.get("secondary_name"), e["tca_utc"],
                    f"{e['miss_km']:.6f}", f"{e['v_rel_kms']:.6f}",
                    *(f"{x:.6f}" for x in rtn), f"{e['approach_angle_deg']:.3f}",
                    "" if pc.get("foster") is None else f"{pc['foster']:.6e}",
                    "" if pc.get("chan") is None else f"{pc['chan']:.6e}",
                    e["applicability"]["status"], e["flag"], pc.get("covariance_mode", "")])
    return buf.getvalue()
