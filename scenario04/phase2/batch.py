"""批次會合分析：一顆主星對多顆次星，SGP4 星曆（GCRF）＋自適應近接搜尋＋展示模式 Pc。

輸入（dict）：
  primary: {norad_id | line1+line2, name?, hbr_m?, sigma_km?[3]}
  secondaries: [csvio.parse_secondaries 的 rows]
  start: ISO UTC；duration_s ≤ 604800；threshold_km；coarse_step_s?
  defaults: {hbr_m, sigma_km_primary[3], sigma_km_secondary[3]}
tle_lookup(norad_id) → (name, line1, line2) 或 None。
"""
from __future__ import annotations

import math
import time

import numpy as np

from . import constants as K
from .eop import Eop
from .ephemeris import RelativeState, Sgp4Ephemeris
from .events import build_event
from .frames import EarthRotationTable
from .rtn import rtn_matrix
from .tca import find_minima_adaptive
from .timescale import UtcTime

MAX_DURATION_S = 7 * 86400.0
SERIES_MAX_POINTS = 600
DEFAULTS = {"hbr_m": 5.0, "sigma_km_primary": [0.1, 0.5, 0.1], "sigma_km_secondary": [0.2, 1.0, 0.2]}


class _Memo:
    """主星在多個配對間重複使用：快取 rv(τ)。"""

    def __init__(self, eph):
        self.eph, self.cache = eph, {}
        self.span = eph.span

    def rv(self, tau):
        k = round(tau, 6)
        if k not in self.cache:
            self.cache[k] = self.eph.rv(tau)
        return self.cache[k]

    def rva(self, tau):
        return self.eph.rva(tau)


def _resolve(spec: dict, tle_lookup):
    if spec.get("line1") and spec.get("line2"):
        return spec.get("name") or f"NORAD {spec.get('norad_id', '?')}", spec["line1"], spec["line2"]
    if spec.get("norad_id") is not None:
        hit = tle_lookup(int(spec["norad_id"]))
        if hit:
            return spec.get("name") or hit[0], hit[1], hit[2]
    return None


def _demo_cov(eph, sigma):
    s2 = np.square(np.asarray(sigma, float))

    def f(tau):
        r, v = eph.rv(tau)
        C = rtn_matrix(r, v)
        return C.T @ np.diag(s2) @ C
    return f


def run_batch(req: dict, eop: Eop, tle_lookup) -> dict:
    t_start = time.perf_counter()
    start = UtcTime.parse(req["start"])
    dur = float(req["duration_s"])
    if not (0 < dur <= MAX_DURATION_S):
        return {"status": "error", "error": f"duration_s 須在 (0, {MAX_DURATION_S:.0f}] 秒"}
    thr = float(req["threshold_km"])
    coarse = float(req.get("coarse_step_s") or K.SEARCH_COARSE_STEP_S)
    d = {**DEFAULTS, **(req.get("defaults") or {})}
    ert = EarthRotationTable(start, -1.0, dur + 1.0, eop)
    span = (0.0, dur)

    prim = req["primary"]
    pr = _resolve(prim, tle_lookup)
    if pr is None:
        return {"status": "error", "error": f"找不到主星 TLE：{prim}"}
    p_eph = _Memo(Sgp4Ephemeris(pr[1], pr[2], start, ert, span))
    p_sigma = prim.get("sigma_km") or d["sigma_km_primary"]
    p_hbr = float(prim.get("hbr_m") or d["hbr_m"])
    p_norad = prim.get("norad_id") or int(pr[1][2:7])
    n_series = int(min(SERIES_MAX_POINTS, max(2, dur / coarse + 1)))
    s_step = dur / (n_series - 1)

    pairs, flat = [], []
    for row in req["secondaries"]:
        sr = _resolve(row, tle_lookup)
        base = {"secondary_norad": row.get("norad_id"), "input_line": row.get("line")}
        if sr is None:
            pairs.append({**base, "status": "error", "error": "找不到 TLE"})
            continue
        base["secondary_name"] = sr[0]
        if row.get("norad_id") == p_norad and not row.get("line1"):
            pairs.append({**base, "status": "error", "error": "與主星相同"})
            continue
        try:
            s_eph = Sgp4Ephemeris(sr[1], sr[2], start, ert, span)
            rel = RelativeState(p_eph, s_eph)
            res = find_minima_adaptive(rel.rv, rel.rva, 0.0, dur, coarse, thr)
            s_sigma = [row.get("sigma_r_km"), row.get("sigma_t_km"), row.get("sigma_n_km")]
            s_sigma = s_sigma if all(s_sigma) else d["sigma_km_secondary"]
            hbr_km = (p_hbr + float(row.get("hbr_m") or d["hbr_m"])) / 1000.0
            cp, cs = _demo_cov(p_eph, p_sigma), _demo_cov(s_eph, s_sigma)
            c3 = lambda tau, cp=cp, cs=cs: cp(tau) + cs(tau)  # noqa: E731
            others = [m.tau for m in res["all_minima"]]
            evs = []
            for ap in res["events"]:
                e = build_event(start, ap, rel, p_eph, c3, "demo", hbr_km, others, span, ["assumed", "assumed"])
                e.update({"primary_norad": p_norad, "secondary_norad": base["secondary_norad"] or int(sr[1][2:7]),
                          "secondary_name": sr[0]})
                evs.append(e)
            n_search_evals = rel.n_evals
            series = [float(np.linalg.norm(rel.rv(i * s_step)[0])) for i in range(n_series)]
            g = res["global_min"]
            pairs.append({**base, "status": "ok", "n_events": len(evs), "events": evs,
                          "n_minima": len(res["all_minima"]), "n_evals": n_search_evals,
                          "global_min": {"tca_utc": start.plus(g.tau).iso(), "miss_km": g.miss_km} if g else None,
                          "series": {"step_s": s_step, "d_km": series}})
            flat += evs
        except ValueError as ex:
            pairs.append({**base, "status": "error", "error": str(ex)})
    flat.sort(key=lambda e: (-((e.get("pc") or {}).get("foster") or 0.0), e["miss_km"]))
    return {
        "status": "ok", "start": start.iso(), "duration_s": dur, "threshold_km": thr,
        "primary": {"norad_id": p_norad, "name": pr[0], "hbr_m": p_hbr, "sigma_km": p_sigma},
        "defaults": d, "pairs": pairs, "events": flat,
        "search": {"method": "adaptive", "coarse_step_s": coarse, "min_step_s": K.SEARCH_MIN_STEP_S,
                   "accel_bound_km_s2": K.SEARCH_ACCEL_BOUND_KM_S2},
        "provenance": {"propagator": "SGP4 (WGS-72, opsmode i) → GCRF", "eop": eop.version(),
                       "covariance": "展示模式：RTN 三軸 σ、忽略相關項與時間演化",
                       "constants_version": K.CONSTANTS_VERSION},
        "elapsed_s": round(time.perf_counter() - t_start, 3),
    }
