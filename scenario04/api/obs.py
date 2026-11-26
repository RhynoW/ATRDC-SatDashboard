"""觀測資料模型：地面 POI、觀測紀錄、軌道解、身分主張 → 交叉驗證與衛星身分／軌道／觀測狀態（對應 Rust 單機版 src/obs.rs）。

線上版的資料只存在使用者瀏覽器（web/static/js/obs_store.js，localStorage），本模組只做無狀態計算：
POST /api/obs/check 傳入四種紀錄，回傳每筆的交叉驗證結果與每顆衛星的狀態摘要。

交叉驗證（與 Rust 同一套公式，結果逐筆一致）：
- 觀測殘差：以目錄 TLE（與使用者軌道解）SGP4 傳播到觀測時間，算觀測站看到的方向，與實測方向的角距。
  RA/Dec 視為 J2000：TEME 近似為當日平赤道（忽略章動，誤差 < 20″），再以 IAU 1976 歲差轉到 J2000；
  方位／仰角以 WGS84 站心座標計算。≤ 0.5° 符合、≤ 2° 勉強符合、其餘不符合。同次發射的其他物體一併比對，找最符合者。
- 軌道比對：軌道解與目錄 TLE 在軌道解曆元 ±12 小時（每小時一點）的位置差；≤ 10 km 一致、≤ 50 km 接近、其餘不一致。
  同次發射的物體全部比對，找軌道最接近者（協助判斷 OBJECT A/B/C 各是誰）。
- 身分主張：與 Space-Track 正式名稱、同一顆衛星的其他主張比對。
任務類型與能力（mission_status／capability_status）一律為 unknown，不自動歸因。
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, jsonify, render_template, request
from sgp4.api import Satrec, jday

from ..ingestion.index import get_sat_index
from .changes import NONAME_RE
from .satadmin import get_satcat, intl_code

bp = Blueprint("obs", __name__)

SOURCE_CONF = {"official": "high", "gcat": "medium", "amateur": "medium", "own": "medium", "news": "low", "other": "low"}
REVIEWS = ("unreviewed", "accepted", "disputed", "rejected")
OBS_OK, OBS_MARGINAL = 0.5, 2.0      # 觀測殘差門檻（度）
ORB_OK, ORB_CLOSE = 10.0, 50.0       # 軌道位置差門檻（km）
GRID_H = 12                          # 軌道比對：曆元 ±12 小時、每小時一點
TOP_N = 5
A_WGS84 = 6378.137
E2 = (1 / 298.257223563) * (2 - 1 / 298.257223563)
AS2R = math.pi / (180 * 3600)
TIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?Z?$")


def rnd(x: float, d: int) -> float:
    """四捨五入（遠離零），與 Rust f64::round 相同"""
    f = 10.0 ** d
    return math.copysign(math.floor(abs(x) * f + 0.5) / f, x)


def parse_time(s: Any) -> datetime | None:
    m = TIME_RE.match(str(s or "").strip())
    if not m:
        return None
    y, mo, d, h, mi = (int(m.group(i)) for i in range(1, 6))
    sec = int(m.group(6) or 0)
    us = int((m.group(7) or "0")[:6].ljust(6, "0"))
    try:
        return datetime(y, mo, d, h, mi, sec, us, tzinfo=timezone.utc)
    except ValueError:
        return None


def fmt_time(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S") + (f".{t.microsecond // 1000:03d}" if t.microsecond else "")


def tle_epoch(line1: str) -> datetime | None:
    """與 Rust satadmin::tle_epoch 相同（取到毫秒）"""
    try:
        yy = int(line1[18:20]); doy = float(line1[20:32])
    except (ValueError, IndexError):
        return None
    ms = math.floor((doy - 1.0) * 86_400_000.0 + 0.5)
    return datetime(2000 + yy if yy < 57 else 1900 + yy, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)


def jd_of(t: datetime) -> float:
    return t.timestamp() / 86400.0 + 2440587.5


def gmst(t: datetime) -> float:
    d = jd_of(t) - 2451545.0
    tc = d / 36525.0
    return math.radians((280.46061837 + 360.98564736629 * d + 0.000387933 * tc * tc - tc ** 3 / 38710000.0) % 360.0)


class Prop:
    def __init__(self, l1: str, l2: str):
        self.sat = Satrec.twoline2rv(l1.rstrip(), l2.rstrip())

    def pos(self, t: datetime) -> tuple[float, float, float] | None:
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6)
        e, r, _ = self.sat.sgp4(jd, fr)
        if e != 0 or not all(math.isfinite(v) for v in r) or r[0] ** 2 + r[1] ** 2 + r[2] ** 2 < 6378.135 ** 2:
            return None
        return r


def make_prop(l1: str, l2: str) -> Prop | None:
    try:
        if len(l1.rstrip()) < 60 or len(l2.rstrip()) < 60 or not l1.startswith("1 ") or not l2.startswith("2 "):
            return None
        p = Prop(l1, l2)
        return p if p.sat.error == 0 else None
    except Exception:
        return None


def station_ecef(lat: float, lon: float, alt_m: float) -> tuple[float, ...]:
    la, lo = math.radians(lat), math.radians(lon)
    sl, cl, so, co = math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)
    n = A_WGS84 / math.sqrt(1 - E2 * sl * sl)
    h = alt_m / 1000.0
    return ((n + h) * cl * co, (n + h) * cl * so, (n * (1 - E2) + h) * sl, sl, cl, so, co)


def precess_to_j2000(v: tuple[float, float, float], t: datetime) -> tuple[float, float, float]:
    """當日平赤道 → J2000（IAU 1976 歲差，P^T·v）"""
    T = (jd_of(t) - 2451545.0) / 36525.0
    zeta = (2306.2181 * T + 0.30188 * T * T + 0.017998 * T ** 3) * AS2R
    z = (2306.2181 * T + 1.09468 * T * T + 0.018203 * T ** 3) * AS2R
    th = (2004.3109 * T - 0.42665 * T * T - 0.041833 * T ** 3) * AS2R
    cz, sz, ct, st, cx, sx = math.cos(z), math.sin(z), math.cos(th), math.sin(th), math.cos(zeta), math.sin(zeta)
    p = ((cz * ct * cx - sz * sx, -cz * ct * sx - sz * cx, -cz * st),
         (sz * ct * cx + cz * sx, -sz * ct * sx + cz * cx, -sz * st),
         (st * cx, -st * sx, ct))
    return (p[0][0] * v[0] + p[1][0] * v[1] + p[2][0] * v[2],
            p[0][1] * v[0] + p[1][1] * v[1] + p[2][1] * v[2],
            p[0][2] * v[0] + p[1][2] * v[1] + p[2][2] * v[2])


def look(r: tuple[float, float, float], t: datetime, stn: tuple[float, ...]) -> dict[str, float]:
    """衛星 TEME 位置 → 觀測站看到的 RA/Dec（J2000）、方位／仰角、斜距"""
    g = gmst(t)
    cg, sg = math.cos(g), math.sin(g)
    x0, y0, z0, sl, cl, so, co = stn
    xe, ye, ze = cg * r[0] + sg * r[1], -sg * r[0] + cg * r[1], r[2]
    dx, dy, dz = xe - x0, ye - y0, ze - z0
    e = -so * dx + co * dy
    n = -sl * co * dx - sl * so * dy + cl * dz
    u = cl * co * dx + cl * so * dy + sl * dz
    rng = math.sqrt(e * e + n * n + u * u)
    el = math.degrees(math.asin(max(-1.0, min(1.0, u / max(rng, 1e-3)))))
    az = math.degrees(math.atan2(e, n)) % 360.0
    # 站心向量轉回 TEME（ECEF 繞 z 轉 +GMST）
    rho = (cg * dx - sg * dy, sg * dx + cg * dy, dz)
    v = precess_to_j2000(rho, t)
    ra = math.degrees(math.atan2(v[1], v[0])) % 360.0
    dec = math.degrees(math.asin(max(-1.0, min(1.0, v[2] / math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)))))
    return {"ra": ra, "dec": dec, "az": az, "el": el, "rng": rng}


def unit_radec(ra: float, dec: float) -> tuple[float, float, float]:
    a, d = math.radians(ra), math.radians(dec)
    return (math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d))


def unit_azel(az: float, el: float) -> tuple[float, float, float]:
    a, e = math.radians(az), math.radians(el)
    return (math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e))


def sep_deg(u: tuple[float, ...], v: tuple[float, ...]) -> float:
    cx = u[1] * v[2] - u[2] * v[1]
    cy = u[2] * v[0] - u[0] * v[2]
    cz = u[0] * v[1] - u[1] * v[0]
    return math.degrees(math.atan2(math.sqrt(cx * cx + cy * cy + cz * cz), u[0] * v[0] + u[1] * v[1] + u[2] * v[2]))


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _norad(v: Any) -> int | None:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 < n < 1_000_000 else None


ALPHA5 = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # TLE Alpha-5：A=10 … Z=33（不用 I、O）


def tle_norad(line: str) -> int | None:
    """TLE 第 3–7 欄的 NORAD 編號（支援 Alpha-5，例如 A1419＝101419）"""
    s = str(line or "")[2:7]
    if len(s) == 5 and s[0] in ALPHA5 and s[1:].isdigit():
        return (ALPHA5.index(s[0]) + 10) * 10000 + int(s[1:])
    s = s.strip()
    return _norad(s) if s.isdigit() else None


def obs_status(sep: float | None) -> str:
    if sep is None:
        return "unknown"
    return "consistent" if sep <= OBS_OK else "marginal" if sep <= OBS_MARGINAL else "inconsistent"


def orb_status(dmax: float | None) -> str:
    if dmax is None:
        return "unknown"
    return "consistent" if dmax <= ORB_OK else "close" if dmax <= ORB_CLOSE else "divergent"


class Ctx:
    """目錄（TLE 索引＋SATCAT）與同次發射分組"""

    def __init__(self, idx: dict, sc: dict):
        self.idx, self.sc = idx, sc
        self._props: dict[int, Prop | None] = {}
        self.tags: dict[str, list[int]] = {}
        for n, s in idx.items():
            t = intl_code(s.get("line1") or "")[:8]
            if len(t) == 8:
                self.tags.setdefault(t, []).append(n)
        for v in self.tags.values():
            v.sort()

    def prop(self, n: int) -> Prop | None:
        if n not in self._props:
            s = self.idx.get(n)
            self._props[n] = make_prop(s["line1"], s["line2"]) if s and s.get("line1") and s.get("line2") else None
        return self._props[n]

    def tag_of(self, n: int) -> str:
        s = self.idx.get(n)
        t = intl_code(s.get("line1") or "")[:8] if s else ""
        if len(t) < 8:
            t = (self.sc.get(n) or {}).get("intl", "")[:8]
        return t

    def name(self, n: int) -> str:
        s = self.idx.get(n)
        return (s or {}).get("name", "").strip() or (self.sc.get(n) or {}).get("name", "")

    def official(self, n: int) -> str:
        nm = ((self.sc.get(n) or {}).get("name") or "").strip()
        return "" if NONAME_RE.match(nm.upper()) else nm


def _obs_dir(o: dict) -> tuple[str, tuple[float, float, float]] | None:
    ra, dec, az, el = (_num(o.get(k)) for k in ("ra_deg", "dec_deg", "az_deg", "el_deg"))
    if ra is not None and dec is not None and 0 <= ra <= 360 and -90 <= dec <= 90:
        return "radec", unit_radec(ra, dec)
    if az is not None and el is not None and 0 <= az <= 360 and -90 <= el <= 90:
        return "azel", unit_azel(az, el)
    return None


def _predict_sep(p: Prop | None, t: datetime, stn: tuple, frame: str, u: tuple) -> tuple[float | None, dict | None]:
    if p is None:
        return None, None
    r = p.pos(t)
    if r is None:
        return None, None
    lk = look(r, t, stn)
    v = unit_radec(lk["ra"], lk["dec"]) if frame == "radec" else unit_azel(lk["az"], lk["el"])
    return sep_deg(u, v), lk


def check_observation(o: dict, ctx: Ctx, pois: dict[str, dict], orbits_by_norad: dict[int, list[dict]]) -> dict:
    out: dict[str, Any] = {"id": o.get("id"), "norad": _norad(o.get("norad")), "errors": [], "warnings": [], "status": "unknown",
                           "sep_deg": None, "predicted": None, "by_orbit": [], "candidates": [], "best": None}
    t = parse_time(o.get("time"))
    poi = pois.get(str(o.get("station") or "").strip().upper())
    d = _obs_dir(o)
    if t is None:
        out["errors"].append("時間格式錯誤（YYYY-MM-DD HH:MM:SS UTC）")
    if poi is None:
        out["errors"].append("找不到觀測站（地面 POI）")
    if d is None:
        out["errors"].append("需要 RA/Dec 或方位／仰角")
    if out["errors"]:
        return out
    lat, lon, alt = _num(poi.get("lat")), _num(poi.get("lon")), _num(poi.get("alt_m")) or 0.0
    if lat is None or lon is None:
        out["errors"].append("觀測站經緯度錯誤")
        return out
    stn = station_ecef(lat, lon, alt)
    frame, u = d
    out["frame"] = frame
    n = out["norad"]
    if n is None:
        out["warnings"].append("未指定 NORAD，無法比對")
        return out
    s, lk = _predict_sep(ctx.prop(n), t, stn, frame, u)
    if s is not None:
        out["sep_deg"] = rnd(s, 3)
        out["predicted"] = {k: rnd(lk[k], 3) for k in ("ra", "dec", "az", "el")} | {"rng_km": rnd(lk["rng"], 1)}
        if lk["el"] < 0:
            out["warnings"].append("預測時衛星在觀測站地平線下")
        ep = tle_epoch(ctx.idx[n]["line1"])
        if ep is not None and abs((ep - t).total_seconds()) > 7 * 86400:
            out["warnings"].append(f"目錄 TLE 曆元距觀測 {abs((ep - t).total_seconds()) / 86400:.1f} 天，預測誤差可能偏大")
    elif n not in ctx.idx:
        out["warnings"].append("目錄中沒有此 NORAD 的 TLE")
    out["status"] = obs_status(out["sep_deg"])
    for orb in orbits_by_norad.get(n, []):
        so, _ = _predict_sep(make_prop(orb.get("line1", ""), orb.get("line2", "")), t, stn, frame, u)
        if so is not None:
            out["by_orbit"].append({"orbit_id": orb.get("id"), "sep_deg": rnd(so, 3), "status": obs_status(so)})
    tag = ctx.tag_of(n)
    cands = []
    for m in ctx.tags.get(tag, []):
        sm, lm = _predict_sep(ctx.prop(m), t, stn, frame, u)
        if sm is not None:
            cands.append((rnd(sm, 3), m, rnd(lm["el"], 1)))
    cands.sort()
    out["candidates"] = [{"norad": m, "name": ctx.name(m), "sep_deg": sm, "el_deg": el} for sm, m, el in cands[:TOP_N]]
    if cands:
        out["best"] = cands[0][1]
        if cands[0][1] != n and out["sep_deg"] is not None and cands[0][0] < out["sep_deg"]:
            out["warnings"].append(f"同次發射中 NORAD {cands[0][1]}（{ctx.name(cands[0][1])}）較符合：{cands[0][0]:.3f}°")
    return out


def check_orbit(s: dict, ctx: Ctx) -> dict:
    out: dict[str, Any] = {"id": s.get("id"), "norad": None, "errors": [], "warnings": [], "status": "unknown", "epoch": None,
                           "d0_km": None, "dmax_km": None, "catalog_epoch_days": None, "candidates": [], "best": None}
    l1, l2 = str(s.get("line1") or ""), str(s.get("line2") or "")
    p = make_prop(l1, l2)
    ep = tle_epoch(l1) if p else None
    if p is None or ep is None:
        out["errors"].append("TLE 格式錯誤或無法傳播")
        return out
    n = tle_norad(l1)
    out["norad"] = n
    out["epoch"] = fmt_time(ep)
    times = [ep + timedelta(hours=k) for k in range(-GRID_H, GRID_H + 1)]
    mine = [p.pos(t) for t in times]

    def diff(q: Prop | None) -> tuple[float | None, float | None]:
        if q is None:
            return None, None
        ds = []
        d0 = None
        for k, (t, a) in enumerate(zip(times, mine)):
            if a is None:
                continue
            b = q.pos(t)
            if b is None:
                continue
            d = math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)
            ds.append(d)
            if k == GRID_H:
                d0 = d
        return (d0, max(ds)) if ds else (None, None)

    if n is not None and n in ctx.idx:
        d0, dm = diff(ctx.prop(n))
        out["d0_km"] = rnd(d0, 2) if d0 is not None else None
        out["dmax_km"] = rnd(dm, 2) if dm is not None else None
        ce = tle_epoch(ctx.idx[n]["line1"])
        if ce is not None:
            out["catalog_epoch_days"] = rnd((ce - ep).total_seconds() / 86400.0, 2)
            if abs((ce - ep).total_seconds()) > 7 * 86400:
                out["warnings"].append(f"目錄 TLE 曆元與軌道解相差 {abs((ce - ep).total_seconds()) / 86400:.1f} 天，位置差含傳播誤差")
        out["status"] = orb_status(out["dmax_km"])
    else:
        out["status"] = "no_catalog"
        out["warnings"].append("目錄中沒有此 NORAD 的 TLE（獨立軌道解）")
    tag = intl_code(l1)[:8]
    cands = []
    for m in ctx.tags.get(tag, []):
        _, dm = diff(ctx.prop(m))
        if dm is not None:
            cands.append((rnd(dm, 2), m))
    cands.sort()
    out["candidates"] = [{"norad": m, "name": ctx.name(m), "dmax_km": dm} for dm, m in cands[:TOP_N]]
    if cands:
        out["best"] = cands[0][1]
        if cands[0][1] != n and (out["dmax_km"] is None or cands[0][0] < out["dmax_km"]):
            out["warnings"].append(f"軌道最接近 NORAD {cands[0][1]}（{ctx.name(cands[0][1])}，最大差 {cands[0][0]:.2f} km）")
    return out


def check_claim(c: dict, ctx: Ctx, claims_by_norad: dict[int, list[dict]]) -> dict:
    n = _norad(c.get("norad"))
    out: dict[str, Any] = {"id": c.get("id"), "norad": n, "errors": [], "warnings": [], "status": "unknown", "official_name": None}
    name = str(c.get("name") or "").strip()
    if n is None or not name:
        out["errors"].append("需要 NORAD 與名稱")
        return out
    off = ctx.official(n)
    out["official_name"] = off or None
    others = sorted({str(x.get("name") or "").strip() for x in claims_by_norad.get(n, [])
                     if x.get("id") != c.get("id") and x.get("review") != "rejected"} - {name, ""})
    if off and off.upper() == name.upper():
        out["status"] = "matches_official"
    elif off:
        out["status"] = "differs_official"
        out["warnings"].append(f"Space-Track 已正式命名為 {off}")
    elif others:
        out["status"] = "disputed"
    else:
        out["status"] = "uncontested"
    if others:
        out["warnings"].append("另有主張：" + "、".join(others))
    return out


def summarize(n: int, ctx: Ctx, obs: list[dict], orbs: list[dict], claims: list[dict]) -> dict:
    off = ctx.official(n)
    live = [c for c in claims if c.get("review") != "rejected" and str(c.get("name") or "").strip()]
    names = sorted({str(c["name"]).strip() for c in live})
    if off:
        ident = "confirmed"
    elif len(names) > 1:
        ident = "disputed"
    elif any(c.get("review") == "accepted" and (c.get("confidence") or SOURCE_CONF.get(c.get("source_type"), "low")) == "high" for c in live):
        ident = "confirmed"
    elif any(c.get("review") == "accepted" for c in live):
        ident = "probable"
    elif live:
        ident = "claimed"
    else:
        ident = "unknown"
    ok_orbs = [o for o in orbs if o["review"] != "rejected" and not o["chk"]["errors"]]
    if ok_orbs:
        last = max(ok_orbs, key=lambda o: o["chk"]["epoch"])
        orbit = {"consistent": "corroborated", "close": "close", "divergent": "divergent", "no_catalog": "independent_only"}.get(last["chk"]["status"], "unknown")
    else:
        orbit = "catalog_only" if n in ctx.idx else "none"
    st = [o["chk"]["status"] for o in obs if o["review"] != "rejected" and o["chk"]["status"] != "unknown"]
    if not st:
        ob = "none"
    elif all(s == "consistent" for s in st):
        ob = "consistent"
    elif any(s == "consistent" for s in st):
        ob = "mixed"
    elif all(s == "inconsistent" for s in st):
        ob = "inconsistent"
    else:
        ob = "marginal"
    tag = ctx.tag_of(n)
    best_claim = next((c for c in live if c.get("review") == "accepted"), live[0] if live else None)
    return {
        "norad": n, "name": ctx.name(n), "official_name": off or None, "launch_tag": tag or None, "in_catalog": n in ctx.idx,
        "identity_status": ident, "orbit_status": orbit, "observation_status": ob,
        "mission_status": "unknown", "capability_status": "unknown",
        "layers": {
            "launch_group": {"value": tag or None, "confidence": "high" if tag else None},
            "candidate_mission": {"value": names or None, "confidence": "medium" if names else None},
            "individual": {"value": off or (best_claim or {}).get("name"), "status": ident},
        },
        "counts": {"observations": len(obs), "orbits": len(orbs), "claims": len(claims)},
    }


def check_all(data: dict, idx: dict, sc: dict) -> dict:
    ctx = Ctx(idx, sc)
    pois = {str(p.get("name") or "").strip().upper(): p for p in data.get("pois") or [] if isinstance(p, dict)}
    orbits = [o for o in data.get("orbits") or [] if isinstance(o, dict)]
    claims = [c for c in data.get("claims") or [] if isinstance(c, dict)]
    observations = [o for o in data.get("observations") or [] if isinstance(o, dict)]
    orbits_by_norad: dict[int, list[dict]] = {}
    for o in orbits:
        n = tle_norad(str(o.get("line1") or ""))
        if n is not None and o.get("review") != "rejected":
            orbits_by_norad.setdefault(n, []).append(o)
    claims_by_norad: dict[int, list[dict]] = {}
    for c in claims:
        n = _norad(c.get("norad"))
        if n is not None:
            claims_by_norad.setdefault(n, []).append(c)
    r_obs = [check_observation(o, ctx, pois, orbits_by_norad) for o in observations[:2000]]
    r_orb = [check_orbit(o, ctx) for o in orbits[:1000]]
    r_clm = [check_claim(c, ctx, claims_by_norad) for c in claims[:5000]]
    per: dict[int, dict[str, list]] = {}
    for rec, chk in zip(observations, r_obs):
        if chk["norad"] is not None:
            per.setdefault(chk["norad"], {"o": [], "b": [], "c": []})["o"].append({"review": rec.get("review"), "chk": chk})
    for rec, chk in zip(orbits, r_orb):
        if chk["norad"] is not None:
            per.setdefault(chk["norad"], {"o": [], "b": [], "c": []})["b"].append({"review": rec.get("review"), "chk": chk})
    for rec, chk in zip(claims, r_clm):
        if chk["norad"] is not None:
            per.setdefault(chk["norad"], {"o": [], "b": [], "c": []})["c"].append(rec)
    sats = [summarize(n, ctx, v["o"], v["b"], v["c"]) for n, v in sorted(per.items(), reverse=True)]
    return {"observations": r_obs, "orbits": r_orb, "claims": r_clm, "sats": sats,
            "thresholds": {"obs_ok_deg": OBS_OK, "obs_marginal_deg": OBS_MARGINAL, "orbit_ok_km": ORB_OK, "orbit_close_km": ORB_CLOSE, "grid_hours": GRID_H}}


@bp.get("/obs")
def page_obs():
    return render_template("obs.html")


@bp.post("/api/obs/check")
def api_check():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "需要 JSON 物件"}), 400
    return jsonify(check_all(data, get_sat_index(), get_satcat(fetch=False)))
