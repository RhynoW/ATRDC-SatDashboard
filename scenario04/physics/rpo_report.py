"""RPO 靜態影像報告（五面板 PNG）。

  左   ：星座分布——同星座（名稱前綴）各星之 RAAN × 平均高度，主星圈選、次星星號，含放大插圖。
  中上 ：兩者平均高度歷史（逐 elset），自動標註次星高度跳變（機動）。
  中下 ：距離時間曲線（log 軸），**觀測段實線／預測段虛線**分開，並塗灰「低於公開 TLE 精度」帶。
  右   ：RIC 相對運動（主星為原點；R–I 與 C–I 兩視圖，類 TRIC），自動辨識 NMC 繞飛。

與 /rpo 3D 場景共用 physics.rpo 之 _load_recs（同一 raw_tle_archive、同一「最近 elset」規則），
但另以向量化 SGP4 細取樣（預設 30 s），並向後延伸彈道預測段——判讀「伴飛 vs 擦身而過」
必須看最後一筆 elset 之後兩者是否仍維持接近，而非僅看最小距離。

誠實原則（避免社群圖常見之過度宣稱）：
  - 最後共同 elset 之後一律標為「彈道預測（假設不再機動）」；
  - 次公里距離為 SGP4/TLE 幾何重建、低於公開 TLE 精度，不當作量測值；
  - 副標題之研判（NMC／伴飛／擦身／遠離／無法判定）由預測段自動產生，不人工撰寫；
  - 兩星平均高度差低於 TLE 可判精度時，不對「長期伴飛或漂離」下結論。
"""
from __future__ import annotations

import io
import math
import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sgp4.api import Satrec, jday

from ..ingestion.db import resolve_db
from .rpo import _connect_ro, _load_recs, preset_for

MU = 398600.4418          # km^3/s^2
RE = 6378.137             # km
J2 = 1.08262668e-3
TLE_FLOOR_KM = 1.0        # 公開 TLE 典型位置不確定度量級（沿軌可更大）
DECAY_ALT_KM = 400.0      # 星座面板：低於此高度歸為「低軌／衰減中」
HOLD_KM = 5.0             # 預測段全程維持在此距離內 → 研判伴飛
FLYBY_KM = 50.0           # 預測段最接近低於此值且其後拉開 → 研判掠過（文字附相對速度以區分 RPO 與高速交會）
JUMP_KM = 1.0             # 相鄰 elset 平均高度變化超過此值 → 標為機動
INDET_DALT_KM = 0.1       # 兩星最後 elset 平均高度差低於此 → 相對漂移在 TLE 精度內，不判長期走向
NMC_RATIO = (1.6, 2.4)    # NMC 判準：沿軌/徑向振幅比（CW 方程理論值 2）
NMC_MIN_R_KM = 0.02       # 徑向振幅下限（過小＝純沿軌偏置，非繞飛）
NMC_MAX_CENTER_KM = 50.0  # 橢圓中心須在此距離內：遠距（數千 km）之 RIC 振盪比值可巧合落入 1.6–2.4


# ───────────────────────── 數值工具 ─────────────────────────

def _mean_alt_km(mean_motion_revday) -> np.ndarray:
    n = np.asarray(mean_motion_revday, dtype=float) * 2.0 * math.pi / 86400.0
    return (MU / n ** 2) ** (1.0 / 3.0) - RE


def _mm_from_df(df: pd.DataFrame) -> np.ndarray:
    """平均運動 rev/day：優先用欄位，否則自 line2 第 53–63 欄解析。"""
    if "mean_motion" in df.columns and df["mean_motion"].notna().all():
        return df["mean_motion"].astype(float).to_numpy()
    return df["line2"].str.slice(52, 63).astype(float).to_numpy()


def _sample_vec(recs, ep_ts, times: pd.DatetimeIndex):
    """向量化「最近 elset」SGP4：回傳 (r, v)，形狀 (N,3)，失敗點為 nan。"""
    # 先統一為 ns 再取整數：pandas 3 起 datetime 預設解析度為 µs，直接 asi8/1e9 會小 1000 倍，
    # 使所有時刻都配到最早 elset（2026-10-18 HF Space 實際踩到：距離外推爆至上萬 km）
    ts = times.as_unit("ns").asi8 / 1e9
    idx = np.abs(ts[:, None] - ep_ts[None, :]).argmin(axis=1)
    jf = np.array([jday(t.year, t.month, t.day, t.hour, t.minute,
                        t.second + t.microsecond / 1e6) for t in times])
    jd, fr = np.ascontiguousarray(jf[:, 0]), np.ascontiguousarray(jf[:, 1])
    r = np.full((len(times), 3), np.nan)
    v = np.full((len(times), 3), np.nan)
    for j in np.unique(idx):
        m = idx == j
        e, rr, vv = recs[j].sgp4_array(np.ascontiguousarray(jd[m]), np.ascontiguousarray(fr[m]))
        ok = e == 0
        rr[~ok] = np.nan
        vv[~ok] = np.nan
        r[m], v[m] = rr, vv
    return r, v


def _raan_dot_deg_day(alt_km, incl_deg, ecc=0.0):
    a = RE + np.asarray(alt_km, dtype=float)
    n = np.sqrt(MU / a ** 3)
    rd = -1.5 * n * J2 * (RE / a) ** 2 * np.cos(np.radians(incl_deg)) / (1 - ecc ** 2) ** 2
    return np.degrees(rd) * 86400.0


def _flag_reversals(jumps: list[dict], within_days: float = 2.0, tol: float = 0.3) -> None:
    """相鄰兩跳變於 within_days 內正負抵銷（|和| < tol·|幅度|），且**中間高度僅 1 筆 elset 支持**
    （b.k − a.k ≤ 1）→ 多為單筆異常 elset（編目錯配／定軌離群），標 suspect，不當作機動。

    中間高度有 ≥2 筆一致 elset 者為真實相位調整：RPO 典型「先降軌追趕、再升軌同步」即呈正負抵銷
    （例：COSMOS 2612 於 10/15 0223Z −3.6 km、10/16 0450–1106Z 回升，經 JCO 證實）。"""
    for a, b in zip(jumps, jumps[1:]):
        if ((b["t"] - a["t"]).total_seconds() <= within_days * 86400
                and a["dalt_km"] * b["dalt_km"] < 0
                and abs(a["dalt_km"] + b["dalt_km"]) < tol * abs(a["dalt_km"])
                and b.get("k", 0) - a.get("k", 0) <= 1):
            a["suspect"] = b["suspect"] = True


def _ric(rp, vp, rs) -> np.ndarray:
    """secondary 相對 primary 之 RIC（R 徑向外、I 沿軌、C 軌道面法向），形狀 (N,3) km。"""
    R = rp / np.linalg.norm(rp, axis=1, keepdims=True)
    C = np.cross(rp, vp)
    C = C / np.linalg.norm(C, axis=1, keepdims=True)
    I = np.cross(C, R)
    d = rs - rp
    return np.stack([(d * R).sum(1), (d * I).sum(1), (d * C).sum(1)], axis=1)


def _nmc_metrics(times: pd.DatetimeIndex, ric: np.ndarray, t_start, period_min: float,
                 n_revs: int = 15) -> dict | None:
    """自 t_start 起逐圈量測相對橢圓：振幅（半峰峰值）、中心、沿軌中心漂移率。

    NMC 判準：I/R 振幅比落於 NMC_RATIO、徑向振幅 ≥ NMC_MIN_R_KM、首圈中心 ≤ NMC_MAX_CENTER_KM；
    每圈中心漂移 > 0.5 × 沿軌振幅 → drifting NMC。"""
    tm = (times - pd.Timestamp(t_start)).total_seconds().to_numpy() / 60.0
    m = tm >= 0
    if not m.any():
        return None
    rev = (tm[m] // period_min).astype(int)
    X = ric[m]
    rows = []
    for k in range(n_revs):
        sel = rev == k
        if sel.sum() < 10 or np.ptp(tm[m][sel]) < 0.9 * period_min:
            break
        # 每圈以「常數 + 線性漂移 + 軌道頻率正/餘弦」聯合最小平方擬合：振幅取諧波分量，
        # 漂移不會被算成振幅；單純對整圈去線性趨勢則會吃掉部分正弦分量而低估振幅。
        w = 2 * np.pi * tm[m][sel] / period_min
        A = np.stack([np.ones_like(w), w, np.cos(w), np.sin(w)], axis=1)
        coef = np.linalg.lstsq(A, X[sel], rcond=None)[0]          # (4, 3)：R/I/C 各一欄
        amp = np.hypot(coef[2], coef[3])
        rows.append((amp[0], amp[1], amp[2], X[sel, 1].mean(), X[sel, 0].mean()))
    if len(rows) < 2:
        return None
    a = np.array(rows)
    r_amp, i_amp, c_amp = (float(np.median(a[:, j])) for j in range(3))
    drift_rev = float(np.polyfit(np.arange(len(a)), a[:, 3], 1)[0])
    ratio = i_amp / r_amp if r_amp > 1e-6 else float("inf")
    return {
        "r_amp": r_amp, "i_amp": i_amp, "c_amp": c_amp, "ratio": ratio,
        "i_center": float(a[0, 3]), "r_center": float(a[0, 4]),
        "drift_rev_km": drift_rev, "drift_day_km": drift_rev * 1440.0 / period_min,
        "n_revs": len(a),
        "is_nmc": bool(NMC_RATIO[0] <= ratio <= NMC_RATIO[1] and r_amp >= NMC_MIN_R_KM
                       and abs(a[0, 3]) <= NMC_MAX_CENTER_KM),
        "drifting": bool(abs(drift_rev) > 0.5 * i_amp),
    }


def _constellation_prefix(name: str | None) -> str | None:
    """'ICEYE-X36' → 'ICEYE'；'STARLINK-1234' → 'STARLINK'；'COSMOS 2612' → 'COSMOS'。"""
    if not name:
        return None
    m = re.match(r"^\s*([A-Za-z][A-Za-z]+)", str(name))
    return m.group(1).upper() if m else None


def _catalog_points(catalog: dict, primary: int, ref_t: datetime, prefix: str | None):
    """自衛星索引取星座成員，RAAN 以 J2 進動推至共同時刻 ref_t（不同 epoch 才可比）。"""
    rows = []
    for nid, info in catalog.items():
        l1, l2 = info.get("line1"), info.get("line2")
        if not (isinstance(l1, str) and isinstance(l2, str)):
            continue
        try:
            s = Satrec.twoline2rv(l1, l2)
        except Exception:  # noqa: BLE001
            continue
        alt = float(_mean_alt_km(s.no_kozai * 1440.0 / (2 * math.pi)))
        incl = math.degrees(s.inclo)
        ep = datetime(2000 + s.epochyr if s.epochyr < 57 else 1900 + s.epochyr, 1, 1,
                      tzinfo=timezone.utc) + timedelta(days=s.epochdays - 1)
        dt_day = (ref_t - ep).total_seconds() / 86400.0
        raan = (math.degrees(s.nodeo) + _raan_dot_deg_day(alt, incl, s.ecco) * dt_day) % 360.0
        rows.append({"norad": int(nid), "name": str(info.get("name") or nid),
                     "alt": alt, "incl": incl, "raan": raan})
    df = pd.DataFrame(rows)
    if df.empty:
        return df, None
    p = df[df.norad == int(primary)]
    members = df[df.name.str.upper().str.startswith(prefix)] if prefix else df.iloc[0:0]
    label = prefix
    if len(members) < 3 and not p.empty:
        # 無可辨識星座 → 退回「同傾角、相近高度」鄰居
        pi, pa = float(p.incl.iloc[0]), float(p.alt.iloc[0])
        members = df[(df.incl.sub(pi).abs() < 0.3) & (df.alt.sub(pa).abs() < 100)]
        label = None
    return members.reset_index(drop=True), label


# ───────────────────────── 資料計算 ─────────────────────────

def compute_report_data(primary: int, secondary: int, *, db=None, catalog=None,
                        lookback_days: float = 45.0, forecast_days: float = 10.0,
                        step_s: float = 30.0, variant: str | None = None) -> dict:
    path = db or resolve_db()
    if path is None:
        raise RuntimeError("找不到資料庫（resolve_db 回傳 None）")
    preset = preset_for(primary, secondary, variant)
    excl = preset.get("exclude_epochs", {})
    con = _connect_ro(path)
    try:
        Precs, Pep, pname, Pdf = _load_recs(con, primary, preset.get("window"), excl.get(int(primary)))
        Srecs, Sep, sname, Sdf = _load_recs(con, secondary, preset.get("window"), excl.get(int(secondary)))
    finally:
        con.close()
    if not Precs:
        raise ValueError(f"raw_tle_archive 無 NORAD {primary} 的 TLE")
    if not Srecs:
        raise ValueError(f"raw_tle_archive 無 NORAD {secondary} 的 TLE")

    if catalog is None:
        try:
            from ..ingestion.index import get_sat_index
            catalog = get_sat_index()
        except Exception:  # noqa: BLE001
            catalog = {}
    if not pname or pname.startswith("NORAD "):
        pname = (catalog.get(int(primary)) or {}).get("name") or pname
    if not sname or sname.startswith("NORAD "):
        sname = (catalog.get(int(secondary)) or {}).get("name") or sname

    first = max(Pdf.epoch_utc.min(), Sdf.epoch_utc.min())
    obs_end = min(Pdf.epoch_utc.max(), Sdf.epoch_utc.max())   # 最後共同 elset：之後皆為預測
    t0 = max(first, obs_end - pd.Timedelta(days=lookback_days))
    t1 = obs_end + pd.Timedelta(days=forecast_days)
    if obs_end <= first:
        raise ValueError("兩顆 TLE 無時間重疊")

    times = pd.date_range(t0, t1, freq=f"{int(step_s)}s")
    rp, vp = _sample_vec(Precs, Pep, times)
    rs, vs = _sample_vec(Srecs, Sep, times)
    d = np.linalg.norm(rs - rp, axis=1)
    dv = np.linalg.norm(vs - vp, axis=1) * 1000.0
    ok = np.isfinite(d)
    times, d, dv = times[ok], d[ok], dv[ok]
    ric = _ric(rp[ok], vp[ok], rs[ok])
    is_obs = times <= obs_end

    def _argmin(mask):
        if not mask.any():
            return None
        k = np.flatnonzero(mask)[np.argmin(d[mask])]
        return {"t": times[k].to_pydatetime(), "d_km": float(d[k]), "dv_ms": float(dv[k])}

    tca_obs = _argmin(is_obs)
    tca_fc = _argmin(~is_obs)
    fc = d[~is_obs]
    fc_end = float(fc[-1]) if len(fc) else None
    fc_max = float(fc.max()) if len(fc) else None

    # 平均高度歷史（逐 elset）+ 次星高度跳變
    def _alt_hist(df):
        return pd.DataFrame({"t": df.epoch_utc.to_numpy(), "alt": _mean_alt_km(_mm_from_df(df))})
    ph, sh = _alt_hist(Pdf), _alt_hist(Sdf)
    jumps = []
    sd = sh.alt.diff().to_numpy()
    for k in np.flatnonzero(np.abs(np.nan_to_num(sd)) > JUMP_KM):
        jumps.append({"t": pd.Timestamp(sh.t.iloc[k]).to_pydatetime(), "dalt_km": float(sd[k]),
                      "suspect": False, "k": int(k)})
    _flag_reversals(jumps)

    # NMC：自最後共同 elset 起逐圈量測相對橢圓（週期取主星最後 elset）
    period_min = 1440.0 / float(_mm_from_df(Pdf)[-1])
    nmc = _nmc_metrics(times, ric, obs_end, period_min)
    # 兩星最後 elset 平均高度差：低於 INDET_DALT_KM → 相對漂移在 TLE 精度內，不判長期走向
    dalt_last = float(sh.alt.iloc[-1] - ph.alt.iloc[-1])
    indeterminate = abs(dalt_last) < INDET_DALT_KM and tca_obs is not None and tca_obs["d_km"] < 50.0
    if fc_max is None:
        verdict = "none"
    elif nmc and nmc["is_nmc"]:
        # 高度差低於 TLE 可判精度時，漂移率本身即不可信 → 不宣稱「漂移」，改於附註說明
        verdict = "nmc_drift" if nmc["drifting"] and not indeterminate else "nmc"
    elif fc_max <= HOLD_KM:
        verdict = "hold"           # 不再機動下仍全程維持接近
    elif indeterminate:
        verdict = "indeterminate"  # 高度差低於 TLE 可判精度：伴飛或漂離皆無法由 TLE 判定
    elif tca_fc and tca_fc["d_km"] < FLYBY_KM and fc_end > 2 * tca_fc["d_km"]:
        verdict = "flyby"          # 預測段有近接（含高速自然交會），其後拉開
    else:
        verdict = "separating"

    prefix = preset.get("constellation_prefix") or _constellation_prefix(pname)
    members, cons_label = _catalog_points(catalog or {}, primary, obs_end.to_pydatetime(), prefix)
    sec_pt = None
    if catalog and int(secondary) in catalog:
        sp, _ = _catalog_points({secondary: catalog[int(secondary)]}, secondary,
                                obs_end.to_pydatetime(), None)
        if not sp.empty:
            sec_pt = sp.iloc[0].to_dict()

    # 軌道面匹配度（最後共同 elset 時刻；RAAN 以 J2 推至同時刻）
    match = None
    if not members.empty and sec_pt is not None and (members.norad == int(primary)).any():
        pp = members[members.norad == int(primary)].iloc[0]
        dr = (sec_pt["raan"] - pp.raan + 180) % 360 - 180
        match = {"di": abs(sec_pt["incl"] - pp.incl), "draan": abs(dr), "dalt": sec_pt["alt"] - pp.alt}

    return {
        "primary": int(primary), "secondary": int(secondary),
        "pname": pname, "sname": sname,
        "times": times, "d": d, "dv": dv, "is_obs": is_obs, "ric": ric,
        "nmc": nmc, "dalt_last_km": dalt_last, "indeterminate": indeterminate,
        "obs_end": obs_end.to_pydatetime(), "t0": t0.to_pydatetime(), "t1": t1.to_pydatetime(),
        "tca_obs": tca_obs, "tca_fc": tca_fc, "fc_end_km": fc_end, "fc_max_km": fc_max,
        "verdict": verdict, "alt_p": ph, "alt_s": sh, "jumps": jumps,
        "members": members, "cons_label": cons_label, "sec_pt": sec_pt, "match": match,
        "forecast_days": forecast_days,
    }


# ───────────────────────── 繪圖 ─────────────────────────

# Debian fonts-noto-cjk 為 .ttc 合集，matplotlib 僅讀第 0 個 face（名為 "Noto Sans CJK JP"），
# 其字集涵蓋繁中所需漢字，故列為最後備援。
_CJK_FONTS = ["Microsoft JhengHei", "Noto Sans CJK TC", "Noto Sans TC", "PingFang TC",
              "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans CJK JP"]

_TXT = {
    "zh": {
        "verdict": {
            "hold": "若不再機動，預測期內仍維持 {fmax} 內——呈伴飛態勢",
            "flyby": "彈道預測：{ft} 以相對速度 {fdv} 掠過，最近約 {fmin}，其後拉開至 {fend}（若無後續機動）",
            "separating": "彈道預測：距離持續拉開至 {fend}（若無後續機動）",
            "indeterminate": "兩星平均高度差僅 {da:.2f} km，相對漂移低於公開 TLE 精度——伴飛或漂離無法判定，需更密集觀測",
            "nmc": "相對運動呈 NMC 繞飛：橢圓中心在 {p} {side} {ic:.1f} km，尺寸約 {ii:.1f}×{rr:.1f} km（沿軌×徑向，比值 {ratio:.1f}）",
            "nmc_drift": "相對運動呈漂移 NMC：橢圓 {ii:.1f}×{rr:.1f} km（比值 {ratio:.1f}），中心每日漂移約 {dd:.1f} km",
            "none": "",
        },
        "ahead": "前方", "behind": "後方", "sep_note": "；",
        "small": "橢圓尺寸低於 TLE 精度（~{f:.0f} km），形狀僅供參考",
        "drift_indet": "中心漂移約 {dd:.1f} km/日，但兩星高度差 {da:.2f} km 低於 TLE 可判精度——長期是否穩定無法判定",
        "drift_ok": "中心漂移約 {dd:.1f} km/日（若無後續機動）",
        "ric_ri": "相對運動 R–I（{p} 為原點，後續 24 h）",
        "ric_ci": "相對運動 C–I（橫向 × 沿軌）",
        "ric_i": "沿軌 I (km)", "ric_r": "徑向 R (km)", "ric_c": "橫向 C (km)",
        "ric_first": "第 1 圈", "ric_rest": "後續各圈",
        "ric_box": "I/R 振幅比 {ratio:.2f}\n（NMC 理論值 2）",
        "title": "{s} 接近 {p}",
        "sub_obs": "觀測期最接近 {dmin}（{t}，相對速度 {dv}）",
        "cons": "{c} 星座各星：軌道面（RAAN）× 平均高度",
        "cons_nb": "同傾角、相近高度之鄰近物體：RAAN × 平均高度",
        "op": "{c}（{n}）", "low": "高度 < {h:.0f} km（{n}，多為衰減中）",
        "alt": "平均高度歷史（逐 elset）", "alt_y": "平均高度 (km)",
        "sep": "與 {p} 之距離（SGP4）", "sep_y": "距離（km，log）",
        "obs": "觀測（TLE 期間內）", "fc": "彈道預測（假設不再機動）",
        "floor": "低於公開 TLE 精度（~{f:.0f} km）", "now": "最後共同 elset",
        "inset": "{p} 所在軌道面附近",
        "foot": "軌道根數：Space-Track TLE｜分析：SatDashboard© 自行以 SGP4 計算（TLE 級精度，次公里距離非量測值）",
        "match": "軌道面匹配 Δi {di:.2f}° · ΔRAAN {dr:.2f}° · Δ高度 {da:+.1f} km",
        "jump": "{v:+.0f} km",
        "suspect": "{v:+.0f} km?（疑異常 elset）",
    },
    "en": {
        "verdict": {
            "hold": "If no further maneuver, predicted to stay within {fmax} — consistent with co-location",
            "flyby": "Ballistic forecast: pass at {ft} with rel. speed {fdv}, closest ~{fmin}, then reopening to {fend} if no further maneuver",
            "separating": "Ballistic forecast: separation keeps growing to {fend} if no further maneuver",
            "indeterminate": "Mean-altitude difference only {da:.2f} km — relative drift is below public-TLE precision; co-location vs. drift-off cannot be determined",
            "nmc": "Relative motion is an NMC: ellipse centred {ic:.1f} km {side} {p}, about {ii:.1f}×{rr:.1f} km (in-track×radial, ratio {ratio:.1f})",
            "nmc_drift": "Relative motion is a drifting NMC: ellipse {ii:.1f}×{rr:.1f} km (ratio {ratio:.1f}), centre drifting ~{dd:.1f} km/day",
            "none": "",
        },
        "ahead": "ahead of", "behind": "behind", "sep_note": "; ",
        "small": "ellipse is below TLE precision (~{f:.0f} km), shape indicative only",
        "drift_indet": "Centre drift ~{dd:.1f} km/day, but the {da:.2f} km altitude difference is below TLE precision — long-term stability cannot be determined",
        "drift_ok": "Centre drift ~{dd:.1f} km/day (if no further maneuver)",
        "ric_ri": "R–I motion (origin: {p}, +24 h)",
        "ric_ci": "C–I relative motion (cross × in-track)",
        "ric_i": "In-track I (km)", "ric_r": "Radial R (km)", "ric_c": "Cross-track C (km)",
        "ric_first": "1st rev", "ric_rest": "later revs",
        "ric_box": "I/R amplitude ratio {ratio:.2f}\n(NMC theory: 2)",
        "title": "{s} closes in on {p}",
        "sub_obs": "Closest observed approach {dmin} ({t}, rel. speed {dv})",
        "cons": "Every catalogued {c} satellite, by orbital plane (RAAN) & altitude",
        "cons_nb": "Objects in a similar plane & altitude: RAAN × altitude",
        "op": "{c} ({n})", "low": "altitude < {h:.0f} km ({n}, mostly decaying)",
        "alt": "Mean altitude history (per elset)", "alt_y": "Mean altitude (km)",
        "sep": "Separation to {p} (SGP4)", "sep_y": "Separation (km, log)",
        "obs": "observed (within TLE span)", "fc": "ballistic forecast (assumes no maneuver)",
        "floor": "below public TLE precision (~{f:.0f} km)", "now": "last common elset",
        "inset": "around {p}'s plane",
        "foot": "Elements: Space-Track TLE | Analysis by SatDashboard© (SGP4; TLE-level accuracy, sub-km ranges are not measurements)",
        "match": "plane match Δi {di:.2f}° · ΔRAAN {dr:.2f}° · Δalt {da:+.1f} km",
        "jump": "{v:+.0f} km",
        "suspect": "{v:+.0f} km? (suspect elset)",
    },
}


def _fmt_km(x):
    if x is None:
        return "—"
    return f"{x * 1000:.0f} m" if x < 1 else f"{x:.1f} km"


def _pick_lang(lang: str) -> tuple[str, list[str]]:
    from matplotlib import font_manager as fm
    have = {f.name for f in fm.fontManager.ttflist}
    cjk = [f for f in _CJK_FONTS if f in have]
    if lang == "zh" and not cjk:
        return "en", ["DejaVu Sans"]     # 無中文字型（如 slim Docker）→ 改出英文，避免方框
    return lang, (cjk + ["DejaVu Sans"]) if lang == "zh" else ["DejaVu Sans"]


# 主題：light（白底）／dark（黑底）。顏色集中於此，繪圖處只引用鍵名。
_THEMES = {
    "light": {"bg": "#ffffff", "ink": "#0b1530", "muted": "#34425a", "grid": "#d9e0ea",
              "p": "#1f5fd6", "s": "#d62828", "low": "#9aa7bb", "ring": "#e08e00",
              "floor": "#e8ecf2", "fcband": "#fff0d6", "inset": "#f4f6fa", "edge": "#ffffff",
              "zoom": "#7d8796"},
    "dark":  {"bg": "#0a1020", "ink": "#f2f5fb", "muted": "#b9c4d6", "grid": "#25314a",
              "p": "#5c9dff", "s": "#ff5a5a", "low": "#6a7890", "ring": "#ffb321",
              "floor": "#18223a", "fcband": "#2b2312", "inset": "#111a2e", "edge": "#0a1020",
              "zoom": "#8592a8"},
}


def render_report_png(data: dict, lang: str = "zh", theme: str = "light", dpi: int = 150) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.ticker
    import matplotlib.pyplot as plt

    lang, fonts = _pick_lang(lang if lang in _TXT else "zh")
    T = _TXT[lang]
    K = _THEMES.get(theme, _THEMES["light"])
    C_P, C_S, C_INK, C_MUTED = K["p"], K["s"], K["ink"], K["muted"]

    with plt.rc_context({"font.family": fonts, "font.weight": "bold", "axes.unicode_minus": False,
                         "axes.labelweight": "bold", "axes.titleweight": "bold",
                         "figure.facecolor": K["bg"], "axes.facecolor": K["bg"],
                         "axes.edgecolor": K["grid"], "axes.linewidth": 1.6,
                         "axes.labelcolor": C_MUTED, "xtick.color": C_MUTED, "ytick.color": C_MUTED,
                         "xtick.labelsize": 14, "ytick.labelsize": 14,
                         "xtick.major.width": 1.4, "ytick.major.width": 1.4,
                         "axes.grid": True, "grid.color": K["grid"], "grid.linewidth": 1.1,
                         "legend.labelcolor": C_INK}):
        fig = plt.figure(figsize=(26, 12.5))
        gs = fig.add_gridspec(2, 3, width_ratios=[1.0, 0.8, 0.52], left=0.045, right=0.98,
                              top=0.72, bottom=0.09, wspace=0.17, hspace=0.42)
        axL = fig.add_subplot(gs[:, 0])
        axA = fig.add_subplot(gs[0, 1])
        axD = fig.add_subplot(gs[1, 1])
        axR = fig.add_subplot(gs[0, 2])
        axC = fig.add_subplot(gs[1, 2])
        p, s = data["pname"], data["sname"]

        # ── 標題／副標（研判自動產生；字級放大後分兩行：觀測一行、預測研判一行） ──
        to, tf = data["tca_obs"], data["tca_fc"]
        nm = data.get("nmc") or {}
        verdict = T["verdict"][data["verdict"]].format(
            fmax=_fmt_km(data["fc_max_km"]), fend=_fmt_km(data["fc_end_km"]),
            fmin=_fmt_km(tf["d_km"]) if tf else "—", ft=f"{tf['t']:%m-%d %H:%MZ}" if tf else "—",
            fdv=f"{tf['dv_ms']:.0f} m/s" if tf else "—",
            da=abs(data["dalt_last_km"]), p=p,
            side=T["ahead"] if nm.get("i_center", 0) > 0 else T["behind"],
            ic=abs(nm.get("i_center", 0)), ii=2 * nm.get("i_amp", 0), rr=2 * nm.get("r_amp", 0),
            ratio=nm.get("ratio", 0), dd=abs(nm.get("drift_day_km", 0)))
        note = ""
        if data["verdict"] == "nmc":
            note = (T["drift_indet"] if data["indeterminate"] else T["drift_ok"]).format(
                dd=abs(nm["drift_day_km"]), da=abs(data["dalt_last_km"]))
        if nm.get("is_nmc") and 2 * nm["i_amp"] < TLE_FLOOR_KM:
            note = (note + T["sep_note"] if note else "") + T["small"].format(f=TLE_FLOOR_KM)
        fig.text(0.045, 0.935, T["title"].format(s=s, p=p), fontsize=34, color=C_INK)
        if note:
            fig.text(0.045, 0.80, note, fontsize=16, color=C_MUTED)
        fig.text(0.045, 0.885, T["sub_obs"].format(
            dmin=_fmt_km(to["d_km"]) if to else "—",
            t=to["t"].strftime("%Y-%m-%d %H:%MZ") if to else "—",
            dv=f"{to['dv_ms']:.1f} m/s" if to else "—"), fontsize=18, color=C_MUTED)
        if verdict:
            fig.text(0.045, 0.842, verdict, fontsize=18, color=C_S)

        # ── 左：星座分布 ──
        mem = data["members"]
        label = data["cons_label"]
        axL.set_title(T["cons"].format(c=label) if label else T["cons_nb"],
                      loc="left", fontsize=18, color=C_INK, pad=12)
        if not mem.empty:
            low = mem[mem.alt < DECAY_ALT_KM]
            op = mem[mem.alt >= DECAY_ALT_KM]
            axL.scatter(op.raan, op.alt, s=110, c=C_P, edgecolors=K["edge"], linewidths=1.2, zorder=3,
                        label=T["op"].format(c=label or "", n=len(op)).strip())
            if len(low):
                axL.scatter(low.raan, low.alt, s=45, c=K["low"], zorder=2,
                            label=T["low"].format(h=DECAY_ALT_KM, n=len(low)))
            pr = mem[mem.norad == data["primary"]]
            if not pr.empty:
                axL.scatter(pr.raan, pr.alt, s=520, facecolors="none", edgecolors=K["ring"],
                            linewidths=4, zorder=4)
        sp = data["sec_pt"]
        if sp is not None:
            axL.scatter([sp["raan"]], [sp["alt"]], s=520, marker="*", c=C_S, zorder=5,
                        edgecolors=K["edge"], linewidths=1.0, label=s)
        axL.set_xlim(-10, 370)
        axL.set_xticks(range(0, 361, 60))
        axL.set_xlabel("RAAN (°)", fontsize=16)
        axL.set_ylabel(T["alt_y"], fontsize=16)
        axL.legend(loc="lower left", frameon=False, fontsize=15, labelspacing=1.1)

        # 放大插圖：主星附近 ±3° RAAN、±20 km
        if not mem.empty and (mem.norad == data["primary"]).any():
            pr = mem[mem.norad == data["primary"]].iloc[0]
            x0, y0 = pr.raan, pr.alt
            ins = axL.inset_axes([0.40, 0.40, 0.34, 0.31])
            near = mem[(mem.raan.sub(x0).abs() < 3) & (mem.alt.sub(y0).abs() < 20)]
            ins.scatter(near.raan, near.alt, s=170, c=C_P, edgecolors=K["edge"], linewidths=1.2, zorder=3)
            for _, r in near.iterrows():
                if int(r.norad) == data["primary"]:
                    continue   # 主星標籤另置於圈外，避免被星號遮住
                ins.annotate(re.sub(r"^[A-Za-z]+-?", "", r["name"]) or r["name"], (r.raan, r.alt),
                             xytext=(0, 12), textcoords="offset points", ha="center",
                             fontsize=13, color=C_INK)
            ins.annotate(re.sub(r"^[A-Za-z]+-?", "", p) or p, (x0, y0), xytext=(22, 14),
                         textcoords="offset points", fontsize=13, color=K["ring"])
            ins.scatter([x0], [y0], s=600, facecolors="none", edgecolors=K["ring"], linewidths=4, zorder=4)
            if sp is not None:
                ins.scatter([sp["raan"]], [sp["alt"]], s=520, marker="*", c=C_S, zorder=5)
            ins.set_xlim(x0 - 3, x0 + 3)
            ins.set_ylim(y0 - 20, y0 + 20)
            ins.set_facecolor(K["inset"])
            ins.tick_params(labelsize=11.5)
            ins.set_title(T["inset"].format(p=p), fontsize=14, color=C_INK, loc="left")
            axL.indicate_inset_zoom(ins, edgecolor=K["zoom"], linewidth=1.5)
            if data["match"]:
                m = data["match"]
                axL.text(0.40, 0.335, T["match"].format(di=m["di"], dr=m["draan"], da=m["dalt"]),
                         transform=axL.transAxes, fontsize=14, color=C_INK)

        # ── 右上：高度歷史 ──
        ap, as_ = data["alt_p"], data["alt_s"]
        axA.step(ap.t, ap.alt, where="post", color=C_P, lw=3.6, label=p)
        axA.step(as_.t, as_.alt, where="post", color=C_S, lw=4.0, label=s)
        for j in data["jumps"]:
            y = np.interp(mdates.date2num(j["t"]), mdates.date2num(as_.t), as_.alt)
            sus = j.get("suspect")
            axA.annotate(T["suspect" if sus else "jump"].format(v=j["dalt_km"]), (j["t"], y),
                         xytext=(0, 14 if j["dalt_km"] > 0 else -22), textcoords="offset points",
                         ha="center", fontsize=13 if sus else 16,
                         color=C_MUTED if sus else C_S)
        axA.axvline(data["obs_end"], color=C_MUTED, ls=":", lw=2.0)
        axA.set_title(T["alt"], loc="left", fontsize=18, color=C_INK, pad=12)
        axA.set_ylabel(T["alt_y"], fontsize=16)
        axA.legend(loc="best", frameon=False, fontsize=15)
        lo_a = min(ap.alt.min(), as_.alt.min()); hi_a = max(ap.alt.max(), as_.alt.max())
        pad = max(5.0, (hi_a - lo_a) * 0.2)
        axA.set_ylim(lo_a - pad, hi_a + pad * 0.6)
        axA.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))

        # ── 右下：距離（觀測實線／預測虛線、TLE 精度帶） ──
        t, d, ob = data["times"], data["d"], data["is_obs"]
        dmin_plot = max(min(d.min(), TLE_FLOOR_KM) * 0.5, 1e-3)
        axD.axhspan(dmin_plot * 0.1, TLE_FLOOR_KM, color=K["floor"], zorder=0)
        axD.text(0.99, 0.03, T["floor"].format(f=TLE_FLOOR_KM), transform=axD.transAxes,
                 ha="right", va="bottom", fontsize=13, color=C_MUTED, style="italic")
        axD.plot(t[ob], d[ob], color=C_P, lw=2.2, label=T["obs"])
        if (~ob).any():
            axD.plot(t[~ob], d[~ob], color=C_P, lw=2.2, ls=(0, (3, 2)), alpha=0.75, label=T["fc"])
            axD.axvspan(data["obs_end"], t[-1], color=K["fcband"], zorder=0)
        axD.axvline(data["obs_end"], color=C_MUTED, ls=":", lw=2.0, label=T["now"])   # 入圖例，免與曲線／圖例重疊
        y_hi = max(d.max() * 2, 10)
        if to:
            axD.scatter([to["t"]], [to["d_km"]], s=130, c=C_S, zorder=5)
            # 點位於 log 軸上段（>60%）時標註改往下擺，免撞面板標題
            frac = math.log(to["d_km"] / dmin_plot) / math.log(y_hi / dmin_plot)
            axD.annotate(f"{_fmt_km(to['d_km'])} · {to['dv_ms']:.1f} m/s\n{to['t']:%m-%d %H:%MZ}",
                         (to["t"], to["d_km"]), xytext=(-12, -70 if frac > 0.6 else 48),
                         textcoords="offset points",
                         ha="right", fontsize=15, color=C_S,
                         arrowprops={"arrowstyle": "-|>", "color": C_S, "lw": 2.2})
        axD.set_yscale("log")
        axD.set_ylim(dmin_plot, y_hi)
        axD.set_title(T["sep"].format(p=p), loc="left", fontsize=18, color=C_INK, pad=12)
        axD.set_ylabel(T["sep_y"], fontsize=16)
        axD.yaxis.set_major_locator(matplotlib.ticker.LogLocator(base=10))
        axD.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        axD.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        axD.legend(loc="upper right", frameon=True, facecolor=K["bg"], edgecolor="none",
                   framealpha=0.9, fontsize=13)
        axD.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))

        # ── 右：RIC 相對運動（R–I、C–I；最後共同 elset 起 24 h） ──
        tt, X = data["times"], data["ric"]
        w = (tt >= data["obs_end"]) & (tt <= pd.Timestamp(data["obs_end"]) + pd.Timedelta(hours=24))
        if not w.any():
            w = tt >= tt[-1] - pd.Timedelta(hours=24)
        Xw = X[w]
        step = (tt[1] - tt[0]).total_seconds() if len(tt) > 1 else 30.0
        per = int(round(95.0 * 60 / max(1.0, step)))   # 約一圈之取樣點數
        for ax, j, ylab, ttl in ((axR, 0, T["ric_r"], T["ric_ri"].format(p=p)),
                                 (axC, 2, T["ric_c"], T["ric_ci"])):
            ax.plot(Xw[:, 1], Xw[:, j], color=C_P, lw=1.4, alpha=0.55, label=T["ric_rest"])
            ax.plot(Xw[:per, 1], Xw[:per, j], color=C_S, lw=3.2, label=T["ric_first"])
            ax.scatter([0], [0], s=420, facecolors="none", edgecolors=K["ring"], linewidths=4, zorder=5)
            ax.scatter([0], [0], s=40, c=K["ring"], zorder=5)
            ax.annotate(re.sub(r"^[A-Za-z]+-?", "", p) or p, (0, 0), xytext=(14, 12),
                        textcoords="offset points", fontsize=13, color=K["ring"])
            ax.axhline(0, color=K["grid"], lw=1.6)
            ax.axvline(0, color=K["grid"], lw=1.6)
            ax.set_aspect("equal", adjustable="datalim")
            ax.set_title(ttl, loc="left", fontsize=15, color=C_INK, pad=12)
            ax.set_xlabel(T["ric_i"], fontsize=14)
            ax.set_ylabel(ylab, fontsize=14)
        axR.legend(loc="lower right", frameon=True, facecolor=K["bg"], edgecolor="none",
                   framealpha=0.85, fontsize=12)
        if nm:
            axR.text(0.03, 0.96, T["ric_box"].format(ratio=nm["ratio"]), transform=axR.transAxes,
                     va="top", fontsize=13, color=C_S if nm.get("is_nmc") else C_MUTED,
                     bbox={"facecolor": K["bg"], "edgecolor": "none", "alpha": 0.85})

        fig.text(0.045, 0.028, T["foot"], fontsize=12.5, color=C_MUTED)
        fig.text(0.98, 0.028, f"SatDashboard© · {datetime.now(timezone.utc):%Y-%m-%d}",
                 fontsize=15, color=C_INK, ha="right")

        if lang == "zh":
            # CJK 字型之粗體檔多為 .ttc（matplotlib 讀不到）→ 以同色描邊模擬加粗
            from matplotlib import patheffects as pe
            from matplotlib.text import Text
            for txt in fig.findobj(Text):
                if txt.get_text():
                    txt.set_path_effects([pe.withStroke(linewidth=0.5, foreground=txt.get_color()),
                                          pe.Normal()])

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, facecolor=K["bg"])
        plt.close(fig)
    return buf.getvalue()


def build_rpo_report(primary: int, secondary: int, *, lang: str = "zh", theme: str = "light",
                     **kw) -> bytes:
    return render_report_png(compute_report_data(primary, secondary, **kw), lang=lang, theme=theme)
