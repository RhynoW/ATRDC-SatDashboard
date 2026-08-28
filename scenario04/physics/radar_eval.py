"""台灣假想地基雷達站之追蹤效益評估（簡化模型）與過頂追蹤序列。

評估邏輯（明確為簡化代理，非作業級 OD 誤差分析）：
  - 對樣本衛星以 SGP4 傳播 24 h（步長 60 s），計算各站仰角 > mask 的「追蹤弧段」。
  - before = 全球已知 SSN 站（config/ssn_stations.geojson）；after = 加入台灣站。
  - 指標：每日弧段數、最大／平均無觀測間隙、累計追蹤分鐘；
    精度代理：σ_after/σ_before ≈ √(N_before/N_after)（觀測數均勻假設）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4.api import Satrec, jday

from ..config.stations import SSN_STATIONS
from .coords import eci_to_elaz, gmst_rad, observer_ecef

TAIWAN_STATION = {"name": "台灣假想地基雷達站（樂山）", "lat": 24.395, "lon": 120.905, "h_km": 2.6}


def _stations(include_taiwan: bool) -> list[dict]:
    out = []
    for f in SSN_STATIONS.get("features", []):
        lon, lat = f["geometry"]["coordinates"][:2]
        out.append({"name": f["properties"].get("name", "?"), "lat": lat, "lon": lon,
                    "obs": observer_ecef(lat, lon, 0.0)})
    if include_taiwan:
        t = TAIWAN_STATION
        out.append({**t, "obs": observer_ecef(t["lat"], t["lon"], t["h_km"])})
    return out


def _propagate(line1: str, line2: str, t0: datetime, n_steps: int, step_s: float):
    sat = Satrec.twoline2rv(line1, line2)
    ts, rs = [], []
    for k in range(n_steps):
        t = t0 + timedelta(seconds=k * step_s)
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second)
        e, r, _ = sat.sgp4(jd, fr)
        if e == 0:
            ts.append((t, jd, fr)); rs.append(r)
    return ts, np.array(rs)


def _to_ecef(r_eci: np.ndarray, ts: list) -> np.ndarray:
    """ECI (N,3) → ECEF (N,3)，各步以自身 GMST 旋轉（向量化）。"""
    g = np.array([gmst_rad(jd, fr) for (_, jd, fr) in ts])
    cg, sg = np.cos(g), np.sin(g)
    return np.column_stack([cg * r_eci[:, 0] + sg * r_eci[:, 1],
                            -sg * r_eci[:, 0] + cg * r_eci[:, 1], r_eci[:, 2]])


def _elev(ecef: np.ndarray, obs: dict) -> np.ndarray:
    dx = ecef[:, 0] - obs["x0"]; dy = ecef[:, 1] - obs["y0"]; dz = ecef[:, 2] - obs["z0"]
    sl, cl, so, co = obs["sl"], obs["cl"], obs["so"], obs["co"]
    E = -so * dx + co * dy
    N = -sl * co * dx - sl * so * dy + cl * dz
    U = cl * co * dx + cl * so * dy + sl * dz
    rng = np.sqrt(E * E + N * N + U * U)
    return np.rad2deg(np.arcsin(np.clip(U / np.where(rng > 1e-3, rng, 1e-3), -1, 1)))


def _arcs(visible: np.ndarray, step_s: float):
    """布林序列 → (弧段數, 最大間隙分, 平均間隙分, 累計可見分)。"""
    n = len(visible)
    if n == 0:
        return 0, 0.0, 0.0, 0.0
    d = np.diff(visible.astype(int))
    starts = list(np.where(d == 1)[0] + 1) + ([0] if visible[0] else [])
    n_arcs = len(starts)
    gaps = []
    run = 0
    for v in visible:
        if v:
            if run: gaps.append(run)
            run = 0
        else:
            run += 1
    if run: gaps.append(run)
    tomin = step_s / 60.0
    return (n_arcs, max(gaps) * tomin if gaps else 0.0,
            float(np.mean(gaps)) * tomin if gaps else 0.0, float(visible.sum()) * tomin)


def evaluate(sats: list[tuple[int, str, str, str]], hours: float = 24.0,
             step_s: float = 60.0, mask_deg: float = 5.0) -> dict:
    """sats: [(norad, name, line1, line2)]。回傳彙總與逐星指標。"""
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    n_steps = int(hours * 3600 / step_s)
    st_before = _stations(False)
    tw = _stations(True)[-1]
    rows = []
    for nid, name, l1, l2 in sats:
        ts, r = _propagate(l1, l2, t0, n_steps, step_s)
        if len(r) < 10:
            continue
        ecef = _to_ecef(r, ts)
        vis_b = np.zeros(len(r), dtype=bool)
        for s in st_before:
            vis_b |= _elev(ecef, s["obs"]) > mask_deg
        vis_tw = _elev(ecef, tw["obs"]) > mask_deg
        vis_a = vis_b | vis_tw
        nb, gb_max, gb_mean, mb = _arcs(vis_b, step_s)
        na, ga_max, ga_mean, ma = _arcs(vis_a, step_s)
        n_tw, _, _, m_tw = _arcs(vis_tw, step_s)
        only_tw = int(np.sum(vis_tw & ~vis_b) * step_s / 60.0)   # 僅台灣可見之分鐘
        sigma_ratio = float(np.sqrt(mb / ma)) if ma > 0 and mb > 0 else (0.0 if ma > 0 else 1.0)
        rows.append({
            "norad": nid, "name": name,
            "arcs_before": nb, "arcs_after": na, "arcs_taiwan": n_tw,
            "gap_max_before_min": round(gb_max, 1), "gap_max_after_min": round(ga_max, 1),
            "gap_mean_before_min": round(gb_mean, 1), "gap_mean_after_min": round(ga_mean, 1),
            "track_min_before": round(mb, 1), "track_min_after": round(ma, 1),
            "track_min_taiwan_only": only_tw,
            "sigma_ratio": round(sigma_ratio, 3),
            "precision_gain_pct": round((1 - sigma_ratio) * 100, 1),
        })
    if not rows:
        return {"error": "無可評估衛星"}
    def avg(k): return round(float(np.mean([x[k] for x in rows])), 1)
    return {
        "t0": t0.isoformat().replace("+00:00", "Z"), "hours": hours, "mask_deg": mask_deg,
        "n_stations_before": len(st_before), "taiwan_station": {k: TAIWAN_STATION[k] for k in ("name", "lat", "lon")},
        "summary": {
            "n_sats": len(rows),
            "arcs_before": avg("arcs_before"), "arcs_after": avg("arcs_after"),
            "gap_max_before_min": avg("gap_max_before_min"), "gap_max_after_min": avg("gap_max_after_min"),
            "track_min_before": avg("track_min_before"), "track_min_after": avg("track_min_after"),
            "taiwan_only_min": avg("track_min_taiwan_only"),
            "precision_gain_pct": avg("precision_gain_pct"),
            "sats_with_taiwan_arc": int(sum(1 for x in rows if x["arcs_taiwan"] > 0)),
        },
        "sats": sorted(rows, key=lambda x: -x["precision_gain_pct"]),
        "model_note": "精度代理 σ∝1/√(累計觀測量)，為均勻觀測假設之簡化模型；弧段以仰角 > mask 定義。",
    }


def track_passes(line1: str, line2: str, hours: float = 24.0, step_s: float = 30.0,
                 mask_deg: float = 5.0, max_passes: int = 3) -> dict:
    """台灣站對單一衛星之未來過頂 az/el 序列（即時追蹤示範用）。"""
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ts, r = _propagate(line1, line2, t0, int(hours * 3600 / step_s), step_s)
    tw = _stations(True)[-1]
    passes, cur = [], None
    for k in range(len(r)):
        el, az, rng = eci_to_elaz(r[k:k + 1], ts[k][1], ts[k][2], tw["obs"])
        if el[0] > mask_deg:
            pt = {"t": ts[k][0].isoformat().replace("+00:00", "Z"),
                  "el": round(float(el[0]), 2), "az": round(float(az[0]), 2),
                  "rng": round(float(rng[0]), 1)}
            if cur is None:
                cur = {"points": [pt]}
            else:
                cur["points"].append(pt)
        elif cur is not None:
            passes.append(cur); cur = None
            if len(passes) >= max_passes:
                break
    if cur is not None and len(passes) < max_passes:
        passes.append(cur)
    for p in passes:
        p["aos"] = p["points"][0]["t"]; p["los"] = p["points"][-1]["t"]
        p["max_el"] = max(x["el"] for x in p["points"])
        p["duration_min"] = round(len(p["points"]) * step_s / 60.0, 1)
    return {"station": {k: TAIWAN_STATION[k] for k in ("name", "lat", "lon")},
            "t0": t0.isoformat().replace("+00:00", "Z"), "mask_deg": mask_deg, "passes": passes}
