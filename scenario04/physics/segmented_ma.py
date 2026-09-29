"""分段 M/A（等效面積／彈道係數）校準之再入預測（Remis 2026, arXiv 2607.20128 方法的輕量 Python 實作）。

方法
----
1. 從 TLE 序列（由新到舊）挑「錨點」：相鄰錨點間隔 ≥ 12 h；接近再入時，若兩筆 TLE 平均半長軸
   已下降 ≥ 10 km，間隔不足 12 h 也接受（Remis §4）。
2. 每一段（前錨點 → 後錨點）：以 SGP4 取前錨點 TLE 於其 epoch 的 TEME 位置速度為初值，數值積分
   （兩體 + J2/J3/J4 + NRLMSIS 大氣阻力）到後錨點 epoch，只調整彈道係數 B = Cd·A/m。
   以兩個試驗值（B=0 與 B_guess）線性內插，使「軌道平均半長軸」的模擬下降量等於 TLE 觀測下降量，
   再以 B_opt 驗證一次（殘差 > 10 m 時再做一次割線修正）。
   半長軸取「一個軌道週期內密切半長軸的時間平均」，SGP4 與數值積分兩邊用完全相同的定義，
   因此 SGP4 初值與數值模型間的常數偏差在「差值」中抵銷。
3. 推力疑似旗標：B 相對前幾段中位數跳升 ≥ jump_ratio 倍（預設 2），或半長軸上升（抬升），
   即視為推進器點火（Remis 以 STARLINK-34110 面積 4–5 → 18 m² 為例）；此時預測標為不可靠。
4. 預測：由最新 TLE 以「最近穩定段」的 B（名目）與近幾段 B 的範圍（±15% 下限）三條軌跡同時積分，
   大地高 < stop_alt_km（預設 80 km）的時刻為再入時刻；B 大者較早、B 小者較晚，構成不確定度區間。

力模型與假設（務實版，非 GMAT 等級）
- 重力：兩體 + J2/J3/J4 帶諧項（無田諧、無日月、無 SRP；SRP 對 Starlink 末段影響遠小於阻力）。
- 座標：TEME 視為慣性系；大氣隨地球自轉（v_rel = v − ωE × r）；緯度用地心緯度、高度用扁球近似
  （< 0.2° / 數十 m 等級誤差，對密度影響可忽略）。
- 密度：NRLMSISE-00（pymsis version=0，與 Remis 相同；MSIS_VERSION 可改 2.1），輸入每日 F10.7（前一日）、F10.7 81 日「尾隨」平均（不偷看未來）、
  每日 Ap。預測時刻之後的太空天氣一律以「截止日最後已知值持平」（persistence），在輸出中註明。
- 積分器：固定步長 RK4（預設 15 s），純 Python 迴圈；密度只在 60 s 節點上計算、節點間對 ln ρ 線性
  內插，每圈（chunk）以「上一圈密度剖面」為預測、算出真實位置後重算密度並迭代到 3% 內收斂，
  讓 pymsis 呼叫次數約為每 60 s 一點。

介面
----
    from segmented_ma import predict_reentry
    res = predict_reentry(tles, cutoff=None, n_segments=6, max_days=10)

`tles` 為 list[dict|tuple]：(line1, line2) 或 {"line1":..,"line2":..}（epoch 由 line1 解析）。
回傳 dict（全部可 JSON 序列化），見 predict_reentry docstring。
"""
from __future__ import annotations

import math
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from sgp4.api import Satrec
from sgp4.propagation import gstime

MU = 398600.4418            # km^3/s^2
RE = 6378.137               # km
FLAT = 1.0 / 298.257223563
J2 = 1.08262668e-3
J3 = -2.53265649e-6
J4 = -1.61962159e-6
WE = 7.2921158553e-5        # rad/s
JD_UNIX0 = 2440587.5

__all__ = ["predict_reentry", "calibrate_segments", "SpaceWeather", "parse_tles"]


# ════════════════════════════ 太空天氣 ════════════════════════════
class SpaceWeather:
    """讀 CelesTrak SW-All.csv 格式（本專案 space_weather_ap.csv 與 pymsis 內建檔同格式）。

    get(unix_times, cutoff_unix) → (f107_prev_day, f107a_trailing81, ap_daily) 陣列。
    日期 > 截止日（cutoff 的前一個完整 UTC 日）者，以截止日的值持平（persistence）。
    """

    _cache: dict[str, "SpaceWeather"] = {}

    def __init__(self, path: str | Path | None = None):
        path = Path(path) if path else self.default_path()
        self.path = str(path) if path else None
        self.day0 = None
        self.f107 = self.f81 = self.ap = None
        self.last_obs_day = None
        if path is None or not Path(path).exists():
            return
        import csv
        rows = []
        with open(path, newline="", encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            for r in rd:
                try:
                    d = datetime.strptime(r["DATE"], "%Y-%m-%d").date()
                    f = float(r["F10.7_OBS"]) if r.get("F10.7_OBS") else float("nan")
                    fl = r.get("F10.7_OBS_LAST81") or r.get("F10.7_OBS_CENTER81") or ""
                    fa = float(fl) if fl else float("nan")
                    a = float(r["AP_AVG"]) if r.get("AP_AVG") else float("nan")
                    typ = r.get("F10.7_DATA_TYPE", "")
                except (ValueError, KeyError):
                    continue
                rows.append((d, f, fa, a, typ))
        if not rows:
            return
        # 只保留日資料（跳過每月 PRM 預報列之後的缺值），依日期連續排列
        rows.sort(key=lambda x: x[0])
        d0 = rows[0][0]
        n = (rows[-1][0] - d0).days + 1
        f107 = np.full(n, np.nan); f81 = np.full(n, np.nan); ap = np.full(n, np.nan)
        last_obs = None
        for d, f, fa, a, typ in rows:
            i = (d - d0).days
            f107[i], f81[i], ap[i] = f, fa, a
            if typ == "OBS" and not math.isnan(a):
                last_obs = i
        # 前向填補缺值
        for arr, dflt in ((f107, 150.0), (f81, 150.0), (ap, 12.0)):
            last = dflt
            for i in range(n):
                if math.isnan(arr[i]):
                    arr[i] = last
                else:
                    last = arr[i]
        self.day0 = datetime(d0.year, d0.month, d0.day, tzinfo=timezone.utc).timestamp()
        self.f107, self.f81, self.ap = f107, f81, ap
        self.last_obs_day = last_obs

    @staticmethod
    def default_path() -> Path | None:
        """SW_ALL_PATH 環境變數 → settings.DB_DIR/SW-All.csv（每日管線／Dataset 下載）→ pymsis 內建檔（可能過時）。"""
        import os
        env = os.getenv("SW_ALL_PATH")
        if env and Path(env).exists():
            return Path(env)
        try:
            from ..config import settings
            p = settings.DB_DIR / "SW-All.csv"
            if p.exists():
                return p
        except Exception:  # noqa: BLE001
            pass
        try:
            import pymsis
            p = Path(pymsis.__file__).parent / "SW-All.csv"
            if p.exists():
                return p
        except ImportError:
            pass
        return None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "SpaceWeather":
        p = Path(path) if path else cls.default_path()
        mtime = p.stat().st_mtime if p and p.exists() else None
        key = f"{p}|{mtime}"                      # 檔案每日更新後自動重讀
        if key not in cls._cache:
            cls._cache.clear()
            cls._cache[key] = SpaceWeather(p)
        return cls._cache[key]

    def last_observed_date(self) -> str | None:
        if self.day0 is None or self.last_obs_day is None:
            return None
        return datetime.fromtimestamp(self.day0 + self.last_obs_day * 86400, tz=timezone.utc).date().isoformat()

    def get(self, unix_t: np.ndarray, cutoff_unix: float | None):
        unix_t = np.asarray(unix_t, float)
        if self.day0 is None:
            n = unix_t.size
            return np.full(n, 150.0), np.full(n, 150.0), np.full(n, 12.0)
        idx = np.floor((unix_t - self.day0) / 86400.0).astype(int)
        cap = len(self.ap) - 1
        if self.last_obs_day is not None:
            cap = min(cap, self.last_obs_day)
        if cutoff_unix is not None:
            cap = min(cap, int(math.floor((cutoff_unix - self.day0) / 86400.0)) - 1)
        idx_ap = np.clip(idx, 0, cap)
        idx_f = np.clip(idx - 1, 0, cap)          # MSIS：F10.7 取前一日
        return self.f107[idx_f], self.f81[idx_ap], self.ap[idx_ap]


MSIS_VERSION = 0   # 0 = NRLMSISE-00（與 Remis 相同，且約快 2 倍）；可改 2.1


def _density(unix_t, lon_deg, lat_deg, alt_km, sw: SpaceWeather, cutoff_unix):
    """NRLMSIS 質量密度 [kg/m^3]（向量化，一次 pymsis 呼叫）。"""
    import pymsis
    unix_t = np.asarray(unix_t, float)
    f, fa, ap = sw.get(unix_t, cutoff_unix)
    dates = (unix_t * 1e6).astype("int64").astype("datetime64[us]")
    aps = np.zeros((unix_t.size, 7)); aps[:, 0] = ap
    alt = np.clip(np.asarray(alt_km, float), 0.0, 1000.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = pymsis.calculate(dates, np.asarray(lon_deg, float), np.asarray(lat_deg, float), alt,
                               f, fa, aps, version=MSIS_VERSION)
    rho = np.asarray(out[..., 0], float).reshape(-1)
    return np.where(np.isfinite(rho) & (rho > 0), rho, 1e-20)


# ════════════════════════════ TLE 工具 ════════════════════════════
@dataclass
class TLE:
    line1: str
    line2: str
    sat: Satrec
    t: float        # epoch unix seconds
    a_kozai: float  # km（僅用於選段的 10 km 門檻）
    period: float   # s


def _jd_split(unix_t: np.ndarray):
    jd = np.asarray(unix_t, float) / 86400.0 + JD_UNIX0
    jd0 = np.floor(jd - 0.5) + 0.5
    return jd0, jd - jd0


def parse_tles(tles: Iterable[Any]) -> list[TLE]:
    out: list[TLE] = []
    for item in tles:
        if isinstance(item, dict):
            l1, l2 = item["line1"], item["line2"]
        else:
            l1, l2 = item[0], item[1]
        try:
            s = Satrec.twoline2rv(l1, l2)
        except Exception:  # noqa: BLE001
            continue
        t = (s.jdsatepoch + s.jdsatepochF - JD_UNIX0) * 86400.0
        n = s.no_kozai / 60.0  # rad/s
        if n <= 0:
            continue
        a = (MU / n ** 2) ** (1.0 / 3.0)
        out.append(TLE(l1, l2, s, t, a, 2 * math.pi / n))
    out.sort(key=lambda x: x.t)
    ded: list[TLE] = []
    for x in out:                       # 同一 epoch（< 60 s）只留最後一筆
        if ded and abs(x.t - ded[-1].t) < 60.0:
            ded[-1] = x
        else:
            ded.append(x)
    return ded


def _sgp4_state(tle: TLE, unix_t: float):
    jd, fr = _jd_split(np.array([unix_t]))
    e, r, v = tle.sat.sgp4(float(jd[0]), float(fr[0]))
    if e:
        raise RuntimeError(f"SGP4 error {e}")
    return [r[0], r[1], r[2], v[0], v[1], v[2]]


def _sgp4_mean_a(tle: TLE, t0: float, period: float, dt: float = 30.0) -> float:
    ts = t0 + np.arange(0.0, period, dt)
    jd, fr = _jd_split(ts)
    e, r, v = tle.sat.sgp4_array(jd, fr)
    ok = e == 0
    if ok.sum() < 0.8 * len(ts):
        raise RuntimeError("SGP4 平均半長軸取樣失敗")
    rr = np.linalg.norm(r[ok], axis=1); vv = np.linalg.norm(v[ok], axis=1)
    return float(np.mean(1.0 / (2.0 / rr - vv * vv / MU)))


# ════════════════════════════ 數值積分 ════════════════════════════
def _gmst(unix_t: float) -> float:
    jd = unix_t / 86400.0 + JD_UNIX0
    return gstime(jd)


class _Traj:
    __slots__ = ("s", "B", "prof", "trend", "active", "t_cross", "wsum", "wcnt", "min_alt", "t")

    def __init__(self, state, B, nwin):
        self.s = list(state); self.B = float(B); self.prof = None; self.trend = 0.0
        self.active = True; self.t_cross = None
        self.wsum = [0.0] * nwin; self.wcnt = [0] * nwin
        self.min_alt = 1e9; self.t = None


def _integrate_chunk(s, B, tc, L, h, node_dt, prof, stop_alt, windows, wsum, wcnt, record_nodes):
    """RK4 積分一個 chunk；回傳 (終態, 節點位置 list, 穿越時刻或 None, 最低高度)。純 Python。"""
    x, y, z, vx, vy, vz = s
    mu = MU; re2 = RE * RE; re3 = re2 * RE; re4 = re2 * re2
    c2 = -1.5 * J2 * mu * re2; c3 = -2.5 * J3 * mu * re3; c4 = 1.875 * J4 * mu * re4
    half_B = 0.5 * B * 1000.0        # a[km/s^2] = -0.5 B rho |v|v * 1000 （v 以 km/s、rho kg/m^3）
    nnode = len(prof) if prof is not None else 0
    exp = math.exp; sqrt = math.sqrt

    def acc(tau, x, y, z, vx, vy, vz):
        r2 = x * x + y * y + z * z
        r = sqrt(r2)
        ir2 = 1.0 / r2
        zr2 = z * z * ir2
        ir3 = ir2 / r
        ir5 = ir3 * ir2
        ir7 = ir5 * ir2
        k = -mu * ir3
        f2 = c2 * ir5
        ax = k * x + f2 * x * (1.0 - 5.0 * zr2)
        ay = k * y + f2 * y * (1.0 - 5.0 * zr2)
        az = k * z + f2 * z * (3.0 - 5.0 * zr2)
        f3 = c3 * ir7
        t3 = 3.0 * z - 7.0 * z * zr2
        ax += f3 * x * t3; ay += f3 * y * t3
        az += f3 * (6.0 * z * z - 7.0 * z * z * zr2 - 0.6 * r2)
        f4 = c4 * ir7
        t4 = 1.0 - 14.0 * zr2 + 21.0 * zr2 * zr2
        ax += f4 * x * t4; ay += f4 * y * t4
        az += f4 * z * (5.0 - 70.0 / 3.0 * zr2 + 21.0 * zr2 * zr2)
        if nnode:
            u = tau / node_dt
            i = int(u)
            if i >= nnode - 1:
                i = nnode - 2
            elif i < 0:
                i = 0
            fr = u - i
            lr = prof[i] + (prof[i + 1] - prof[i]) * fr
            rho = exp(lr)
            wx = vx + WE * y; wy = vy - WE * x
            vr = sqrt(wx * wx + wy * wy + vz * vz)
            q = -half_B * rho * vr
            ax += q * wx; ay += q * wy; az += q * vz
        return ax, ay, az

    nsteps = int(round(L / h))
    per_node = max(1, int(round(node_dt / h)))
    nodes = []
    t_cross = None
    min_alt = 1e9
    prev_alt = None
    nw = len(windows)
    for k in range(nsteps + 1):
        tau = k * h
        t = tc + tau
        r2 = x * x + y * y + z * z
        r = sqrt(r2)
        sphi2 = z * z / r2
        alt = r - RE * (1.0 - FLAT * sphi2)
        if alt < min_alt:
            min_alt = alt
        if record_nodes and k % per_node == 0:
            nodes.append((t, x, y, z))
        if nw:
            v2 = vx * vx + vy * vy + vz * vz
            a_osc = 1.0 / (2.0 / r - v2 / mu)
            for w in range(nw):
                if windows[w][0] <= t < windows[w][1]:
                    wsum[w] += a_osc; wcnt[w] += 1
        if stop_alt is not None and alt < stop_alt:
            if prev_alt is not None and prev_alt > alt:
                t_cross = t - h * (stop_alt - alt) / (prev_alt - alt)
            else:
                t_cross = t
            return [x, y, z, vx, vy, vz], nodes, t_cross, min_alt, k
        if k == nsteps:
            break
        prev_alt = alt
        # RK4
        a1 = acc(tau, x, y, z, vx, vy, vz)
        hh = 0.5 * h
        x2 = x + hh * vx; y2 = y + hh * vy; z2 = z + hh * vz
        u2 = vx + hh * a1[0]; v2_ = vy + hh * a1[1]; w2 = vz + hh * a1[2]
        a2 = acc(tau + hh, x2, y2, z2, u2, v2_, w2)
        x3 = x + hh * u2; y3 = y + hh * v2_; z3 = z + hh * w2
        u3 = vx + hh * a2[0]; v3 = vy + hh * a2[1]; w3 = vz + hh * a2[2]
        a3 = acc(tau + hh, x3, y3, z3, u3, v3, w3)
        x4 = x + h * u3; y4 = y + h * v3; z4 = z + h * w3
        u4 = vx + h * a3[0]; v4 = vy + h * a3[1]; w4 = vz + h * a3[2]
        a4 = acc(tau + h, x4, y4, z4, u4, v4, w4)
        h6 = h / 6.0
        x += h6 * (vx + 2 * u2 + 2 * u3 + u4)
        y += h6 * (vy + 2 * v2_ + 2 * v3 + v4)
        z += h6 * (vz + 2 * w2 + 2 * w3 + w4)
        vx += h6 * (a1[0] + 2 * a2[0] + 2 * a3[0] + a4[0])
        vy += h6 * (a1[1] + 2 * a2[1] + 2 * a3[1] + a4[1])
        vz += h6 * (a1[2] + 2 * a2[2] + 2 * a3[2] + a4[2])
    return [x, y, z, vx, vy, vz], nodes, None, min_alt, nsteps


def _nodes_llh(nodes):
    arr = np.asarray(nodes, float)
    t = arr[:, 0]; x = arr[:, 1]; y = arr[:, 2]; z = arr[:, 3]
    th0 = _gmst(float(t[0]))
    th = th0 + WE * (t - t[0])
    xe = np.cos(th) * x + np.sin(th) * y
    ye = -np.sin(th) * x + np.cos(th) * y
    r = np.sqrt(x * x + y * y + z * z)
    lat = np.degrees(np.arcsin(z / r))
    lon = np.degrees(np.arctan2(ye, xe)) % 360.0
    alt = r - RE * (1.0 - FLAT * (z / r) ** 2)
    return t, lon, lat, alt


def propagate(states: Sequence[Sequence[float]], Bs: Sequence[float], t0: float, t_end: float,
              sw: SpaceWeather, cutoff_unix: float | None = None, stop_alt: float | None = None,
              windows: Sequence[tuple[float, float]] = (), h: float = 15.0, node_dt: float = 120.0,
              chunk_s: float = 5400.0, tol_mean: float = 0.02, tol_max: float = 0.10, max_iter: int = 4) -> tuple[list[_Traj], dict]:
    """多條軌跡（不同 B）同時推進；每 chunk 一次 pymsis 批次呼叫。回傳 (軌跡, 統計)。"""
    trajs = [_Traj(s, B, len(windows)) for s, B in zip(states, Bs)]
    stats = {"msis_points": 0, "msis_calls": 0, "chunks": 0, "reintegrations": 0}
    chunk_s = max(node_dt * 4, round(chunk_s / node_dt) * node_dt)   # chunk ≈ 一圈、且為節點間距整數倍
    tc = t0
    nnode = int(round(chunk_s / node_dt)) + 1
    while tc < t_end - 1e-6 and any(tr.active for tr in trajs):
        L = min(chunk_s, t_end - tc)
        L = max(h, round(L / h) * h)
        nn = int(math.floor(L / node_dt + 1e-9)) + 1
        if (nn - 1) * node_dt < L - 1e-6:
            nn += 1
        stats["chunks"] += 1
        pending = [tr for tr in trajs if tr.active]
        # 初始密度剖面：上一 chunk 的剖面；第一個 chunk 用起點密度常數
        need = [tr for tr in pending if tr.B > 0]
        if need and any(tr.prof is None for tr in need):
            first = [tr for tr in need if tr.prof is None]
            pts = [_nodes_llh([(tc, tr.s[0], tr.s[1], tr.s[2])]) for tr in first]
            rho = _density(np.concatenate([p[0] for p in pts]), np.concatenate([p[1] for p in pts]),
                           np.concatenate([p[2] for p in pts]), np.concatenate([p[3] for p in pts]),
                           sw, cutoff_unix)
            stats["msis_points"] += len(first); stats["msis_calls"] += 1
            for tr, rr in zip(first, rho):
                tr.prof = [math.log(rr)] * nnode
        results = {}
        for tr in pending:
            prof = None
            if tr.B > 0:
                # 上一圈剖面 + 平均 ln ρ 趨勢（高度下降使密度逐圈升高）作為預測
                prof = [v + tr.trend for v in (tr.prof + [tr.prof[-1]] * nn)[:nn]]
            ws = list(tr.wsum); wc = list(tr.wcnt)
            res = _integrate_chunk(tr.s, tr.B, tc, L, h, node_dt, prof, stop_alt, windows, ws, wc,
                                   record_nodes=tr.B > 0)
            results[id(tr)] = (res, prof, ws, wc)
        # 密度迭代
        for it in range(max_iter):
            todo = [tr for tr in pending if tr.B > 0]
            if not todo:
                break
            llh = []
            for tr in todo:
                nodes = results[id(tr)][0][1]
                llh.append(_nodes_llh(nodes))
            rho = _density(np.concatenate([p[0] for p in llh]), np.concatenate([p[1] for p in llh]),
                           np.concatenate([p[2] for p in llh]), np.concatenate([p[3] for p in llh]),
                           sw, cutoff_unix)
            stats["msis_points"] += rho.size; stats["msis_calls"] += 1
            off = 0
            redo = []
            for tr, p in zip(todo, llh):
                m = p[0].size
                lr = np.log(rho[off:off + m]); off += m
                prof_used = results[id(tr)][1]
                used = np.asarray(prof_used[:m])
                new = list(lr) + [float(lr[-1])] * (nn - m)
                dif = np.abs(lr - used)
                if (dif.mean() > tol_mean or dif.max() > tol_max) and it < max_iter - 1:
                    redo.append((tr, new))
                else:
                    if tr.prof is not None and len(tr.prof) >= m and m == nn:
                        tr.trend = float(np.clip(np.mean(lr) - np.mean(tr.prof[:m]), -0.5, 0.5))
                    tr.prof = new
            if not redo:
                break
            for tr, new in redo:
                ws = list(tr.wsum); wc = list(tr.wcnt)
                res = _integrate_chunk(tr.s, tr.B, tc, L, h, node_dt, new, stop_alt, windows, ws, wc, True)
                results[id(tr)] = (res, new, ws, wc)
                stats["reintegrations"] += 1
            pending = [tr for tr, _ in redo]
        for tr in [t for t in trajs if t.active]:
            (state, _nodes, t_cross, min_alt, _k), _p, ws, wc = results[id(tr)]
            tr.s = state; tr.wsum = ws; tr.wcnt = wc
            tr.min_alt = min(tr.min_alt, min_alt)
            if t_cross is not None:
                tr.active = False; tr.t_cross = t_cross
        tc += L
    for tr in trajs:
        tr.t = tc
    return trajs, stats


# ════════════════════════════ 分段校準 ════════════════════════════
def select_anchors(tl: list[TLE], n_segments: int, min_gap_h: float = 12.0, da_km: float = 10.0,
                   min_gap_da_h: float = 1.0) -> list[int]:
    """由最新一筆往回挑錨點索引（回傳由舊到新）。"""
    if not tl:
        return []
    anchors = [len(tl) - 1]
    cur = len(tl) - 1
    while len(anchors) <= n_segments:
        tcur, acur = tl[cur].t, tl[cur].a_kozai
        j_time = j_da = -1
        for j in range(cur - 1, -1, -1):
            dt = tcur - tl[j].t
            if j_da < 0 and dt >= min_gap_da_h * 3600 and tl[j].a_kozai - acur >= da_km:
                j_da = j
            if dt >= min_gap_h * 3600:
                j_time = j
                break
        j = max(j_time, j_da)
        if j < 0:
            break
        anchors.append(j)
        cur = j
    return anchors[::-1]


_SEG_CACHE: dict[tuple, dict] = {}


def calibrate_segment(t1: TLE, t2: TLE, sw: SpaceWeather, B_guess: float = 0.03, h: float = 15.0,
                      tol_m: float = 10.0, cutoff_unix: float | None = None, verify: bool = True) -> dict:
    """單段校準（結果以 (TLE 對, 太空天氣截止日, h, verify) 為鍵快取，重複呼叫免重算）。"""
    cap = None
    if cutoff_unix is not None and sw.day0 is not None:
        cap = int(math.floor((cutoff_unix - sw.day0) / 86400.0)) - 1
        # 段落結束（含平均窗）早於截止日者，太空天氣與截止日無關 → 共用快取
        if sw.day0 + (cap + 1) * 86400.0 > t2.t + t1.period + 86400.0:
            cap = None
    key = (t1.line1, t1.line2, t2.line1, t2.line2, cap, h, verify)
    if key in _SEG_CACHE:
        return dict(_SEG_CACHE[key])
    out = _calibrate_segment(t1, t2, sw, B_guess, h, tol_m, cutoff_unix, verify)
    _SEG_CACHE[key] = dict(out)
    return out


def _calibrate_segment(t1: TLE, t2: TLE, sw: SpaceWeather, B_guess: float, h: float,
                       tol_m: float, cutoff_unix: float | None, verify: bool) -> dict:
    P = t1.period
    a1 = _sgp4_mean_a(t1, t1.t, P)
    a2 = _sgp4_mean_a(t2, t2.t, P)
    da_obs = a2 - a1
    s0 = _sgp4_state(t1, t1.t)
    win = [(t1.t, t1.t + P), (t2.t, t2.t + P)]
    t_end = t2.t + P + h
    out = {"t_start": _iso(t1.t), "t_end": _iso(t2.t), "dt_h": round((t2.t - t1.t) / 3600, 2),
           "a_start_km": round(a1, 3), "da_obs_km": round(da_obs, 4)}

    def run(Bs):
        trajs, st = propagate([s0] * len(Bs), Bs, t1.t, t_end, sw, cutoff_unix, stop_alt=60.0,
                              windows=win, h=h, chunk_s=P)
        vals = []
        for tr in trajs:
            if tr.t_cross is not None or min(tr.wcnt) == 0:
                vals.append(None)
            else:
                vals.append(tr.wsum[1] / tr.wcnt[1] - tr.wsum[0] / tr.wcnt[0])
        return vals, st

    Bg = max(B_guess, 1e-4)
    for _ in range(6):
        (d0, dg), st = run([0.0, Bg])
        if dg is not None:
            break
        Bg *= 0.4
    else:
        out.update(B=None, status="calib_failed")
        return out
    if abs(dg - d0) < 1e-9:
        out.update(B=None, status="no_drag_signal")
        return out
    B = Bg * (da_obs - d0) / (dg - d0)
    resid = None
    if B > 0 and verify:
        (dv,), _ = run([B])
        if dv is not None:
            resid = dv - da_obs
            if abs(resid) * 1000 > tol_m:
                # 割線修正（用 (Bg,dg) 與 (B,dv)）
                if abs(dv - dg) > 1e-12:
                    B2 = B + (da_obs - dv) * (B - Bg) / (dv - dg)
                    if B2 > 0:
                        (dv2,), _ = run([B2])
                        if dv2 is not None and abs(dv2 - da_obs) < abs(resid):
                            B, resid = B2, dv2 - da_obs
    out.update(B=B, resid_m=None if resid is None else round(resid * 1000, 1),
               da_nodrag_km=round(d0, 4), status="ok")
    return out


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def calibrate_segments(tl: list[TLE], n_segments: int, sw: SpaceWeather, cutoff_unix: float | None,
                       h: float = 15.0, jump_ratio: float = 2.0, raise_km: float = 0.3,
                       mass_kg: float = 800.0, cd: float = 2.2, verify: bool = True) -> list[dict]:
    anchors = select_anchors(tl, n_segments)
    segs = []
    Bprev = 0.03
    for i0, i1 in zip(anchors[:-1], anchors[1:]):
        seg = calibrate_segment(tl[i0], tl[i1], sw, B_guess=Bprev, h=h, cutoff_unix=cutoff_unix,
                                verify=verify)
        B = seg.get("B")
        seg["A_eff_m2"] = None if B is None else round(B * mass_kg / cd, 2)
        if B is not None:
            seg["B"] = round(B, 6)
        flag = None
        if seg["da_obs_km"] > raise_km or (B is not None and B <= 0):
            flag = "raise"            # 半長軸上升或 B≤0：抬升點火（或目錄雜訊）
        else:
            ref = [s["B"] for s in segs if s.get("B") and not s.get("thrust_flag")][-4:]
            if B is not None and len(ref) >= 1 and B > jump_ratio * float(np.median(ref)):
                flag = "jump"         # 面積跳升：降軌點火（Remis 34110 型）
        seg["thrust_flag"] = flag
        if B is not None and B > 0:
            Bprev = B
        segs.append(seg)
    # 事後再檢查：若某段被標 jump，但之後各段都維持同水準（≥2 段），可能是姿態改變（高阻力姿態）而非推力
    for k, s in enumerate(segs):
        if s["thrust_flag"] == "jump":
            later = [x["B"] for x in segs[k + 1:] if x.get("B")]
            if len(later) >= 2 and all(abs(b / s["B"] - 1) < 0.35 for b in later):
                s["thrust_flag"] = "jump_persistent"   # 持續偏高：姿態/構型改變或長時間推進，存疑
    return segs


# ════════════════════════════ 主介面 ════════════════════════════
def predict_reentry(tles: Iterable[Any], cutoff: datetime | float | None = None, n_segments: int = 6,
                    max_days: float = 10.0, stop_alt_km: float = 80.0, h: float = 15.0,
                    spread_floor: float = 0.15, sw_path: str | None = None,
                    mass_kg: float = 800.0, cd: float = 2.2, verify: bool = True,
                    exact_window: bool = False) -> dict[str, Any]:
    """分段 M/A 校準 + 前向數值積分之再入預測。

    參數
    - tles：該 NORAD 的 TLE 序列（任意順序；(line1,line2) 或 dict）。
    - cutoff：預測發布時刻（UTC）；只使用 epoch ≤ cutoff 的 TLE，且太空天氣於 cutoff 前一日之後持平。
      None ＝ 用全部 TLE、太空天氣用到最後觀測日。
    - n_segments：使用最近幾段做校準（越多越慢；預設 6 ≈ 最近 3–6 天）。
    - max_days：前向積分上限；超過仍未再入則回傳 reentry_utc=None 與 "beyond_horizon"。
    - h：RK4 步長 [s]；預設 15 s（30 s 約快 2 倍、誤差略增）。
    - mass_kg / cd：只用來把 B=Cd·A/m 換成等效面積 A_eff 顯示（預測只取決於 B）。

    回傳 dict：segments（逐段 B、A_eff、殘差、thrust_flag）、B_used（名目/低/高）、
    reentry_utc / window_early_utc / window_late_utc、hours_from_last_tle、reliable（最新段是否無推力疑似）、
    thrust_suspected（是否有任一段被標記）、space_weather（來源、持平起始日）、timing。
    """
    tstart = time.perf_counter()
    sw = SpaceWeather.load(sw_path)
    tl = parse_tles(tles)
    cut = None
    if cutoff is not None:
        cut = cutoff.timestamp() if isinstance(cutoff, datetime) else float(cutoff)
        tl = [x for x in tl if x.t <= cut]
    if len(tl) < 2:
        return {"error": "可用 TLE 少於 2 筆"}
    segs = calibrate_segments(tl, n_segments, sw, cut, h=h, mass_kg=mass_kg, cd=cd, verify=verify)
    t_cal = time.perf_counter() - tstart
    good = [s for s in segs if s.get("B") and s["B"] > 0]
    if not good:
        return {"error": "無有效校準段", "segments": segs}
    last = segs[-1]
    reliable = last.get("thrust_flag") is None and last.get("B") is not None and last["B"] > 0
    # 最近穩定段：最後一次旗標之後的段
    k_last_flag = max([i for i, s in enumerate(segs) if s.get("thrust_flag")], default=-1)
    stable = [s for s in segs[k_last_flag + 1:] if s.get("B") and s["B"] > 0]
    if stable:
        B_nom = stable[-1]["B"]
        rec = [s["B"] for s in stable[-3:]]
    else:
        B_nom = good[-1]["B"]
        rec = [B_nom]
    B_lo = min(min(rec), B_nom * (1 - spread_floor))
    B_hi = max(max(rec), B_nom * (1 + spread_floor))
    t0 = tl[-1]
    s0 = _sgp4_state(t0, t0.t)
    if exact_window:
        trajs, st = propagate([s0, s0, s0], [B_nom, B_hi, B_lo], t0.t, t0.t + max_days * 86400.0, sw, cut,
                              stop_alt=stop_alt_km, h=h, chunk_s=t0.period)
        tn, th, tlo = (tr.t_cross for tr in trajs)
    else:
        # 剩餘壽命 ∝ 1/B（King-Hele；太空天氣持平時近乎精確）→ 只積分名目軌跡，區間用比例換算
        trajs, st = propagate([s0], [B_nom], t0.t, t0.t + max_days * 86400.0, sw, cut,
                              stop_alt=stop_alt_km, h=h, chunk_s=t0.period)
        tn = trajs[0].t_cross
        th = None if tn is None else t0.t + (tn - t0.t) * B_nom / B_hi
        tlo = None if tn is None else t0.t + (tn - t0.t) * B_nom / B_lo
    elapsed = time.perf_counter() - tstart
    sw_hold = None
    if sw.day0 is not None:
        cap_day = sw.last_obs_day
        if cut is not None:
            cap_day = min(cap_day, int(math.floor((cut - sw.day0) / 86400.0)) - 1)
        sw_hold = _iso(sw.day0 + cap_day * 86400.0)[:10]
    return {
        "method": "segmented_ma_v1",
        "tle_last_epoch": _iso(t0.t),
        "cutoff": None if cut is None else _iso(cut),
        "n_tles_used": len(tl),
        "segments": segs,
        "B_used": {"nominal": round(B_nom, 6), "low": round(B_lo, 6), "high": round(B_hi, 6)},
        "A_eff_used_m2": round(B_nom * mass_kg / cd, 2),
        "reentry_utc": None if tn is None else _iso(tn),
        "window_early_utc": None if th is None else _iso(th),
        "window_late_utc": None if tlo is None else _iso(tlo),
        "beyond_horizon": tn is None,
        "hours_from_last_tle": None if tn is None else round((tn - t0.t) / 3600, 2),
        "reentry_unix": tn, "early_unix": th, "late_unix": tlo,
        "reliable": bool(reliable),
        "thrust_suspected": any(s.get("thrust_flag") for s in segs),
        "latest_segment_flag": last.get("thrust_flag"),
        "stop_alt_km": stop_alt_km,
        "space_weather": {"source": sw.path, "persisted_from": sw_hold,
                          "note": "截止日之後 F10.7 / Ap 以最後已知日值持平（persistence）"},
        "force_model": ("two-body + J2/J3/J4 + " + ("NRLMSISE-00" if MSIS_VERSION == 0 else f"NRLMSIS {MSIS_VERSION}")
                        + " drag (co-rotating atmosphere); RK4 fixed step"),
        "timing_s": {"calibration": round(t_cal, 2), "total": round(elapsed, 2)},
        "stats": st,
    }
