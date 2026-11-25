"""過頂預報演算法（scenario04/physics/forecast.py）：幾何、ISS 過台北（與逐秒掃描比對）、偵照角度、日出日落、誤差曲線。"""
import math
from datetime import datetime, timedelta, timezone

from sgp4.api import Satrec, jday

from scenario04.physics import forecast as fc

L1 = "1 25544U 98067A   24001.50000000  .00016717  00000-0  10270-3 0  9005"
L2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537"
T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
REQ = {"mask_deg": 10.0, "look_deg": 30.0, "only_coverable": False, "daylight": "none", "sun_min_deg": 0.0}
SJ = {"norad": 25544, "name": "ISS", "sensor": "optical", "look": 30.0, "look_from": "預設", "line1": L1, "line2": L2}


def test_footprint_geometry():
    assert abs(fc.lam_eta(500, 15) * fc.RE - 135) < 5
    assert abs(fc.lam_eta(500, 30) * fc.RE - 290) < 10
    assert abs(math.degrees(fc.lam_eps(500, 0)) - 22.0) < 0.3
    assert fc.lam_eta(500, 80) <= fc.lam_eps(500, 0) + 1e-9


def _elev_scan(tg, days):
    """獨立參考：逐秒 SGP4 求仰角 ≥ 10° 的時段"""
    sat = Satrec.twoline2rv(L1, L2)
    x0, y0, z0, sl, cl, so, co = tg.obs
    out, inside = [], False
    for k in range(int(days * 86400)):
        t = T0 + timedelta(seconds=k)
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second)
        _, r, _ = sat.sgp4(jd, fr)
        g = float(fc.gmst_arr(__import__("numpy").array([jd + fr]))[0])
        xe, ye, ze = math.cos(g) * r[0] + math.sin(g) * r[1], -math.sin(g) * r[0] + math.cos(g) * r[1], r[2]
        dx, dy, dz = xe - x0, ye - y0, ze - z0
        el = math.degrees(math.asin((cl * co * dx + cl * so * dy + sl * dz) / math.sqrt(dx * dx + dy * dy + dz * dz)))
        if el >= 10 and not inside:
            inside, start = True, t
        elif el < 10 and inside:
            inside = False
            out.append((start, t))
    return out


def test_iss_pass_over_taipei_matches_scan():
    tg = fc.Target("台北", 25.033, 121.565, 10)
    ps = fc.sat_passes(SJ, [tg], T0, 2 * 86400, REQ, lambda t, x: 0.0)
    ref = _elev_scan(tg, 2)
    assert ps and len(ps) == len(ref)
    for p, (s, e) in zip(ps, ref):
        assert abs((p["start"] - s).total_seconds()) <= 1.5
        assert abs((p["end"] - e).total_seconds()) <= 1.5
        assert p["max_el_deg"] >= 9.8 and p["start"] <= p["tca"] <= p["end"]
        if p["coverable"]:
            assert p["min_off_nadir_deg"] <= 30.3 and p["img_duration_s"] > 0


def test_look_angle_and_region():
    tg = fc.Target("台北", 25.033, 121.565, 10)
    req = dict(REQ, only_coverable=True)
    n30 = len(fc.sat_passes(SJ, [tg], T0, 5 * 86400, req, lambda t, x: 0.0))
    n15 = len(fc.sat_passes(dict(SJ, look=15.0), [tg], T0, 5 * 86400, req, lambda t, x: 0.0))
    region = fc.Target("區域", 25.033, 121.565, 10, radius_km=300)
    nr = len(fc.sat_passes(SJ, [region], T0, 5 * 86400, req, lambda t, x: 0.0))
    assert n15 <= n30 <= nr and n30 > 0


def test_daylight_filter():
    tg = fc.Target("台北", 25.033, 121.565, 10)
    allp = fc.sat_passes(SJ, [tg], T0, 3 * 86400, REQ, fc.sun_elevation)
    day = fc.sat_passes(SJ, [tg], T0, 3 * 86400, dict(REQ, daylight="optical"), fc.sun_elevation)
    assert all(p["sunlit"] for p in day) and len(day) <= len(allp)
    assert any(not p["sunlit"] for p in allp)


def test_sun_events_taipei():
    tz = timezone(timedelta(hours=8))
    day0 = datetime(2024, 1, 1, tzinfo=tz).astimezone(timezone.utc)
    r, s, note = fc.sun_events(fc.Target("台北", 25.033, 121.565, 10), day0)
    assert r.astimezone(tz).strftime("%H:%M") == "06:38" and s.astimezone(tz).hour == 17 and note == ""


def test_error_curve():
    c = [{"lead_days": 1.0, "time_p90_s": 2.0}, {"lead_days": 3.0, "time_p90_s": 10.0}]
    assert fc.est_err(c, 2.0) == 6.0 and fc.est_err(c, 0.5) == 1.0 and fc.est_err(c, 6.0) == 20.0
    assert fc.bucket_of(0.2) is None and fc.bucket_of(1.2) == 0 and fc.bucket_of(4.0) == 3
    s = fc.summarize([(0, 1.0, 1.0), (0, 3.0, 2.0), (2, 9.0, 4.0)])
    assert len(s) == 2 and s[0]["pos_med_km"] == 3.0
