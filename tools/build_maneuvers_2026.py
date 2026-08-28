#!/usr/bin/env python3
"""
build_maneuvers_2026.py — 整合展示用「今年度機動偵測成果」預算
====================================================================
輸出 scenario04/config/stories/data/maneuvers_2026.json（部署用靜態成果）。

群組：GPS／北斗／Starlink／OneWeb（Δa 統計偵測：相鄰 TLE 半長軸跳變超過門檻）、
      大陸 ISR 遙感／大陸通訊（併入 prc_maneuver 管線既有 2026 成果 + Δa 偵測補足）。
門檻（依軌道域）：LEO(<2,000 km) |Δa|>0.5 km；MEO/GEO |Δa|>2.0 km；且相鄰 TLE 間隔 ≤ 5 天。
註：此為統計級偵測（非 ML 管線），供展示規模與月分佈；數字以「候選事件」計。
用法：python tools/build_maneuvers_2026.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from scenario04.ingestion.index import get_sat_index  # noqa: E402
from scenario04.api.story import GROUPS, group_members  # noqa: E402

FULL_DB = APP.parent / "space_db.duckdb"
PRC_CSV = APP.parent / "prc_maneuver" / "output" / "prc_maneuver_flagged_202601-202605.csv"
OUT = APP / "scenario04" / "config" / "stories" / "data" / "maneuvers_2026.json"
YEAR = 2026


def detect_group(con, ids: list[int]) -> pd.DataFrame:
    con.register("ids", pd.DataFrame({"norad_id": ids}))
    return con.execute(f"""
        with t as (
            select r.norad_id, r.epoch_utc, r.sma_km,
                   lag(r.epoch_utc) over (partition by r.norad_id order by r.epoch_utc) as ep0,
                   lag(r.sma_km)    over (partition by r.norad_id order by r.epoch_utc) as a0
            from raw_tle_archive r join ids using (norad_id)
            where r.epoch_utc >= timestamp '{YEAR}-01-01' and r.epoch_utc < timestamp '{YEAR+1}-01-01'
        )
        select norad_id, epoch_utc, ep0 as epoch_before, sma_km, sma_km - a0 as da_km,
               date_diff('hour', ep0, epoch_utc) as dt_h
        from t
        where ep0 is not null and date_diff('hour', ep0, epoch_utc) <= 120
          and abs(sma_km - a0) > case when sma_km - 6378.137 < 2000 then 0.5 else 2.0 end
    """).df()


def main() -> None:
    idx = get_sat_index()
    con = duckdb.connect(str(FULL_DB), read_only=True)
    prc = pd.read_csv(PRC_CSV) if PRC_CSV.exists() else pd.DataFrame()
    if len(prc):
        prc = prc[prc["flagged"] == True]  # noqa: E712
        prc["month"] = pd.to_datetime(prc["t_to"], utc=True).dt.strftime("%Y-%m")

    out = {"year": YEAR, "method": {
        "stat": "相鄰 TLE 半長軸跳變 |Δa| 超過門檻（LEO 0.5 km、MEO/GEO 2 km；間隔 ≤5 天）之候選事件",
        "prc": "prc_maneuver 管線 2026-01~05 旗標事件（da/di/de/dΩ 複合評分）",
        "dv": "等效 Δv = n·Δa/2（切向脈衝假設，n 為平均運動），非量測值",
        "caveat": "候選≠確認；替代解釋：TLE 品質波動／軌道決定更新、阻力模型誤差（LEO）、資料缺漏"},
        "groups": {}}
    for key, g in GROUPS.items():
        ids = group_members(idx, key)
        det = detect_group(con, ids)
        det["month"] = pd.to_datetime(det["epoch_utc"], utc=True).dt.strftime("%Y-%m")
        monthly = Counter(det["month"])
        per_sat = Counter(det["norad_id"])
        src = "stat"
        prc_part = pd.DataFrame()
        if key.startswith("prc_") and len(prc):
            prc_part = prc[prc["norad_id"].isin(ids)]
            if len(prc_part):
                src = "prc+stat"
        top = [{"norad": int(n), "name": idx[n]["name"], "events": int(c)}
               for n, c in per_sat.most_common(12) if n in idx]
        # 事件明細（|Δa| 最大前 10）：前後 TLE epoch、間隔、等效 Δv = n·Δa/2（切向脈衝假設）
        ev = det.reindex(det["da_km"].abs().sort_values(ascending=False).index).head(10)
        events = []
        for _, r in ev.iterrows():
            n = int(r["norad_id"])
            if n not in idx:
                continue
            mu, a = 398600.4418, float(r["sma_km"])
            n_rad = (mu / a ** 3) ** 0.5                 # rad/s
            dv = n_rad * float(r["da_km"]) / 2.0 * 1000.0  # m/s
            events.append({"norad": n, "name": idx[n]["name"],
                           "epoch_before": pd.Timestamp(r["epoch_before"]).strftime("%Y-%m-%dT%H:%M"),
                           "epoch_after": pd.Timestamp(r["epoch_utc"]).strftime("%Y-%m-%dT%H:%M"),
                           "gap_h": round(float(r["dt_h"]), 1), "da_km": round(float(r["da_km"]), 2),
                           "dv_ms": round(dv, 3),
                           "regime": "LEO" if a - 6378.137 < 2000 else ("MEO" if a - 6378.137 < 30000 else "GEO/IGSO")})
        n_tle_sats = con.execute(f"""select count(distinct norad_id) from raw_tle_archive r join ids using(norad_id)
            where epoch_utc >= timestamp '{YEAR}-01-01'""").fetchone()[0]
        out["groups"][key] = {
            "label": g["label"], "n_sats": len(ids), "n_with_tle_2026": int(n_tle_sats),
            "n_events": int(len(det)), "n_sats_with_event": int(len(per_sat)),
            "monthly": dict(sorted(monthly.items())),
            "top": top, "events": events, "source": src,
            "prc_pipeline": ({"n_events": int(len(prc_part)),
                              "n_sats": int(prc_part["norad_id"].nunique()),
                              "monthly": dict(sorted(Counter(prc_part["month"]).items())),
                              "severity": dict(Counter(prc_part["da_severity"]))}
                             if len(prc_part) else None),
        }
        print(f"{g['label']:14s} sats={len(ids):5d} tle2026={n_tle_sats:5d} "
              f"events={len(det):6d} sats_evt={len(per_sat):5d}  prc={len(prc_part)}")
    con.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("→", OUT)


if __name__ == "__main__":
    main()
