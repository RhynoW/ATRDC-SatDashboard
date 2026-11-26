"""一箭多星軌道驗證：同次發射所有物體的軌道分群與沿軌排序、歷史演化（機動偵測）、SupGP 身分比對（對應 Rust 單機版 src/launch.rs）。

- 分群：以目錄 TLE 的傾角（差 ≤ 0.5°）、升交點赤經（差 ≤ 2°）、平均高度（差 ≤ 50 km）分群；
  共同曆元（成員中最新的 TLE 曆元）以 SGP4 算緯度幅角 u，群內依相對參考星（群內最小 NORAD）的 Δu 排序，
  並列出相對漂移（Δ平均運動 × 360，°/日）與與參考星的距離。
- 歷史演化：資料庫歷史 TLE（共同曆元前 120 天）逐筆換算半長軸與近地／遠地高度；相鄰兩筆間隔 ≤ 5 天且半長軸增加 > 1 km
  視為升軌機動（大氣阻力只會降低軌道）；間隔 ≤ 2 天且減少 > 5 km 視為降軌；其餘依近 14 天斜率判斷衰減或穩定。
- SupGP：每日管線下載 CelesTrak 營運商補充星曆（supgp 表）。同發射或軌道相近（傾角差 ≤ 0.5°、平均運動差 ≤ 0.05 rev/日、
  升交點差 ≤ 5°）的星曆，傳播到該物體目錄 TLE 曆元比較位置，≤ 25 km 吻合、≤ 100 km 可能。吻合到別的編號、且本編號的
  SupGP 星曆落在那個編號的目錄位置（≤ 25 km）時，標為疑似目錄編號互換（cross-tag）。
與 Rust 同一套公式，結果逐筆一致。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, jsonify, render_template, request

from ..config import settings  # noqa: F401  RAW_TABLE
from ..ingestion.db import resolve_db
from ..ingestion.index import get_sat_index
from .obs import fmt_time, make_prop, rnd, tle_epoch, tle_norad
from .satadmin import get_satcat, intl_code
from .suggest import gcat

bp = Blueprint("launch", __name__)

MU = 398600.4418
RE = 6378.137
G_INCL, G_RAAN, G_ALT = 0.5, 2.0, 50.0
HIST_DAYS = 120
RAISE_KM, RAISE_DT = 1.0, 5.0
LOWER_KM, LOWER_DT = 5.0, 2.0
DECAY_SLOPE = -0.05
SERIES_MAX = 300
SUP_INCL, SUP_N, SUP_RAAN = 0.5, 0.05, 5.0
SUP_OK, SUP_MAYBE = 25.0, 100.0
_sup: dict[str, Any] = {"mtime": None, "rows": [], "meta": None}


def elements(l1: str, l2: str) -> dict | None:
    """TLE 文字 → 平均根數與衍生量（與 Rust 相同切法）"""
    try:
        incl = float(l2[8:16]); raan = float(l2[17:25]); ecc = float("0." + l2[26:33].strip())
        argp = float(l2[34:42]); ma = float(l2[43:51]); n = float(l2[52:63])
    except (ValueError, IndexError):
        return None
    if n <= 0:
        return None
    s = l1[53:61]
    try:
        bstar = float(("-" if s[0] == "-" else "") + "0." + s[1:6].strip()) * 10 ** int(s[6:8])
    except (ValueError, IndexError):
        bstar = 0.0
    w = n * 2 * math.pi / 86400.0
    a = (MU / (w * w)) ** (1 / 3)
    per, apo = a * (1 - ecc) - RE, a * (1 + ecc) - RE
    return {"incl": incl, "raan": raan, "ecc": ecc, "argp": argp, "ma": ma, "n": n, "bstar": bstar, "a": a,
            "perigee": per, "apogee": apo, "alt": (per + apo) / 2, "period": 1440.0 / n}


def _arg_lat(p, t: datetime) -> tuple[float, tuple] | None:
    """緯度幅角 u（°）；速度以 ±0.5 s 差分（與 Rust 相同）"""
    r = p.pos(t)
    a, b = p.pos(t - timedelta(milliseconds=500)), p.pos(t + timedelta(milliseconds=500))
    if r is None or a is None or b is None:
        return None
    v = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    h = (r[1] * v[2] - r[2] * v[1], r[2] * v[0] - r[0] * v[2], r[0] * v[1] - r[1] * v[0])
    nx, ny = -h[1], h[0]
    rn = math.sqrt(r[0] * r[0] + r[1] * r[1] + r[2] * r[2])
    nn = math.hypot(nx, ny)
    if nn < 1e-9:
        u = math.degrees(math.atan2(r[1], r[0])) % 360.0
    else:
        u = math.degrees(math.acos(max(-1.0, min(1.0, (nx * r[0] + ny * r[1]) / (nn * rn)))))
        if r[2] < 0:
            u = 360.0 - u
    return u, r


def _wrap(d: float) -> float:
    d = (d + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def _sup_load() -> dict:
    db = resolve_db()
    if db is None:
        return _sup
    try:
        mt = db.stat().st_mtime
    except OSError:
        return _sup
    if mt == _sup["mtime"]:
        return _sup
    import duckdb
    rows, meta = [], None
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            tabs = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
            if "supgp" in tabs:
                for f, nm, l1, l2 in con.execute("SELECT file, name, line1, line2 FROM supgp ORDER BY file, line1").fetchall():
                    e = elements(l1 or "", l2 or "")
                    if e:
                        rows.append({"file": f or "", "name": (nm or "").strip(), "line1": l1, "line2": l2, "norad": tle_norad(l1),
                                     "tag": intl_code(l1)[:8], "el": e})
            if "supgp_meta" in tabs:
                r = con.execute("SELECT fetched_at, files, count FROM supgp_meta LIMIT 1").fetchone()
                if r:
                    meta = {"fetched_at": r[0], "files": r[1], "count": r[2]}
    except Exception:
        pass
    _sup.update(mtime=mt, rows=rows, meta=meta)
    return _sup


def _history(ids: list[int], t0: datetime) -> dict[int, list[tuple[datetime, str, str]]]:
    db = resolve_db()
    if db is None or not ids:
        return {}
    import duckdb
    since = (t0 - timedelta(days=HIST_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    out: dict[int, list] = {}
    with duckdb.connect(str(db), read_only=True) as con:
        rows = con.execute(
            f"SELECT norad_id, line1, line2 FROM {settings.RAW_TABLE} WHERE norad_id IN ({','.join(map(str, ids))}) "
            f"AND epoch_utc >= TIMESTAMP '{since}' AND line1 IS NOT NULL AND line2 IS NOT NULL ORDER BY norad_id, epoch_utc, line1").fetchall()
    for n, l1, l2 in rows:
        ep = tle_epoch(l1)
        if ep is None:
            continue
        v = out.setdefault(int(n), [])
        if v and v[-1][0] == ep:
            continue
        v.append((ep, l1.strip(), l2.strip()))
    return out


def evolution(hist: list[tuple[datetime, str, str]]) -> dict:
    pts = []
    for ep, l1, l2 in hist:
        e = elements(l1, l2)
        if e:
            pts.append((ep, e))
    out: dict[str, Any] = {"points": len(pts), "status": "insufficient", "raises": [], "lowers": [], "raise_total_km": 0.0,
                           "slope_km_day": None, "a_first": None, "a_last": None, "series": []}
    if not pts:
        return out
    out["a_first"], out["a_last"] = rnd(pts[0][1]["a"], 2), rnd(pts[-1][1]["a"], 2)
    out["first_epoch"], out["last_epoch"] = fmt_time(pts[0][0]), fmt_time(pts[-1][0])
    tot = 0.0
    for (t1, e1), (t2, e2) in zip(pts, pts[1:]):
        dt = (t2 - t1).total_seconds() / 86400.0
        da = e2["a"] - e1["a"]
        if dt <= RAISE_DT and da > RAISE_KM:
            out["raises"].append({"epoch": fmt_time(t2), "da_km": rnd(da, 2)})
            tot += da
        elif dt <= LOWER_DT and da < -LOWER_KM:
            out["lowers"].append({"epoch": fmt_time(t2), "da_km": rnd(da, 2)})
    out["raise_total_km"] = rnd(tot, 2)
    tl = pts[-1][0]
    rec = [(p[0], p[1]["a"]) for p in pts if (tl - p[0]).total_seconds() <= 14 * 86400]
    if len(rec) >= 3 and (rec[-1][0] - rec[0][0]).total_seconds() >= 2 * 86400:
        xs = [(t - rec[0][0]).total_seconds() / 86400.0 for t, _ in rec]
        ys = [a for _, a in rec]
        sx = sy = 0.0          # 逐項累加（不用 sum()：Python 3.12 起為補償加總，與 Rust 不同）
        for x, y in zip(xs, ys):
            sx += x
            sy += y
        mx, my = sx / len(xs), sy / len(ys)
        sxx = sxy = 0.0
        for x, y in zip(xs, ys):
            sxx += (x - mx) * (x - mx)
            sxy += (x - mx) * (y - my)
        slope = sxy / sxx if sxx > 0 else 0.0
        out["slope_km_day"] = rnd(slope, 3)
    if out["raises"]:
        out["status"] = "maneuvering"
    elif out["lowers"]:
        out["status"] = "lowering"
    elif out["slope_km_day"] is not None:
        out["status"] = "decaying" if out["slope_km_day"] < DECAY_SLOPE else "stable"
    k = max(1, math.ceil(len(pts) / SERIES_MAX))
    sel = pts[::k]
    if sel[-1] is not pts[-1]:
        sel.append(pts[-1])
    out["series"] = [[fmt_time(t), rnd(e["alt"], 2), rnd(e["perigee"], 2), rnd(e["apogee"], 2)] for t, e in sel]
    return out


def supgp_match(n: int, el: dict, prop, ep: datetime, tag: str, sup: list[dict], cat_prop=None) -> dict | None:
    if not sup or prop is None:
        return None
    r0 = prop.pos(ep)
    if r0 is None:
        return None
    best = None
    for s in sup:
        e = s["el"]
        near = abs(e["incl"] - el["incl"]) <= SUP_INCL and abs(e["n"] - el["n"]) <= SUP_N and abs(_wrap(e["raan"] - el["raan"])) <= SUP_RAAN
        if s["tag"] != tag and not near:
            continue
        p = make_prop(s["line1"], s["line2"])
        r = p.pos(ep) if p else None
        if r is None:
            continue
        dx, dy, dz = r[0] - r0[0], r[1] - r0[1], r[2] - r0[2]
        d = rnd(math.sqrt(dx * dx + dy * dy + dz * dz), 2)
        key = (d, s["file"], s["name"], s["line1"])
        if best is None or key < best[0]:
            best = (key, s)
    if best is None:
        return None
    d, s = best[0][0], best[1]
    out = {"name": s["name"], "file": s["file"], "norad": s["norad"], "epoch": fmt_time(tle_epoch(s["line1"])) if tle_epoch(s["line1"]) else None,
           "dist_km": d, "status": "matched" if d <= SUP_OK else "possible" if d <= SUP_MAYBE else "none",
           "norad_consistent": s["norad"] == n if s["norad"] is not None else None, "own_dist_km": None, "swap_suspected": False}
    # 本物體編號的 SupGP 星曆在哪裡；若吻合的是別的編號，且本編號的星曆正落在那個編號的目錄位置 → 疑似目錄編號互換（cross-tag）
    own = next((x for x in sup if x["norad"] == n), None)
    if own is not None:
        po = make_prop(own["line1"], own["line2"])
        ro = po.pos(ep) if po else None
        if ro is not None:
            dx, dy, dz = ro[0] - r0[0], ro[1] - r0[1], ro[2] - r0[2]
            out["own_dist_km"] = rnd(math.sqrt(dx * dx + dy * dy + dz * dz), 2)
            if out["status"] == "matched" and s["norad"] is not None and s["norad"] != n and cat_prop is not None:
                q = cat_prop(s["norad"])
                rq = q.pos(ep) if q else None
                if rq is not None:
                    dx, dy, dz = ro[0] - rq[0], ro[1] - rq[1], ro[2] - rq[2]
                    sd = rnd(math.sqrt(dx * dx + dy * dy + dz * dz), 2)
                    out["swap_dist_km"] = sd
                    out["swap_suspected"] = sd <= SUP_OK
    return out


def analyze(tag: str, idx: dict, sc: dict, g: dict, sup: dict, names: dict[int, str] | None = None,
            history: dict[int, list] | None = None) -> dict:
    names = names or {}
    mem = sorted(n for n, s in idx.items() if intl_code(s.get("line1") or "")[:8] == tag)
    rows, props = [], {}
    for n in mem:
        s = idx[n]
        el = elements(s["line1"], s["line2"])
        p = make_prop(s["line1"], s["line2"])
        ep = tle_epoch(s["line1"])
        if el is None or p is None or ep is None:
            continue
        props[n] = (p, ep, el)
        scr = sc.get(n) or {}
        rows.append({"norad": n, "name": names.get(n) or s["name"].strip(), "intl": intl_code(s["line1"]), "object_type": scr.get("object_type") or "",
                     "official_name": scr.get("name") or "", "epoch": fmt_time(ep),
                     "incl": rnd(el["incl"], 4), "raan": rnd(el["raan"], 4), "ecc": rnd(el["ecc"], 7), "n": rnd(el["n"], 8),
                     "bstar": float(f"{el['bstar']:.5e}"), "a_km": rnd(el["a"], 2), "perigee_km": rnd(el["perigee"], 2),
                     "apogee_km": rnd(el["apogee"], 2), "alt_km": rnd(el["alt"], 2), "period_min": rnd(el["period"], 3)})
    gone = [{"norad": n, "name": r.get("name") or "", "object_type": r.get("object_type") or "", "decay": r.get("decay") or ""}
            for n, r in sorted(sc.items()) if (r.get("intl") or "")[:8] == tag and n not in props]
    la = (g.get("launch") or {}).get(tag)
    out: dict[str, Any] = {"tag": tag, "launch": la, "members": rows, "not_in_catalog": gone, "groups": [], "t0": None,
                           "supgp_meta": sup.get("meta"), "params": {"group_incl_deg": G_INCL, "group_raan_deg": G_RAAN, "group_alt_km": G_ALT,
                           "hist_days": HIST_DAYS, "raise_km": RAISE_KM, "lower_km": LOWER_KM, "sup_ok_km": SUP_OK, "sup_maybe_km": SUP_MAYBE}}
    if not rows:
        return out
    t0 = max(props[r["norad"]][1] for r in rows)
    out["t0"] = fmt_time(t0)
    # 分群（依 NORAD 順序，與群內第一顆比較）
    grp: list[list[dict]] = []
    for r in rows:
        el = props[r["norad"]][2]
        for gl in grp:
            e0 = props[gl[0]["norad"]][2]
            if abs(el["incl"] - e0["incl"]) <= G_INCL and abs(_wrap(el["raan"] - e0["raan"])) <= G_RAAN and abs(el["alt"] - e0["alt"]) <= G_ALT:
                gl.append(r)
                break
        else:
            grp.append([r])
    grp.sort(key=lambda gl: (-len(gl), gl[0]["norad"]))
    for gi, gl in enumerate(grp, 1):
        ref = gl[0]["norad"]
        uref = _arg_lat(props[ref][0], t0)
        for r in gl:
            r["group"] = f"G{gi}"
            ul = _arg_lat(props[r["norad"]][0], t0)
            if ul is None or uref is None:
                r["du_deg"] = r["dist_ref_km"] = None
            else:
                r["du_deg"] = rnd(_wrap(ul[0] - uref[0]), 3)
                dx, dy, dz = ul[1][0] - uref[1][0], ul[1][1] - uref[1][1], ul[1][2] - uref[1][2]
                r["dist_ref_km"] = rnd(math.sqrt(dx * dx + dy * dy + dz * dz), 2)
            r["drift_deg_day"] = rnd((props[r["norad"]][2]["n"] - props[ref][2]["n"]) * 360.0, 4)
        order = sorted(gl, key=lambda r: (-(r["du_deg"] if r["du_deg"] is not None else -1e9), r["norad"]))
        for k, r in enumerate(order, 1):
            r["order"] = k
        alts = [props[r["norad"]][2]["alt"] for r in gl]
        out["groups"].append({"id": f"G{gi}", "count": len(gl), "ref": ref, "order": [r["norad"] for r in order],
                              "incl": rnd(props[ref][2]["incl"], 3), "raan": rnd(props[ref][2]["raan"], 3),
                              "alt_min_km": rnd(min(alts), 2), "alt_max_km": rnd(max(alts), 2),
                              "types": sorted({r["object_type"] or "UNKNOWN" for r in gl})})
    hist = history if history is not None else _history([r["norad"] for r in rows], t0)
    cache: dict[int, Any] = {}

    def cat_prop(m: int):
        if m not in cache:
            s = idx.get(m)
            cache[m] = make_prop(s["line1"], s["line2"]) if s and s.get("line1") and s.get("line2") else None
        return cache[m]

    srows = sup.get("rows") or []
    for r in rows:
        r["evolution"] = evolution(hist.get(r["norad"], []))
        p, ep, el = props[r["norad"]]
        r["supgp"] = supgp_match(r["norad"], el, p, ep, tag, srows, cat_prop)
    return out


def resolve_tag(q: str, idx: dict, sc: dict) -> str | None:
    q = q.strip().upper()
    if len(q) >= 8 and q[:4].isdigit() and q[4] == "-" and q[5:8].isdigit():
        return q[:8]
    if q.isdigit():
        n = int(q)
        s = idx.get(n)
        t = intl_code(s["line1"])[:8] if s else ((sc.get(n) or {}).get("intl") or "")[:8]
        return t if len(t) == 8 else None
    return None


def recent(idx: dict, sc: dict, g: dict, days: int = 60, now: datetime | None = None) -> list[dict]:
    """近期多物體發射（目錄中同發射 ≥ 2 個物體），新到舊"""
    now = now or datetime.now(timezone.utc)
    cnt: dict[str, int] = {}
    for s in idx.values():
        t = intl_code(s.get("line1") or "")[:8]
        if len(t) == 8:
            cnt[t] = cnt.get(t, 0) + 1
    y0 = (now - timedelta(days=days)).year
    ldate: dict[str, str] = {}
    for n in sorted(sc):
        r = sc[n]
        t = (r.get("intl") or "")[:8]
        if t in cnt and r.get("launch") and t not in ldate:
            ldate[t] = r["launch"]
    out = []
    for t, c in cnt.items():
        if c < 2 or not t[:4].isdigit() or int(t[:4]) < y0:
            continue
        la = (g.get("launch") or {}).get(t) or {}
        ld = ldate.get(t)
        if ld and ld < (now - timedelta(days=days)).strftime("%Y-%m-%d"):
            continue
        out.append({"tag": t, "count": c, "launch_date": ld, "lv": la.get("lv") or "", "mission": la.get("mission") or la.get("flight") or ""})
    out.sort(key=lambda x: (x["launch_date"] or "", x["tag"]), reverse=True)
    return out


@bp.get("/launch")
def page_launch():
    return render_template("launch.html")


@bp.get("/api/launch")
def api_launch():
    idx, sc = get_sat_index(), get_satcat(fetch=False)
    tag = resolve_tag(request.args.get("tag") or request.args.get("norad") or "", idx, sc)
    if not tag:
        return jsonify({"error": "請輸入國際編號（例如 2026-260）或 NORAD 編號"}), 400
    return jsonify(analyze(tag, idx, sc, gcat(), _sup_load()))


@bp.get("/api/launch/recent")
def api_recent():
    try:
        days = max(7, min(int(request.args.get("days", 60)), 730))
    except ValueError:
        days = 60
    return jsonify({"results": recent(get_sat_index(), get_satcat(fetch=False), gcat(), days), "days": days, "supgp": _sup_load()["meta"]})
