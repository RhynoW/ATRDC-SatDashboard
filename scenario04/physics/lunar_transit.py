"""台北衛星凌月預報（Moon transit）。

流程：
  1. 以 astronomy-engine 逐步（預設 60 s）計算月球站心方位／仰角、視半徑與月相。
  2. 只在月球位於地平線以上的時段，以 SGP4 傳播候選衛星，得到衛星站心單位向量。
  3. 相鄰兩步之間，衛星的天空軌跡近似為大圓弧；計算月球到該弧的最小角距（粗篩，門檻 1.5°）。
  4. 對通過粗篩者以 0.5 s → 0.02 s 兩段細掃，得到最小角距 / 時刻 / 相對角速度。
  5. 最小角距 ≤ 月球視半徑為「凌月」，≤ 視半徑 + margin 為「擦邊」（涵蓋 TLE 預測誤差）。

TLE 沿軌誤差（低軌約 1–3 km，換成角度約 0.2–0.5°）與月面半徑（~0.26°）同量級，
故結果屬「事件預報」等級：實際凌月帶寬僅數公里，觀測前應以最新 TLE 重算。
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import astronomy as A
import numpy as np
from sgp4.api import Satrec, jday

from ..config import settings
from ..ingestion.index import get_index_for_time
from .coverage import OVERPASS_CATS, TAIPEI_OBS, get_overpass_candidates
from .propagate import HAS_SATREC_ARRAY

if HAS_SATREC_ARRAY:
    from sgp4.api import SatrecArray as _SatrecArray

logger = logging.getLogger(__name__)

MOON_RADIUS_KM = 1737.4
AU_KM = 149597870.7
_CHUNK = 400
_COARSE_DEG = 1.5
_SKIP_TAGS = (" DEB", " R/B")

# 空間站（凌月觀測最常見目標）：目錄中同一座站常以多個模組 NORAD 出現，只取第一個存在者
STATIONS: dict[str, Any] = {
    "label": "空間站 ISS / CSS", "sublabel": "ISS · 中國空間站", "color": "#FFFFFF",
    "groups": {"ISS": [25544, 25575, 26400, 26700, 49044], "CSS": [48274, 53239, 54216]},
}
STATION_CAT = "STATION"

_OBSV = A.Observer(settings.TAIPEI_LAT, settings.TAIPEI_LON, settings.TAIPEI_H_KM * 1000.0)


def _unit_from_azel(az_deg: np.ndarray, el_deg: np.ndarray) -> np.ndarray:
    az, el = np.deg2rad(az_deg), np.deg2rad(el_deg)
    return np.stack([np.cos(el) * np.sin(az), np.cos(el) * np.cos(az), np.sin(el)], axis=-1)


def _gmst(jd: np.ndarray, fr: np.ndarray) -> np.ndarray:
    t = ((jd - 2451545.0) + fr) / 36525.0
    return np.deg2rad((280.46061837 + 360.98564736629 * (jd - 2451545.0 + fr) + 0.000387933 * t ** 2) % 360.0)


def _sat_enu_unit(r_teme: np.ndarray, gm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """TEME(km) → 站心 ENU 單位向量與距離（km）。r_teme (..., T, 3)、gm (T,)。"""
    obs = TAIPEI_OBS
    cg, sg = np.cos(gm), np.sin(gm)
    xe = cg * r_teme[..., 0] + sg * r_teme[..., 1]
    ye = -sg * r_teme[..., 0] + cg * r_teme[..., 1]
    dx, dy, dz = xe - obs["x0"], ye - obs["y0"], r_teme[..., 2] - obs["z0"]
    up = obs["cl"] * obs["co"] * dx + obs["cl"] * obs["so"] * dy + obs["sl"] * dz
    east = -obs["so"] * dx + obs["co"] * dy
    north = -obs["sl"] * obs["co"] * dx - obs["sl"] * obs["so"] * dy + obs["cl"] * dz
    rng = np.sqrt(east ** 2 + north ** 2 + up ** 2)
    safe = np.where(rng > 1e-3, rng, 1e-3)
    return np.stack([east / safe, north / safe, up / safe], axis=-1), rng


def min_arc_distance_deg(u: np.ndarray, m: np.ndarray, min_el_deg: float = 4.0) -> tuple[np.ndarray, np.ndarray]:
    """u: (n,T,3) 衛星單位向量；m: (T,3) 月球單位向量。
    回傳每顆衛星「月球到衛星各相鄰步之大圓弧」的最小角距（度）與其所在步索引。
    僅計算兩端點皆高於 min_el_deg 的弧（多數衛星大部分時間在地平線下，可省去約 90% 運算）。"""
    n_sat, n_t = u.shape[0], u.shape[1]
    zmin = math.sin(math.radians(min_el_deg))
    ok = (u[:, :-1, 2] >= zmin) & (u[:, 1:, 2] >= zmin)
    si, ki = np.nonzero(ok)
    best = np.full(n_sat, np.inf)
    kbest = np.zeros(n_sat, dtype=int)
    if len(si) == 0:
        return best, kbest
    a, b = u[si, ki], u[si, ki + 1]
    p = 0.5 * (m[ki] + m[ki + 1])
    p = p / np.linalg.norm(p, axis=1, keepdims=True)
    n = np.cross(a, b)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = n / np.where(ln > 1e-12, ln, 1.0)
    pn = np.einsum("ik,ik->i", n, p)
    c = p - pn[:, None] * n
    cl = np.linalg.norm(c, axis=1, keepdims=True)
    c = c / np.where(cl > 1e-12, cl, 1.0)
    inside = (np.einsum("ik,ik->i", np.cross(a, c), n) >= 0) & (np.einsum("ik,ik->i", np.cross(c, b), n) >= 0)
    d_in = np.arcsin(np.clip(np.abs(pn), 0, 1))
    d_a = np.arccos(np.clip(np.einsum("ik,ik->i", a, p), -1, 1))
    d_b = np.arccos(np.clip(np.einsum("ik,ik->i", b, p), -1, 1))
    d = np.rad2deg(np.where(inside, d_in, np.minimum(d_a, d_b)))
    order = np.lexsort((d, si))                       # 依衛星分組、組內距離由小到大
    si_o = si[order]
    first = np.concatenate(([True], si_o[1:] != si_o[:-1]))
    best[si_o[first]] = d[order][first]
    kbest[si_o[first]] = ki[order][first]
    return best, kbest


class _Moon:
    """月球站心方位／仰角格點（供內插）。"""

    def __init__(self, t0: datetime, offs_s: np.ndarray):
        self.t0, self.offs = t0, offs_s
        jd0, fr0 = jday(t0.year, t0.month, t0.day, t0.hour, t0.minute, t0.second)
        self.jd0, self.fr0 = jd0, fr0
        az = np.empty(len(offs_s)); el = np.empty(len(offs_s)); dist = np.empty(len(offs_s))
        for i, s in enumerate(offs_s):
            t = A.Time(jd0 + fr0 - 2451545.0 + s / 86400.0)
            eq = A.Equator(A.Body.Moon, t, _OBSV, True, True)
            h = A.Horizon(t, _OBSV, eq.ra, eq.dec, A.Refraction.Airless)
            az[i], el[i], dist[i] = h.azimuth, h.altitude, eq.dist * AU_KM
        self.az, self.el, self.dist = az, el, dist
        self.u = _unit_from_azel(az, el)

    def unit_at(self, s: np.ndarray) -> np.ndarray:
        v = np.stack([np.interp(s, self.offs, self.u[:, c]) for c in range(3)], axis=-1)
        return v / np.linalg.norm(v, axis=-1, keepdims=True)

    def radius_deg(self, s: float) -> float:
        d = float(np.interp(s, self.offs, self.dist))
        return math.degrees(math.asin(MOON_RADIUS_KM / d))


def _sat_unit_at(sat: Satrec, jd0: float, fr0: float, s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    jd = np.full(len(s), jd0)
    fr = fr0 + s / 86400.0
    e, r, _ = sat.sgp4_array(jd, fr)
    u, rng = _sat_enu_unit(r, _gmst(jd, fr))
    u[e != 0] = np.nan
    return u, rng


def _sep_deg(u: np.ndarray, m: np.ndarray) -> np.ndarray:
    return np.rad2deg(np.arccos(np.clip(np.einsum("tk,tk->t", u, m), -1, 1)))


def _tangent_basis(m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """以月球方向 m 為視軸的畫面基底：right＝方位角增加方向，up＝天頂方向（垂直於視軸的分量）。"""
    right = np.array([m[1], -m[0], 0.0])
    right /= np.linalg.norm(right)
    up = np.array([0.0, 0.0, 1.0]) - m[2] * m
    up /= np.linalg.norm(up)
    return right, up


def _refine(sat: Satrec, moon: _Moon, s_center: float) -> dict[str, Any] | None:
    """在粗篩最小角距附近細掃，回傳最小角距、時刻與畫面座標軌跡。"""
    s1 = s_center + np.arange(-75.0, 75.0 + 1e-9, 0.5)
    u, _ = _sat_unit_at(sat, moon.jd0, moon.fr0, s1)
    sep = _sep_deg(u, moon.unit_at(s1))
    if not np.isfinite(sep).any():
        return None
    s_best = s1[int(np.nanargmin(sep))]
    s2 = s_best + np.arange(-1.0, 1.0 + 1e-9, 0.02)
    u2, rng2 = _sat_unit_at(sat, moon.jd0, moon.fr0, s2)
    sep2 = _sep_deg(u2, moon.unit_at(s2))
    k = int(np.nanargmin(sep2))
    s_min, sep_min = float(s2[k]), float(sep2[k])
    m = moon.unit_at(np.array([s_min]))[0]
    right, up = _tangent_basis(m)

    sd = np.array([s_min - 0.1, s_min + 0.1])
    ua, _ = _sat_unit_at(sat, moon.jd0, moon.fr0, sd)
    ma = moon.unit_at(sd)
    # 相對角速度（度/秒）：衛星在月球畫面座標中的位移率（月球本身於 0.2 s 內位移可忽略，但仍以各自時刻之視軸計）
    rel = []
    for i in range(2):
        r_i, u_i = _tangent_basis(ma[i])
        d = float(ua[i] @ ma[i])
        rel.append((math.degrees(math.atan2(ua[i] @ r_i, d)), math.degrees(math.atan2(ua[i] @ u_i, d))))
    omega = math.hypot(rel[1][0] - rel[0][0], rel[1][1] - rel[0][1]) / 0.2
    omega = max(omega, 1e-4)
    R = moon.radius_deg(s_min)
    half = float(np.clip((R + 0.65) / omega, 1.0, 90.0))
    st = s_min + np.linspace(-half, half, 61)
    ut, _ = _sat_unit_at(sat, moon.jd0, moon.fr0, st)
    mt = moon.unit_at(st)
    track = []
    for i in range(len(st)):
        if not np.isfinite(ut[i]).all():
            continue
        r_i, u_i = _tangent_basis(mt[i])
        d = float(ut[i] @ mt[i])
        track.append([round(float(st[i] - s_min), 2),
                      round(math.degrees(math.atan2(ut[i] @ r_i, d)), 4),
                      round(math.degrees(math.atan2(ut[i] @ u_i, d)), 4)])
    az = math.degrees(math.atan2(u2[k][0], u2[k][1])) % 360.0
    el = math.degrees(math.asin(np.clip(u2[k][2], -1, 1)))
    return {"s": s_min, "sep": sep_min, "omega": omega, "R": R, "track": track,
            "sat_az": az, "sat_el": el, "sat_range_km": float(rng2[k]), "m": m}


def _sun_dir_deg(moon: _Moon, when: datetime, s: float) -> tuple[float, float, float]:
    """(太陽在月球畫面座標中的方位角〔自 right 逆時針，度〕, 照亮比例, 月相角〔黃經差 0–360，<180 為盈〕)。"""
    t = A.Time(moon.jd0 + moon.fr0 - 2451545.0 + s / 86400.0)
    eq = A.Equator(A.Body.Sun, t, _OBSV, True, True)
    h = A.Horizon(t, _OBSV, eq.ra, eq.dec, A.Refraction.Airless)
    sv = _unit_from_azel(np.array(h.azimuth), np.array(h.altitude))
    m = moon.unit_at(np.array([s]))[0]
    right, up = _tangent_basis(m)
    ang = math.degrees(math.atan2(float(sv @ up), float(sv @ right)))
    frac = A.Illumination(A.Body.Moon, t).phase_fraction
    return ang, float(frac), float(A.MoonPhase(t))


def _moon_up_runs(el: np.ndarray, min_el: float) -> list[tuple[int, int]]:
    up = el >= min_el
    if not up.any():
        return []
    d = np.diff(np.concatenate(([0], up.astype(np.int8), [0])))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0] - 1))


def find_lunar_transits(
    ts: datetime, hours: float = 24.0, step_s: float = 60.0, near_margin_deg: float = 0.30,
    cats: list[str] | None = None, max_events: int = 300, min_el_deg: float = 5.0,
) -> dict[str, Any]:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.replace(second=0, microsecond=0)
    n_steps = int(hours * 3600 / step_s) + 1
    offs = np.arange(n_steps) * step_s
    moon = _Moon(ts, offs)
    runs = _moon_up_runs(moon.el, min_el_deg)

    idx = get_index_for_time(ts)
    cand = get_overpass_candidates(idx)
    groups: dict[str, list[int]] = {c: list(v) for c, v in cand.items()}
    st_ids = []
    for ids in STATIONS["groups"].values():
        pick = next((n for n in ids if n in idx), None)
        if pick:
            st_ids.append(pick)
    groups[STATION_CAT] = st_ids
    want = set(cats) if cats else set(groups)
    catinfo = {c: {"label": cfg["label"], "sublabel": cfg["sublabel"], "color": cfg["color"]}
               for c, cfg in OVERPASS_CATS.items()}
    catinfo[STATION_CAT] = {k: STATIONS[k] for k in ("label", "sublabel", "color")}

    events: list[dict[str, Any]] = []
    scanned: set[int] = set()
    for a0, b0 in runs:
        if b0 - a0 < 2 or not HAS_SATREC_ARRAY:
            continue
        s_run = offs[a0:b0 + 1]
        jd = np.full(len(s_run), moon.jd0)
        fr = moon.fr0 + s_run / 86400.0
        gm = _gmst(jd, fr)
        m_run = moon.u[a0:b0 + 1]
        for cat, nids in groups.items():
            if cat not in want or not nids:
                continue
            for c0 in range(0, len(nids), _CHUNK):
                chunk = [n for n in nids[c0:c0 + _CHUNK]
                         if not any(tg in idx[n]["name"].upper() for tg in _SKIP_TAGS)]
                if not chunk:
                    continue
                try:
                    sats = [Satrec.twoline2rv(idx[n]["line1"], idx[n]["line2"]) for n in chunk]
                    e_raw, r_raw, _ = _SatrecArray(sats).sgp4(jd, fr)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("凌月傳播失敗 cat=%s: %s", cat, exc)
                    continue
                scanned.update(chunk)
                u, _rng = _sat_enu_unit(r_raw, gm)
                u = np.where((e_raw != 0)[..., None], np.array([0.0, 0.0, -1.0]), u)   # 傳播失敗 → 視為在地平線下
                dmin, kmin = min_arc_distance_deg(u, m_run, min_el_deg - 1.0)
                for i in np.where(dmin < _COARSE_DEG)[0]:
                    nid = chunk[i]
                    s_c = float(s_run[kmin[i]] + step_s / 2)
                    ref = _refine(sats[i], moon, s_c)
                    if not ref or ref["sep"] > ref["R"] + near_margin_deg:
                        continue
                    if ref["sat_el"] < min_el_deg:
                        continue
                    s_min = ref["s"]
                    when = ts + timedelta(seconds=s_min)
                    sun_ang, frac, phase = _sun_dir_deg(moon, when, s_min)
                    kind = "transit" if ref["sep"] <= ref["R"] else "near"
                    chord = 2 * math.sqrt(max(ref["R"] ** 2 - ref["sep"] ** 2, 0.0)) / ref["omega"] if kind == "transit" else 0.0
                    mi = int(np.argmin(np.abs(offs - s_min)))
                    events.append({
                        "norad_id": nid, "name": idx[nid]["name"], "cat": cat, "color": catinfo[cat]["color"],
                        "t_utc": when.isoformat(timespec="milliseconds"),
                        "kind": kind, "sep_deg": round(ref["sep"], 4), "moon_r_deg": round(ref["R"], 4),
                        "chord_s": round(chord, 3), "omega_dps": round(ref["omega"], 3),
                        "sat_az": round(ref["sat_az"], 2), "sat_el": round(ref["sat_el"], 2),
                        "sat_range_km": round(ref["sat_range_km"], 1),
                        "moon_az": round(float(moon.az[mi]), 2), "moon_el": round(float(moon.el[mi]), 2),
                        "moon_frac": round(frac, 4), "moon_phase_deg": round(phase, 1), "sun_angle_deg": round(sun_ang, 2),
                        "track": ref["track"],
                    })
    events.sort(key=lambda e: e["t_utc"])
    return {
        "timestamp": ts.isoformat(), "hours": hours, "step_sec": step_s,
        "near_margin_deg": near_margin_deg, "min_el_deg": min_el_deg,
        "moon_up_runs": [[(ts + timedelta(seconds=float(offs[a]))).isoformat(),
                          (ts + timedelta(seconds=float(offs[b]))).isoformat()] for a, b in runs],
        "n_scanned": len(scanned),
        "categories": catinfo,
        "events": events[:max_events], "n_events": len(events),
        "note": "TLE 沿軌誤差（低軌約 0.2–0.5°）與月面視半徑（~0.26°）同量級，屬事件預報；"
                "實際凌月帶寬僅數公里，觀測前請以最新 TLE 重算。",
    }
