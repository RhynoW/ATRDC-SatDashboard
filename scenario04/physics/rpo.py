"""RPO 場景資料生成（相對接近 3D + Chan Pc）。

以 raw_tle_archive 之歷史 TLE，逐時刻用「最近 elset」SGP4 傳播兩顆衛星至共同時鐘，輸出：
  - orbit：聚焦於最接近時刻（TCA）之細取樣（供 Cesium 3D 動畫）——兩者測地座標、range、Pc。
  - series：全重疊窗之粗取樣 range/Pc（供時間軸脈絡與 range 曲線）。
  - meta/summary：標題、事件時間軸（已知案例預設）、TCA、最小距離、Pc@TCA。

Pc 採 scenario04.physics.conjunction.compute_pc_chan（Chan 2008 各向同性首階近似），
為**排序代理（proxy）**、非作業級碰撞機率——與論文 §4.4 界定一致。
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone

import duckdb
import numpy as np
import pandas as pd
from sgp4.api import WGS72, Satrec, jday

# SGP4 epoch 基準：1949-12-31 00:00 UT
_EPOCH1949 = datetime(1949, 12, 31, 0, 0, 0, tzinfo=timezone.utc)

from ..config import settings
from ..ingestion.db import resolve_db
from .conjunction import compute_pc_chan
from .coords import eci_to_llh_batch

logger = logging.getLogger(__name__)

# 已知案例預設（標題／副題／事件時間軸）；未知 pair 自動產生通用標題。
_PRESETS: dict[tuple[int, int], dict] = {
    (58573, 59884): {
        "title": "神龍太空梭 — 在軌釋放與再接近（58573 × 59884）",
        "subtitle": (
            "中國可重複使用太空飛機 58573 於 2024-05-24 在約 605 km 釋放 59884，"
            "離散至約 1,700 km 後於 6 月再接近至約 1.4 km——逆轉自然沿軌漂移需要推力。"
            "碰撞機率 Pc 為 Chan 首階排序代理（proxy），非作業級數值。"
        ),
        "events": [
            {"t": "2024-05-24T16:30Z", "label": "DEPLOY"},
            {"t": "2024-06-14T10:13Z", "label": "TCA ~1.4 km"},
        ],
        "timeline": [
            {"d": "2024-05-24", "t": "<b>釋放</b>：於 16:30–22:50 UTC 窗、約 605 km 釋放次物體。"},
            {"d": "2024-05-25", "t": "<b>首筆 elset</b>：59884 於 15:13 UTC 編目（分離後約 9–16 h）。"},
            {"d": "05-25 ~ 06-05", "t": "<b>離散</b>：沿軌分離穩定增長至約 1,720 km。"},
            {"d": "06-06 ~ 06-08", "t": "<b>再接近</b>：3 天內 1,439 km → 5.9 km；逆轉漂移需推力。"},
            {"d": "06-08 ~ 06-18", "t": "<b>近接操作</b>：維持約 2–80 km 約 10 天；最接近約 1.41 km（06-14 10:13 UTC）。"},
        ],
    },
}

# 靜態歷史資料 → 模組級快取（key: (primary, secondary)）
_SCENE_CACHE: dict[tuple[int, int], dict] = {}


def _iso(t: datetime) -> str:
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _connect_ro(path, attempts: int = 3, delay: float = 0.6):
    """read-only 連線；archive 被 pipeline write 鎖時短暫重試。"""
    last = None
    for i in range(attempts):
        try:
            return duckdb.connect(str(path), read_only=True)
        except Exception as e:  # noqa: BLE001
            last = e
            if i < attempts - 1:
                time.sleep(delay)
    raise RuntimeError(
        f"資料庫暫時無法開啟（可能正被資料管線寫入鎖定）：{str(last)[:120]}")


def _table_cols(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=?",
        [table]).fetchall()}


def _satrec_from_elements(nid, epoch_dt: datetime, ecc, incl_deg, raan_deg,
                          argp_deg, ma_deg, mm_revday, bstar):
    """由平均軌道根數（無 TLE 字串之精簡 DB）以 sgp4init 建 Satrec。

    角度 deg→rad；mean_motion rev/day→rad/min；epoch 為 1949-12-31 起之日數。"""
    sat = Satrec()
    epoch_days = (epoch_dt - _EPOCH1949).total_seconds() / 86400.0
    d2r = math.pi / 180.0
    no_kozai = float(mm_revday) * 2.0 * math.pi / 1440.0
    sat.sgp4init(
        WGS72, "i", int(nid), epoch_days,
        float(bstar or 0.0), 0.0, 0.0,
        float(ecc), float(argp_deg) * d2r, float(incl_deg) * d2r,
        float(ma_deg) * d2r, no_kozai, float(raan_deg) * d2r,
    )
    return sat


def _load_recs(con: duckdb.DuckDBPyConnection, nid: int):
    """回傳 (recs, ep_ts, name, df) 或 (None, ...)。

    相容兩種 raw_tle_archive schema：
      (a) 含 line1/line2（完整 archive）→ Satrec.twoline2rv；
      (b) 僅存平均根數（精簡 DB，如 scenario-advanced01/DB/*slim*）→ sgp4init。
    """
    cols = _table_cols(con, "raw_tle_archive")
    has_tle = {"line1", "line2"} <= cols
    has_elem = {"eccentricity", "inclination_deg", "raan_deg", "argp_deg",
                "mean_anomaly_deg", "mean_motion"} <= cols
    if not has_tle and not has_elem:
        raise ValueError("raw_tle_archive 既無 line1/line2、亦無平均根數欄位，無法傳播")

    sel = ["epoch_utc"]
    if "object_name" in cols:
        sel.append("object_name")
    if has_tle:
        sel += ["line1", "line2"]
    if has_elem:
        sel += ["eccentricity", "inclination_deg", "raan_deg", "argp_deg",
                "mean_anomaly_deg", "mean_motion"]
        if "bstar" in cols:
            sel.append("bstar")

    df = con.execute(
        f"SELECT {', '.join(sel)} FROM raw_tle_archive WHERE norad_id=? ORDER BY epoch_utc",
        [int(nid)],
    ).fetchdf()
    if df.empty:
        return None, None, None, None
    df["epoch_utc"] = pd.to_datetime(df["epoch_utc"], utc=True)
    df = df.drop_duplicates("epoch_utc").reset_index(drop=True)

    name = None
    if "object_name" in cols:
        try:
            name = re.sub(r"^0\s+", "", str(df["object_name"].iloc[-1]).strip())
        except Exception:
            name = None

    recs, ep = [], []
    for _, r in df.iterrows():
        try:
            l1 = r["line1"] if has_tle else None
            l2 = r["line2"] if has_tle else None
            if isinstance(l1, str) and isinstance(l2, str):
                rec = Satrec.twoline2rv(l1, l2)
            elif has_elem:
                rec = _satrec_from_elements(
                    nid, r["epoch_utc"].to_pydatetime(),
                    r["eccentricity"], r["inclination_deg"], r["raan_deg"],
                    r["argp_deg"], r["mean_anomaly_deg"], r["mean_motion"],
                    r["bstar"] if "bstar" in cols else 0.0)
            else:
                continue
            recs.append(rec)
            ep.append(r["epoch_utc"].to_pydatetime().timestamp())
        except Exception:
            pass
    if not recs:
        return None, None, None, None
    return recs, np.array(ep), name or f"NORAD {int(nid)}", df


def _prop(recs, ep_ts, t: datetime):
    """以最接近 t 之 elset SGP4 傳播，回傳 ECI 位置 (km) 或 None。"""
    j = int(np.argmin(np.abs(ep_ts - t.timestamp())))
    jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                  t.second + t.microsecond / 1e6)
    e, r, _v = recs[j].sgp4(jd, fr)
    if e != 0:
        return None
    return np.array(r)


def _sample(Precs, Pep, Srecs, Sep, t0: datetime, t1: datetime, step_s: float):
    """在 [t0, t1] 以 step_s 秒取樣，回傳 list of (t, rp, rs, dist_km)。"""
    out = []
    n = int((t1 - t0).total_seconds() // step_s) + 1
    for k in range(n):
        t = t0 + timedelta(seconds=k * step_s)
        rp = _prop(Precs, Pep, t)
        rs = _prop(Srecs, Sep, t)
        if rp is None or rs is None:
            continue
        dist = float(np.linalg.norm(rs - rp))
        if not (np.isfinite(dist) and np.all(np.isfinite(rp)) and np.all(np.isfinite(rs))):
            continue   # sgp4/sgp4init 偶發 nan（元素異常）→ 略過該取樣點
        out.append((t, rp, rs, dist))
    return out


def _pick_focus_center(preset: dict, coarse: list, lo: datetime, hi: datetime) -> datetime:
    """挑選 3D 聚焦窗中心。優先採預設事件中標註 TCA 者（落於重疊窗內），
    否則採全窗最小距離之時刻。"""
    events = preset.get("events", []) if preset else []
    cand = None
    for ev in events:
        if "TCA" in str(ev.get("label", "")).upper():
            cand = ev.get("t")
            break
    if cand is None and events:
        cand = events[-1].get("t")
    if cand:
        try:
            t = datetime.fromisoformat(str(cand).replace("Z", "+00:00"))
            if lo <= t <= hi:
                return t
        except Exception:
            pass
    return coarse[int(np.argmin([c[3] for c in coarse]))][0]


def compute_rpo_scene(
    primary: int = 58573,
    secondary: int = 59884,
    focus_hours: float = 12.0,
    fine_step_s: float = 60.0,
    max_coarse_pts: int = 1500,
    db=None,
) -> dict:
    """計算兩顆衛星之相對接近 3D 場景資料（含 Chan Pc）。

    orbit：TCA ± focus_hours、每 fine_step_s 秒之細取樣（Cesium 動畫用）。
    series：全重疊窗之粗取樣 range/Pc（脈絡曲線；點數上限 max_coarse_pts）。
    """
    path = db or resolve_db()
    if path is None:
        raise RuntimeError("找不到資料庫（resolve_db 回傳 None）")

    con = _connect_ro(path)
    try:
        Precs, Pep, pname, Pdf = _load_recs(con, primary)
        Srecs, Sep, sname, Sdf = _load_recs(con, secondary)
    finally:
        con.close()

    if not Precs:
        raise ValueError(f"raw_tle_archive 無 NORAD {primary} 的 TLE（可先回補歷史 TLE）")
    if not Srecs:
        raise ValueError(f"raw_tle_archive 無 NORAD {secondary} 的 TLE（可先回補歷史 TLE）")

    lo = max(Pdf.epoch_utc.min(), Sdf.epoch_utc.min()).to_pydatetime()
    hi = min(Pdf.epoch_utc.max(), Sdf.epoch_utc.max()).to_pydatetime()
    if hi <= lo:
        raise ValueError(
            f"兩顆 TLE 無時間重疊（{primary}:{Pdf.epoch_utc.min()}~{Pdf.epoch_utc.max()} "
            f"vs {secondary}:{Sdf.epoch_utc.min()}~{Sdf.epoch_utc.max()}）"
        )

    # 粗掃全重疊窗 → 脈絡 series + 近似 TCA（步長自動放大以不超過點數上限）
    span_min = (hi - lo).total_seconds() / 60.0
    coarse_step_min = max(30.0, span_min / max_coarse_pts)
    coarse = _sample(Precs, Pep, Srecs, Sep, lo, hi, coarse_step_min * 60.0)
    if not coarse:
        raise ValueError("無有效傳播點（TLE 可能不足或 SGP4 失敗）")
    series = [{"t": _iso(t), "d": round(d, 3), "pc": compute_pc_chan(d)}
              for (t, _rp, _rs, d) in coarse]

    # 聚焦中心：優先採已知案例之標註 TCA 事件（如神龍 6/14 再接近 ~1.4 km，對應影片
    # 「約 1 公里量級」之機動再接近），而非釋放瞬間之近乎共位；未知案例退回全窗最小距離。
    preset = _PRESETS.get((int(primary), int(secondary)), {})
    focus_center = _pick_focus_center(preset, coarse, lo, hi)

    # 聚焦窗：中心 ± focus_hours、細掃 → 3D 動畫 orbit（兩者測地座標 + range + Pc）
    f0 = max(lo, focus_center - timedelta(hours=focus_hours))
    f1 = min(hi, focus_center + timedelta(hours=focus_hours))
    fine = _sample(Precs, Pep, Srecs, Sep, f0, f1, fine_step_s) or coarse
    orbit = []
    for (t, rp, rs, d) in fine:
        llh = eci_to_llh_batch(np.vstack([rp, rs]), t)  # 每列 [lat, lon, alt_km]
        orbit.append({
            "t": _iso(t),
            # Cesium 用 [lon, lat, alt_m]
            "p": [round(float(llh[0, 1]), 4), round(float(llh[0, 0]), 4), round(float(llh[0, 2]) * 1000.0, 1)],
            "s": [round(float(llh[1, 1]), 4), round(float(llh[1, 0]), 4), round(float(llh[1, 2]) * 1000.0, 1)],
            "d": round(d, 4),
            "pc": compute_pc_chan(d),
        })

    imin = int(np.argmin([o["d"] for o in orbit]))
    # 全窗最小（可能為釋放瞬間之近乎共位）另記於 summary，供對照
    g_i = int(np.argmin([c[3] for c in coarse]))
    g_d, g_t = round(coarse[g_i][3], 3), _iso(coarse[g_i][0])
    meta = {
        "primId": int(primary), "secId": int(secondary),
        "primName": pname or f"NORAD {primary}", "secName": sname or f"NORAD {secondary}",
        "title": preset.get("title", f"相對接近 3D — {pname} × {sname}"),
        "subtitle": preset.get(
            "subtitle",
            f"{primary} 與 {secondary} 之 SGP4 相對幾何重建與 Pc（Chan 首階排序代理，非作業級）。"),
        "events": preset.get("events", [{"t": orbit[imin]["t"], "label": "TCA"}]),
        "timeline": preset.get("timeline", []),
        "focus": [_iso(f0), _iso(f1)],
        "overlap": [_iso(lo), _iso(hi)],
        "sigma_r": settings.SIGMA_R_KM, "sigma_t": settings.SIGMA_T_KM,
        "sigma_n": settings.SIGMA_N_KM,
        "sat_radius": settings.SAT_RADIUS_KM,
    }
    summary = {
        "d_min": orbit[imin]["d"], "t_min": orbit[imin]["t"], "pc_max": orbit[imin]["pc"],
        "d_min_global": g_d, "t_min_global": g_t,
        "n_orbit": len(orbit), "fine_step_s": fine_step_s,
        "n_series": len(series), "coarse_step_min": round(coarse_step_min, 1),
    }
    logger.info("RPO 場景 %s×%s：TCA %s d_min=%.3f km Pc(proxy)=%.2e（orbit %d 點、series %d 點）",
                primary, secondary, summary["t_min"], summary["d_min"], summary["pc_max"],
                summary["n_orbit"], summary["n_series"])
    return {"orbit": orbit, "series": series, "meta": meta, "summary": summary}


def _cache_file(primary: int, secondary: int):
    return settings.DB_DIR / f"rpo_scene_{int(primary)}_{int(secondary)}.json"


def get_rpo_scene(primary: int = 58573, secondary: int = 59884,
                  refresh: bool = False) -> dict:
    """帶記憶體＋檔案快取之場景取得。

    歷史 TLE 為靜態，且完整 archive（13 GB）可能被資料管線 write 鎖定；故一經算出即
    落地為 JSON 檔（scenario04/DB/rpo_scene_<p>_<s>.json），之後直接由檔案供應，
    使 3D 展示與 archive 鎖狀態解耦。refresh=True 時強制重算並覆寫。
    """
    key = (int(primary), int(secondary))
    if not refresh and key in _SCENE_CACHE:
        return _SCENE_CACHE[key]

    cf = _cache_file(primary, secondary)
    if not refresh and cf.exists():
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
            _SCENE_CACHE[key] = data
            return data
        except Exception:
            logger.warning("RPO 場景快取讀取失敗、改為重算：%s", cf)

    data = compute_rpo_scene(primary, secondary)
    _SCENE_CACHE[key] = data
    try:
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        logger.info("RPO 場景已快取至 %s", cf)
    except Exception:
        logger.warning("RPO 場景快取寫入失敗：%s", cf)
    return data
