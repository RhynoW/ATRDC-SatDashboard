#!/usr/bin/env python3
"""
reentry_estimate.py — Cluster Samba／Tango 再入估算（零階 SGP4 掠過 ＋ 數值 Monte Carlo）＋ Salsa 回測
=====================================================================================================
輸出 scenario04/config/stories/data/reentry_cluster.json（供 /api/story/reentry 與 Markdown 匯出）。

用法：
  python tools/reentry_estimate.py                # 先抓最新 TLE（CelesTrak／Space-Track）→ 估算
  python tools/reentry_estimate.py --no-fetch --mc 30
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import duckdb  # noqa: E402
from scenario04.ingestion.db import resolve_db  # noqa: E402
from scenario04.physics.reentry_pass import reentry_estimate  # noqa: E402
from scenario04.physics.reentry_numeric import Body, monte_carlo, propagate  # noqa: E402

OUT = APP / "scenario04" / "config" / "stories" / "data" / "reentry_cluster.json"
ESA = {  # [ESA-reported] 2026-08-24 blog（CEST→UTC）
    26410: {"name": "Samba", "esa_t": "2026-08-31T21:41:54Z", "esa_unc_min": 10, "esa_region": "紐西蘭以北南太平洋"},
    26464: {"name": "Tango", "esa_t": "2026-09-01T21:33:20Z", "esa_unc_min": 10, "esa_region": "紐西蘭以北南太平洋"},
}
SALSA = {"norad": 26411, "name": "Salsa", "actual_t": "2024-09-08T18:47:00Z", "actual_region": "智利以西南太平洋",
         "actual_final_perigee_km": 112, "source": "ESA Salsa FAQ"}


def tle_at(norad: int, before: datetime | None = None):
    with duckdb.connect(str(resolve_db()), read_only=True) as con:
        q = "SELECT line1, line2, epoch_utc FROM raw_tle_archive WHERE norad_id=? AND line1 IS NOT NULL"
        args = [norad]
        if before:
            q += " AND epoch_utc <= ?"; args.append(before.replace(tzinfo=None))
        q += " ORDER BY epoch_utc DESC LIMIT 1"
        row = con.execute(q, args).fetchone()
    return row


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--mc", type=int, default=40)
    ap.add_argument("--skip-hindcast", action="store_true")
    a = ap.parse_args()
    t_run = datetime.now(timezone.utc).replace(microsecond=0)
    out = {"generated_at": t_run.isoformat().replace("+00:00", "Z"), "fetch": {}, "targets": {}, "hindcast": None,
           "method": {
               "stage1": "SGP4/SDP4 由最後 TLE 外推，逐圈近地點掠過（大地高極小），首次 ≤100 km 者為零階再入圈次；次衛星點=該時刻傳播位置",
               "stage2": "數值傳播：J2＋太陽／月球點質量（astropy 內建星曆）＋NRLMSIS 2.1 阻力（pymsis，CelesTrak 太空天氣）＋大氣共轉；"
                         "DOP853；再入判定為大地高 ≤80 km；Monte Carlo 抽樣迎風面積 3.8–6.6 m²（Cd 2.2、質量 550 kg）與密度尺度 lnN(0,0.3)",
               "caveat": "初始軌道為公開 TLE（TEME 視為慣性系），無任務遙測；屬 TLE-derived 估算，非操作級再入預報"}}

    if not a.no_fetch:
        from scenario04.ingestion.celestrak import refresh_to_db
        out["fetch"] = {str(k): {kk: vv for kk, vv in v.items() if kk not in ("line1", "line2")}
                        for k, v in refresh_to_db(list(ESA)).items()}
        print("fetch:", json.dumps(out["fetch"], ensure_ascii=False))

    # ── Salsa 回測校準：掃描密度尺度，取 |誤差| 最小者作為 Samba/Tango 之基準尺度 ──
    cal = {"scales": [0.3, 0.5, 0.7, 1.0, 1.4], "cases": []}
    actual = dt(SALSA["actual_t"])
    for lead_days in (13, 1):
        row = tle_at(SALSA["norad"], before=actual - timedelta(days=lead_days))
        if not row:
            continue
        l1, l2, ep = row
        errs = {}
        for sc in cal["scales"]:
            r = propagate(l1, l2, Body(), actual + timedelta(days=6), atmos_scale=sc)
            errs[str(sc)] = round((dt(r["t"]) - actual).total_seconds() / 3600.0, 2) if r.get("t") else None
        cal["cases"].append({"lead_days": lead_days, "tle_epoch": ep.isoformat(), "err_h_by_scale": errs})
        print(f"calib lead {lead_days}d: {errs}")
    # 以各案例 |err| 平均最小之 scale 為基準；線性內插至誤差 0 的 scale（限於掃描範圍內）
    best = 1.0
    if cal["cases"]:
        import numpy as _np
        sc = _np.array(cal["scales"]); m = _np.array([[c["err_h_by_scale"][str(x)] or 0 for x in cal["scales"]] for c in cal["cases"]]).mean(0)
        i = int(_np.argmin(_np.abs(m))); best = float(sc[i])
        # 零交叉內插
        for j in range(len(sc) - 1):
            if m[j] * m[j + 1] < 0:
                best = float(sc[j] + (sc[j + 1] - sc[j]) * (-m[j]) / (m[j + 1] - m[j])); break
        cal["mean_err_h_by_scale"] = dict(zip(map(str, cal["scales"]), [round(float(x), 2) for x in m]))
    cal["best_scale"] = round(best, 3)
    out["calibration"] = cal
    print("calibrated atmos scale:", cal["best_scale"])

    # ── Space-Track 60 日衰減預報／TIP（若有）──
    try:
        from scenario04.ingestion.celestrak import st_decay_forecast
        out["spacetrack_forecast"] = {}
        for n in ESA:
            rows = st_decay_forecast(n)
            out["spacetrack_forecast"][str(n)] = [{k: r.get(k) for k in ("_class", "MSG_EPOCH", "DECAY_EPOCH", "SOURCE", "MSG_TYPE", "WINDOW", "LAT", "LON")} for r in rows[:3]]
    except Exception as exc:  # noqa: BLE001
        out["spacetrack_forecast"] = {"error": str(exc)}

    for n, meta in ESA.items():
        l1, l2, ep = tle_at(n)
        t0 = time.time()
        s1 = reentry_estimate(l1, l2, days=25.0, interface_km=100.0)
        esa_t = dt(meta["esa_t"])
        rp = s1["reentry_pass"]
        s1_cmp = None
        if rp:
            d_h = (dt(rp["t"]) - esa_t).total_seconds() / 3600.0
            s1_cmp = {"dt_hours_vs_esa": round(d_h, 2), "lon_shift_deg_equiv": round(d_h * 15.0, 1)}
        t_end = esa_t + timedelta(days=6)
        nominal = propagate(l1, l2, Body(), t_end, atmos_scale=best)
        mc = monte_carlo(l1, l2, t_end, n=a.mc, base_scale=best)
        mc_cmp = None
        if mc.get("t_median"):
            mc_cmp = {"dt_hours_vs_esa": round((dt(mc["t_median"]) - esa_t).total_seconds() / 3600.0, 2)}
        out["targets"][str(n)] = {**meta, "norad": n, "tle_epoch": ep.isoformat(), "tle_age_days": round((t_run - ep.replace(tzinfo=timezone.utc)).total_seconds() / 86400, 1),
                                  "stage1": s1, "stage1_vs_esa": s1_cmp, "stage2_nominal": nominal, "stage2_mc": mc, "stage2_vs_esa": mc_cmp,
                                  "elapsed_s": round(time.time() - t0, 1)}
        print(f"{meta['name']}: TLE {ep.date()} | S1 {rp['t'] if rp else None} {rp['lat'] if rp else ''},{rp['lon'] if rp else ''} "
              f"| S2 nominal {nominal.get('t')} {nominal.get('lat')},{nominal.get('lon')} | MC median {mc.get('t_median')} "
              f"{mc.get('lat_median')},{mc.get('lon_median')} spread {mc.get('spread_hours')} h | {time.time()-t0:.0f}s")

    if not a.skip_hindcast:
        hc = {**SALSA, "cases": []}
        actual = dt(SALSA["actual_t"])
        for lead_days in (13, 1):
            row = tle_at(SALSA["norad"], before=actual - timedelta(days=lead_days))
            if not row:
                continue
            l1, l2, ep = row
            t0 = time.time()
            s1 = reentry_estimate(l1, l2, days=25.0, interface_km=100.0)
            rp = s1["reentry_pass"]
            nominal = propagate(l1, l2, Body(), actual + timedelta(days=6), atmos_scale=best)
            mc = monte_carlo(l1, l2, actual + timedelta(days=6), n=max(10, a.mc // 2), base_scale=best)
            case = {"lead_days": lead_days, "tle_epoch": ep.isoformat(),
                    "stage1_t": rp["t"] if rp else None, "stage1_latlon": [rp["lat"], rp["lon"]] if rp else None,
                    "stage1_err_h": round((dt(rp["t"]) - actual).total_seconds() / 3600.0, 2) if rp else None,
                    "stage2_t": nominal.get("t"), "stage2_latlon": [nominal.get("lat"), nominal.get("lon")],
                    "stage2_err_h": round((dt(nominal["t"]) - actual).total_seconds() / 3600.0, 2) if nominal.get("t") else None,
                    "mc_t_median": mc.get("t_median"), "mc_spread_h": mc.get("spread_hours"),
                    "mc_err_h": round((dt(mc["t_median"]) - actual).total_seconds() / 3600.0, 2) if mc.get("t_median") else None,
                    "elapsed_s": round(time.time() - t0, 1)}
            hc["cases"].append(case)
            print(f"Salsa hindcast lead {lead_days}d TLE {ep.date()}: S1 err {case['stage1_err_h']} h | S2 err {case['stage2_err_h']} h | MC err {case['mc_err_h']} h ±{case['mc_spread_h']}")
        out["hindcast"] = hc

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("→", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
