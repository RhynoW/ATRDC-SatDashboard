"""近地點掠過分析器（零階再入估算，SGP4/SDP4）。

對高橢圓軌道（Cluster 等），再入發生在某一次近地點掠過；本模組由最後一筆 TLE 以 SGP4/SDP4 外推，
逐圈列出近地點時刻、大地高、次衛星點、速度，並判定首次低於「進入介面」高度的那一圈。

限制：SGP4/SDP4 為解析平均要素模型，深空攝動（月日）為近似、100 km 近地點之阻力不可靠；
外推超過數天時再入圈次可能誤判一圈（Cluster 每圈約 53.5 h，對應經度差約 80°）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4.api import Satrec, jday

from .coords import eci_to_llh_batch

MU = 398600.4418
RE = 6378.137


def _state(sat: Satrec, t: datetime):
    jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6)
    e, r, v = sat.sgp4(jd, fr)
    return e, np.array(r), np.array(v)


def _alt(sat: Satrec, t: datetime) -> float | None:
    e, r, _ = _state(sat, t)
    if e:
        return None
    return float(eci_to_llh_batch(r[None, :], t)[0, 2])


def perigee_passes(line1: str, line2: str, t_start: datetime, t_end: datetime,
                   coarse_s: float = 60.0) -> list[dict]:
    """列出 [t_start, t_end] 內每次近地點掠過（大地高局部極小），時刻以黃金分割精修至 ~1 s。"""
    sat = Satrec.twoline2rv(line1, line2)
    n = int((t_end - t_start).total_seconds() // coarse_s)
    ts = [t_start + timedelta(seconds=k * coarse_s) for k in range(n + 1)]
    alts = [_alt(sat, t) for t in ts]
    out = []
    for i in range(1, len(ts) - 1):
        a0, a1, a2 = alts[i - 1], alts[i], alts[i + 1]
        if a1 is None or a0 is None or a2 is None or not (a1 < a0 and a1 <= a2):
            continue
        lo, hi = ts[i - 1], ts[i + 1]
        for _ in range(40):                                  # 黃金分割
            d = (hi - lo).total_seconds()
            if d < 1.0:
                break
            m1 = lo + timedelta(seconds=0.382 * d); m2 = lo + timedelta(seconds=0.618 * d)
            f1, f2 = _alt(sat, m1), _alt(sat, m2)
            if f1 is None or f2 is None:
                break
            if f1 < f2:
                hi = m2
            else:
                lo = m1
        tp = lo + (hi - lo) / 2
        e, r, v = _state(sat, tp)
        if e:
            continue
        llh = eci_to_llh_batch(r[None, :], tp)[0]
        lst = (tp.hour + tp.minute / 60.0 + llh[1] / 15.0) % 24.0
        out.append({"t": tp.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "alt_km": round(float(llh[2]), 1), "lat": round(float(llh[0]), 2), "lon": round(float(llh[1]), 2),
                    "speed_kms": round(float(np.linalg.norm(v)), 2), "local_solar_time_h": round(lst, 1)})
    return out


def reentry_estimate(line1: str, line2: str, t_start: datetime | None = None, days: float = 20.0,
                     interface_km: float = 100.0) -> dict:
    """由 TLE 外推 days 天，回傳所有近地點掠過與首次低於 interface_km 之圈次（零階再入估算）。"""
    sat = Satrec.twoline2rv(line1, line2)
    ep = datetime(2000 + int(line1[18:20]) if int(line1[18:20]) < 57 else 1900 + int(line1[18:20]), 1, 1, tzinfo=timezone.utc) \
        + timedelta(days=float(line1[20:32]) - 1.0)
    t0 = t_start or ep
    passes = perigee_passes(line1, line2, t0, t0 + timedelta(days=days))
    hit = next((p for p in passes if p["alt_km"] <= interface_km), None)
    prev = None
    if hit:
        i = passes.index(hit)
        prev = passes[i - 1] if i > 0 else None
    a = float(line2[52:63]); period_h = (86400.0 / a) / 3600.0 if a > 0 else None
    return {
        "tle_epoch": ep.isoformat().replace("+00:00", "Z"), "tle_age_days_at_start": round((t0 - ep).total_seconds() / 86400.0, 1),
        "interface_km": interface_km, "period_h": round(period_h, 2) if period_h else None,
        "lon_per_hour_deg": 15.0, "lon_per_rev_deg": round((period_h % 24) * 15.0, 1) if period_h else None,
        "passes": passes, "reentry_pass": hit, "previous_pass": prev,
        "method": "SGP4/SDP4 外推之近地點掠過（大地高局部極小）；首次 ≤ interface_km 者為零階再入圈次；"
                  "次衛星點為該時刻之 TLE 傳播位置，時間誤差 1 h ≈ 經度 15°",
    }
