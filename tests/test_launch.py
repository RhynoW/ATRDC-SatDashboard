"""一箭多星軌道驗證（scenario04/api/launch.py）：根數解析、分群排序、機動偵測、SupGP 比對與編號互換偵測。"""
from datetime import timedelta

from scenario04.api import launch as la
from scenario04.api import obs as ob

L1 = "1 25544U 98067A   26329.50000000  .00016717  00000-0  10270-3 0  9994"
L2 = "2 25544  51.6400 208.9163 0006317  69.9862 290.2104 15.50000000000017"


def _tle(norad, intl, ma=290.2104, n=15.5, raan=208.9163, epoch="26329.50000000"):
    l1 = f"1 {norad:05d}U {intl:<8s} {epoch}  .00016717  00000-0  10270-3 0  999"
    l2 = f"2 {norad:05d}  51.6400 {raan:8.4f} 0006317  69.9862 {ma:8.4f} {n:11.8f}00001"
    return l1.ljust(69, "0")[:69], l2.ljust(69, "0")[:69]


def test_elements_and_wrap():
    e = la.elements(L1, L2)
    assert abs(e["incl"] - 51.64) < 1e-9 and abs(e["ecc"] - 0.0006317) < 1e-12 and abs(e["bstar"] - 1.027e-4) < 1e-12
    assert 400 < e["perigee"] < e["apogee"] < 440 and abs(e["period"] - 92.903) < 0.01
    assert la._wrap(190) == -170 and la._wrap(-180) == 180 and la._wrap(359) == -1


def test_group_order_and_supgp_swap():
    # 同軌道面三顆（平近點角 0.0／0.5／1.0° → 沿軌排序），另一顆不同升交點（另一群）
    idx = {}
    for i, (ma, raan) in enumerate([(10.0, 208.9163), (10.5, 208.9163), (11.0, 208.9163), (10.0, 230.0)]):
        l1, l2 = _tle(90001 + i, "26300A", ma=ma, raan=raan)
        idx[90001 + i] = {"name": f"OBJECT {chr(65 + i)}", "line1": l1, "line2": l2}
    # SupGP：90002 的位置被標成 90003、90003 的位置被標成 90002（互換）；90001 正常
    sup = []
    for n_lab, src in ((90001, 90001), (90003, 90002), (90002, 90003)):
        l1, l2 = idx[src]["line1"], idx[src]["line2"]
        l1 = l1[:2] + f"{n_lab:05d}" + l1[7:]
        l2 = l2[:2] + f"{n_lab:05d}" + l2[7:]
        sup.append({"file": "test", "name": f"SAT-{n_lab}", "line1": l1, "line2": l2, "norad": n_lab, "tag": "2026-300", "el": la.elements(l1, l2)})
    sup.sort(key=lambda s: (s["file"], s["line1"]))
    r = la.analyze("2026-300", idx, {}, {"launch": {}}, {"rows": sup, "meta": None}, history={})
    g = {x["id"]: x for x in r["groups"]}
    assert g["G1"]["count"] == 3 and g["G2"]["count"] == 1
    assert g["G1"]["order"] == [90003, 90002, 90001]          # 平近點角大者在前
    m = {x["norad"]: x for x in r["members"]}
    assert m[90001]["du_deg"] == 0.0 and abs(m[90003]["du_deg"] - 1.0) < 0.05
    assert m[90001]["supgp"]["status"] == "matched" and m[90001]["supgp"]["norad_consistent"] and not m[90001]["supgp"]["swap_suspected"]
    assert m[90002]["supgp"]["norad"] == 90003 and m[90002]["supgp"]["swap_suspected"]
    assert m[90003]["supgp"]["swap_suspected"]


def test_evolution_maneuver_and_decay():
    base = ob.tle_epoch(L1)
    hist = []
    for k, n in enumerate([15.50, 15.50, 15.48, 15.47, 15.47]):   # 平均運動下降＝半長軸上升（升軌）
        t = base + timedelta(days=k)
        l1, l2 = _tle(90001, "26300A", n=n, epoch=f"{26329.5 + k:014.8f}")
        hist.append((t, l1, l2))
    e = la.evolution(hist)
    assert e["status"] == "maneuvering" and len(e["raises"]) >= 2 and e["raise_total_km"] > 5
    hist2 = []
    for k in range(6):
        l1, l2 = _tle(90001, "26300A", n=15.50 + k * 0.002, epoch=f"{26329.5 + k:014.8f}")
        hist2.append((base + timedelta(days=k), l1, l2))
    e2 = la.evolution(hist2)
    assert e2["status"] == "decaying" and e2["slope_km_day"] < -0.05 and not e2["raises"]
