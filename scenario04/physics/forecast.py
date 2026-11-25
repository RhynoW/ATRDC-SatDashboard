"""過頂預報（客戶回饋第 6、7、8 項；與 Rust 單機版 src/forecast.rs 同一套判定）。

- 過頂時段：中心點仰角 ≥ 門檻；中心點設半徑 > 0 時，改用「星下點與中心的地心角 ≤ 仰角門檻對應地心角＋半徑」。
- 可偵照時段：中心點相對衛星的離天底角 ≤ 偵照角度；半徑 > 0 時用「偵照地面半徑＋區域半徑」。
- 日照：TCA 當下中心點太陽仰角（astronomy-engine）；日出日落用 astronomy.SearchRiseSet（含大氣折射與日面半徑）。
- 歷史誤差：資料庫中同一顆衛星兩筆 TLE 互相比對（舊 TLE 傳播到新 TLE 曆元與新 TLE 位置相減），
  依提前天數分組統計位置誤差與沿軌時間誤差，再依每筆過頂距 TLE 曆元的天數內插預估時間誤差（p90）。

地球以球體 6371 km 估算地心角與偵照地面半徑；仰角與離天底角用 WGS84 橢球站心座標精確計算。
粗掃以 sgp4_array 向量化，邊界與最高點再以純量 SGP4 二分／黃金分割細化到約 0.5 秒。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import numpy as np
from sgp4.api import Satrec, jday

RE = 6371.0
A_WGS84 = 6378.137
E2 = (1 / 298.257223563) * (2 - 1 / 298.257223563)
LEAD_BUCKETS = [1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 14.0, 21.0, 30.0]
SENSOR_LABEL = {"optical": "光學", "radar": "雷達", "sigint": "電子偵察", "other": "其他"}


# ── 幾何 ─────────────────────────────────────────────────────────────────────

def lam_eps(h: float, eps_deg: float) -> float:
    """仰角門檻 ε 對應之最大地心角（rad）"""
    e = math.radians(eps_deg)
    return math.acos(max(-1.0, min(1.0, RE * math.cos(e) / (RE + h)))) - e


def lam_eta(h: float, eta_deg: float) -> float:
    """離天底角 η 對應之地心角（rad）；超過地平取地平"""
    s = (RE + h) / RE * math.sin(math.radians(eta_deg))
    if s >= 1.0:
        return lam_eps(h, 0.0)
    return math.asin(s) - math.radians(eta_deg)


def gmst_arr(jd: np.ndarray) -> np.ndarray:
    """IAU 2006 GMST（rad），與 Rust physics::gmst_rad 相同"""
    d = jd - 2451545.0
    tc = d / 36525.0
    deg = (280.46061837 + 360.98564736629 * d + 0.000387933 * tc * tc - tc ** 3 / 38710000.0) % 360.0
    return np.deg2rad(deg)


@dataclass
class Target:
    name: str
    lat: float
    lon: float
    alt_m: float = 0.0
    radius_km: float = 0.0
    note: str = ""
    obs: tuple = field(init=False)
    unit: np.ndarray = field(init=False)
    r_rad: float = field(init=False)

    def __post_init__(self):
        la, lo = math.radians(self.lat), math.radians(self.lon)
        sl, cl, so, co = math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)
        n = A_WGS84 / math.sqrt(1 - E2 * sl * sl)
        h = self.alt_m / 1000.0
        self.obs = ((n + h) * cl * co, (n + h) * cl * so, (n * (1 - E2) + h) * sl, sl, cl, so, co)
        self.unit = np.array([cl * co, cl * so, sl])
        self.r_rad = self.radius_km / RE


class Sat:
    """單顆衛星傳播（向量化）"""

    def __init__(self, line1: str, line2: str, t0: datetime):
        self.sat = Satrec.twoline2rv(line1, line2)
        self.jd0, self.fr0 = jday(t0.year, t0.month, t0.day, t0.hour, t0.minute, t0.second + t0.microsecond / 1e6)

    def states(self, secs: np.ndarray) -> dict[str, np.ndarray]:
        """secs：相對 t0 秒數陣列 → TEME 位置、GMST、星下點、單位向量；SGP4 錯誤以 ok=False 標示"""
        fr = self.fr0 + secs / 86400.0
        jd = np.full_like(fr, self.jd0)
        e, r, _ = self.sat.sgp4_array(jd, fr)
        r = np.asarray(r)
        ok = (e == 0) & np.all(np.isfinite(r), axis=1) & (np.einsum("ij,ij->i", r, r) >= 6378.135 ** 2)
        g = gmst_arr(jd + fr)
        cg, sg = np.cos(g), np.sin(g)
        xe = cg * r[:, 0] + sg * r[:, 1]
        ye = -sg * r[:, 0] + cg * r[:, 1]
        ze = r[:, 2]
        lon = np.arctan2(ye, xe)
        rr = np.hypot(xe, ye)
        lat = np.arctan2(ze, rr * (1 - E2))
        alt = np.zeros_like(lat)
        for _ in range(5):
            sl = np.sin(lat)
            n = A_WGS84 / np.sqrt(1 - E2 * sl * sl)
            cl = np.cos(lat)
            alt = np.where(np.abs(cl) > 1e-9, rr / np.where(np.abs(cl) > 1e-9, cl, 1.0) - n, np.abs(ze) / (1 - E2) - n)
            lat = np.arctan2(ze, rr * (1 - E2 * (n / (n + alt))))
        u = np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=1)
        return {"ok": ok, "xe": xe, "ye": ye, "ze": ze, "lat": np.rad2deg(lat), "lon": np.rad2deg(lon), "alt": alt, "u": u}


def geo(s: dict[str, np.ndarray], tg: Target) -> dict[str, np.ndarray]:
    x0, y0, z0, sl, cl, so, co = tg.obs
    dx, dy, dz = s["xe"] - x0, s["ye"] - y0, s["ze"] - z0
    e = -so * dx + co * dy
    n = -sl * co * dx - sl * so * dy + cl * dz
    up = cl * co * dx + cl * so * dy + sl * dz
    rng = np.sqrt(e * e + n * n + up * up)
    el = np.rad2deg(np.arcsin(np.clip(up / np.maximum(rng, 1e-3), -1, 1)))
    az = np.rad2deg(np.arctan2(e, n)) % 360.0
    n1 = np.sqrt(s["xe"] ** 2 + s["ye"] ** 2 + s["ze"] ** 2)
    # 離天底角：衛星→地心 與 衛星→中心點 的夾角
    c = (s["xe"] * dx + s["ye"] * dy + s["ze"] * dz) / (n1 * np.maximum(rng, 1e-9))
    off = np.rad2deg(np.arccos(np.clip(c, -1, 1)))
    lam = np.arccos(np.clip(s["u"] @ tg.unit, -1, 1))
    return {"el": el, "az": az, "rng": rng, "off": off, "lam": lam, "alt": s["alt"], "lat": s["lat"], "lon": s["lon"], "ok": s["ok"]}


def g_vis(g: dict[str, np.ndarray], tg: Target, mask: float) -> np.ndarray:
    if tg.r_rad <= 0:
        v = g["el"] - mask
    else:
        e = math.radians(mask)
        lam_e = np.arccos(np.clip(RE * math.cos(e) / (RE + g["alt"]), -1, 1)) - e
        v = np.rad2deg(lam_e + tg.r_rad - g["lam"])
    return np.where(g["ok"], v, -999.0)


def g_img(g: dict[str, np.ndarray], tg: Target, eta: float) -> np.ndarray:
    if tg.r_rad <= 0:
        v = np.where(g["el"] < 0, -90.0, eta - g["off"])
    else:
        lam_n = np.array([lam_eta(float(h), eta) for h in np.atleast_1d(g["alt"])])
        v = np.rad2deg(lam_n + tg.r_rad - g["lam"])
    return np.where(g["ok"], v, -999.0)


def _gmax(f: Callable[[float], float], a: float, b: float) -> tuple[float, float]:
    r = (math.sqrt(5) - 1) / 2
    c, d = b - r * (b - a), a + r * (b - a)
    fc, fd = f(c), f(d)
    while b - a > 0.5:
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - r * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + r * (b - a)
            fd = f(d)
    t = (a + b) / 2
    return t, f(t)


def _root(f: Callable[[float], float], lo: float, hi: float) -> float:
    """f(lo) < 0、f(hi) ≥ 0（lo 可大於 hi）"""
    for _ in range(40):
        if abs(hi - lo) <= 0.5:
            break
        m = (lo + hi) / 2
        if f(m) >= 0:
            hi = m
        else:
            lo = m
    return hi


def tle_epoch(line1: str) -> datetime | None:
    try:
        yy = int(line1[18:20])
        doy = float(line1[20:32])
    except (ValueError, IndexError):
        return None
    year = 2000 + yy if yy < 57 else 1900 + yy
    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)


# ── 單顆衛星過頂 ─────────────────────────────────────────────────────────────

def sat_passes(sj: dict[str, Any], targets: list[Target], t0: datetime, span: float, req: dict[str, Any],
               sun_el: Callable[[Target, datetime], float]) -> list[dict[str, Any]]:
    try:
        sat = Sat(sj["line1"], sj["line2"], t0)
    except Exception:
        return []
    epoch = tle_epoch(sj["line1"])
    try:
        mm = float(sj["line2"][52:63])
    except ValueError:
        mm = 15.0
    period = 1440.0 / max(mm, 0.01)
    step = 20.0 if period < 200 else 60.0 if period < 800 else 300.0
    margin = 4.0 if period < 200 else 1.5
    secs = np.arange(0.0, span + step, step)
    secs[-1] = min(secs[-1], span)
    st = sat.states(secs)
    mask, look = req["mask_deg"], sj["look"]

    def one(x: float) -> dict[str, np.ndarray]:
        return sat.states(np.array([x]))

    out = []
    for tg in targets:
        gv_all = g_vis(geo(st, tg), tg, mask)
        inside = gv_all > -margin
        if not inside.any():
            continue
        idx = np.flatnonzero(np.diff(np.concatenate([[0], inside.astype(int), [0]])))
        segs = [(max(secs[a] - step, 0.0), secs[b - 1] + step if b - 1 < len(secs) else span) for a, b in zip(idx[::2], idx[1::2])]

        def gv(x: float, tg=tg) -> float:
            return float(g_vis(geo(one(x), tg), tg, mask)[0])

        def gi(x: float, tg=tg) -> float:
            return float(g_img(geo(one(x), tg), tg, look)[0])

        for a, b in segs:
            b = min(b, span)
            pieces = [(a, b)]
            if b - a > 6 * 3600:  # 高軌：UTC 午夜切開
                pieces, s0 = [], a
                mid = datetime.combine((t0 + timedelta(seconds=a)).date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
                while (mid - t0).total_seconds() <= b:
                    m = (mid - t0).total_seconds()
                    if m > s0:
                        pieces.append((s0, m))
                    s0 = m
                    mid += timedelta(days=1)
                pieces.append((s0, b))
            for a, b in pieces:
                tc, vmax = _gmax(gv, a, b)
                if vmax < 0:
                    continue
                ws = a if gv(a) >= 0 else _root(gv, a, tc)
                we = b if gv(b) >= 0 else _root(gv, b, tc)
                tbest, vimg = _gmax(gi, ws, we)
                img_s = img_e = None
                if vimg >= 0:
                    img_s = ws if gi(ws) >= 0 else _root(gi, ws, tbest)
                    img_e = we if gi(we) >= 0 else _root(gi, we, tbest)
                coverable = img_s is not None
                if req["only_coverable"] and not coverable:
                    continue
                gt = {k: float(v[0]) for k, v in geo(one(tc), tg).items()}
                gb = {k: float(v[0]) for k, v in geo(one(tbest), tg).items()}
                tca = t0 + timedelta(seconds=tc)
                sun = sun_el(tg, tca)
                sunlit = sun >= req["sun_min_deg"]
                dl = req["daylight"]
                if (dl == "all" and not sunlit) or (dl == "optical" and sj["sensor"] == "optical" and not sunlit):
                    continue
                l1, l2 = float(one(tc - 10)["lat"][0]), float(one(tc + 10)["lat"][0])
                lead = (tca - epoch).total_seconds() / 86400.0 if epoch else float("nan")
                out.append({
                    "norad_id": sj["norad"], "name": sj["name"], "orig_name": sj.get("orig", ""), "group": sj.get("group", ""),
                    "sensor": SENSOR_LABEL.get(sj["sensor"], ""), "site": tg.name,
                    "start": t0 + timedelta(seconds=ws), "end": t0 + timedelta(seconds=we), "duration_s": round(we - ws),
                    "tca": tca, "max_el_deg": round(gt["el"], 1), "az_tca_deg": round(gt["az"], 1), "range_km": round(gt["rng"], 1),
                    "min_off_nadir_deg": round(gb["off"], 1), "ground_dist_km": round(gt["lam"] * RE, 1),
                    "sub_lat": round(gt["lat"], 4), "sub_lon": round(gt["lon"], 4), "alt_km": round(gt["alt"], 1),
                    "direction": "升軌" if l2 > l1 else "降軌", "look_deg": look, "look_from": sj["look_from"],
                    "swath_radius_km": round(lam_eta(gt["alt"], look) * RE, 1), "coverable": coverable,
                    "img_start": t0 + timedelta(seconds=img_s) if coverable else None,
                    "img_end": t0 + timedelta(seconds=img_e) if coverable else None,
                    "img_duration_s": round(img_e - img_s) if coverable else 0,
                    "sun_el_deg": round(sun, 1), "sunlit": sunlit, "tle_epoch": epoch,
                    "lead_days": round(lead, 2) if math.isfinite(lead) else None, "est_err_s": None,
                })
    return out


# ── 歷史誤差 ─────────────────────────────────────────────────────────────────

def bucket_of(d: float) -> int | None:
    if d < 0.5:
        return None
    for i, b in enumerate(LEAD_BUCKETS):
        if d <= b + 0.5:
            return i
    return None


def sat_errors(tles: list[tuple[datetime, str, str]]) -> list[tuple[int, float, float]]:
    """TLE 歷史（每 12 小時一筆）兩兩比對 → (分組, 位置誤差 km, |沿軌時間誤差| s)"""
    sats = [(t, Satrec.twoline2rv(l1, l2)) for t, l1, l2 in tles]
    out = []
    for j, (tj, sj) in enumerate(sats):
        jd, fr = jday(tj.year, tj.month, tj.day, tj.hour, tj.minute, tj.second + tj.microsecond / 1e6)
        e1, rj, vj = sj.sgp4(jd, fr)
        if e1:
            continue
        rj, vj = np.array(rj), np.array(vj)
        sp = float(np.linalg.norm(vj))
        if sp < 1e-6:
            continue
        for ti, si in sats[:j]:
            b = bucket_of((tj - ti).total_seconds() / 86400.0)
            if b is None:
                continue
            e2, ri, _ = si.sgp4(jd, fr)
            if e2:
                continue
            dr = np.array(ri) - rj
            along = float(dr @ vj) / sp
            out.append((b, float(np.linalg.norm(dr)), abs(along / sp)))
    return out


def summarize(samples: list[tuple[int, float, float]]) -> list[dict[str, Any]]:
    out = []
    for bi, b in enumerate(LEAD_BUCKETS):
        p = np.array([s[1] for s in samples if s[0] == bi])
        t = np.array([s[2] for s in samples if s[0] == bi])
        if not len(p):
            continue
        # 與 Rust f64::round 相同（0.5 進位）；Python round() 是銀行家捨入，偶數樣本中位數會取到不同位置
        q = lambda a, x: float(np.sort(a)[int(math.floor((len(a) - 1) * x + 0.5))])
        out.append({"lead_days": b, "n": int(len(p)), "pos_med_km": round(q(p, .5), 2), "pos_p90_km": round(q(p, .9), 2),
                    "time_med_s": round(q(t, .5), 1), "time_p90_s": round(q(t, .9), 1)})
    return out


def est_err(curve: list[dict[str, Any]], lead: float) -> float | None:
    if not curve:
        return None
    if lead <= curve[0]["lead_days"]:
        return curve[0]["time_p90_s"] * max(lead / curve[0]["lead_days"], 0.0)
    for a, b in zip(curve, curve[1:]):
        if lead <= b["lead_days"]:
            f = (lead - a["lead_days"]) / (b["lead_days"] - a["lead_days"])
            return a["time_p90_s"] + f * (b["time_p90_s"] - a["time_p90_s"])
    return curve[-1]["time_p90_s"] * lead / curve[-1]["lead_days"]


# ── 太陽 ─────────────────────────────────────────────────────────────────────

def sun_elevation(tg: Target, t: datetime) -> float:
    import astronomy
    obs = astronomy.Observer(tg.lat, tg.lon, tg.alt_m)
    at = astronomy.Time.Make(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6)
    eq = astronomy.Equator(astronomy.Body.Sun, at, obs, True, True)
    return astronomy.Horizon(at, obs, eq.ra, eq.dec, astronomy.Refraction.Airless).altitude


def sun_events(tg: Target, day0_utc: datetime) -> tuple[datetime | None, datetime | None, str]:
    """day0_utc：當地日期 00:00 換算成 UTC；回傳當地日期內的日出、日落（UTC）"""
    import astronomy
    obs = astronomy.Observer(tg.lat, tg.lon, tg.alt_m)
    t = astronomy.Time.Make(day0_utc.year, day0_utc.month, day0_utc.day, day0_utc.hour, day0_utc.minute, day0_utc.second)
    end = day0_utc + timedelta(days=1)

    def find(direction):
        r = astronomy.SearchRiseSet(astronomy.Body.Sun, obs, direction, t, 1.0)
        if r is None:
            return None
        dt = r.Utc().replace(tzinfo=timezone.utc)
        return dt if dt < end else None

    rise, sset = find(astronomy.Direction.Rise), find(astronomy.Direction.Set)
    note = ""
    if rise is None and sset is None:
        note = "極晝" if sun_elevation(tg, day0_utc + timedelta(hours=12)) > 0 else "極夜"
    return rise, sset, note


def local_days(t0: datetime, span: float, tz: timezone) -> list[date]:
    d, last, out = t0.astimezone(tz).date(), (t0 + timedelta(seconds=span)).astimezone(tz).date(), []
    while d <= last:
        out.append(d)
        d += timedelta(days=1)
    return out
