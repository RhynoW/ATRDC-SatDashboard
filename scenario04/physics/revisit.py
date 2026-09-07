"""星系對單一地面點的重訪 / 覆蓋分析（可設定觀測仰角門檻）。

用途：回答「在台灣所屬緯度，OneWeb 這類低軌星系多久過頂一次？仰角門檻拉高之後
覆蓋會斷多久？」——這是 §災時備援連線 的量化版本。

設計重點
--------
* **一次傳播、多門檻**：SGP4 只跑一次得到 (n_sats, n_steps) 仰角矩陣，之後每個仰角
  門檻只是布林運算。前端滑桿因此可以即時切換門檻而不必重新傳播。
* **三種「重訪」定義都給**，因為它們的工程意義完全不同，混為一談會誤導：
    - 星系級過頂間隔：任一顆衛星連續兩次過頂「開始」之間的時間，代表換手頻率。
      654 顆的星系此值會小到數十秒，它不是一般語境下的「重訪週期」。
    - 單星重訪週期：**同一顆**衛星再次過頂同一地點的間隔，才是傳統遙測語境的
      revisit period；以各衛星自身間隔的中位數再取全星系中位數。
    - 無覆蓋空窗（coverage gap）：完全沒有任何一顆衛星在門檻之上的時間，
      對通訊備援而言這才是「服務中斷」。
* **邊界截斷據實標示**：時窗頭尾的空窗長度被觀測窗切斷，不納入統計，另行回報，
  避免把「其實更長的空窗」誤報成短空窗。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import numpy as np
from sgp4.api import Satrec, jday

from .coords import observer_ecef

logger = logging.getLogger(__name__)

try:
    from sgp4.api import SatrecArray as _SatrecArray
    HAS_SATREC_ARRAY = True
except ImportError:      # pragma: no cover - 僅極舊版 sgp4
    _SatrecArray = None
    HAS_SATREC_ARRAY = False

DEFAULT_MASKS: tuple[float, ...] = (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60)
TIMELINE_BINS = 360          # 覆蓋時間帶的取樣格數（前端畫圖用）
MAX_MATRIX_MB = 512          # 傳播矩陣記憶體上限；超過即等比縮減衛星數


def _runs(mask_bool: np.ndarray) -> list[tuple[int, int]]:
    """回傳布林陣列中所有 True 連續區段的 (起始索引, 結束索引)（含端點）。"""
    if not mask_bool.any():
        return []
    d = np.diff(mask_bool.astype(np.int8))
    starts = list(np.where(d == 1)[0] + 1)
    ends = list(np.where(d == -1)[0])
    if mask_bool[0]:
        starts = [0] + starts
    if mask_bool[-1]:
        ends = ends + [len(mask_bool) - 1]
    return list(zip(starts, ends))


def compute_revisit(
    nids: Sequence[int],
    idx: dict,
    lat_deg: float,
    lon_deg: float,
    h_km: float = 0.01,
    hours: float = 24.0,
    step_sec: float = 30.0,
    masks: Sequence[float] = DEFAULT_MASKS,
    t0: datetime | None = None,
) -> dict[str, Any]:
    """計算星系對指定地面點的重訪統計（逐仰角門檻）。

    Returns dict：station / window / by_mask{mask: 統計}；由 API 直接序列化。
    """
    if not HAS_SATREC_ARRAY:
        return {"error": "sgp4 SatrecArray 不可用，無法批次傳播"}

    t0 = t0 or datetime.now(timezone.utc)
    n_steps = max(2, int(hours * 3600 / step_sec))

    usable = [n for n in nids if idx.get(n, {}).get("line1")]
    if not usable:
        return {"error": "此群組無可用 TLE"}

    # 記憶體保護：矩陣過大時等比縮減衛星數（並據實回報縮減後的實際顆數）
    est_mb = len(usable) * n_steps * 3 * 8 / 1024 ** 2
    if est_mb > MAX_MATRIX_MB:
        keep = max(1, int(MAX_MATRIX_MB * 1024 ** 2 / (n_steps * 3 * 8)))
        logger.warning("重訪分析矩陣估計 %.0f MB > %d MB 上限，衛星數 %d → %d",
                       est_mb, MAX_MATRIX_MB, len(usable), keep)
        usable = usable[:keep]

    times = [t0 + timedelta(seconds=i * step_sec) for i in range(n_steps)]
    jd_fr = np.array([
        jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond * 1e-6)
        for t in times
    ])
    jds = np.ascontiguousarray(jd_fr[:, 0])
    frs = np.ascontiguousarray(jd_fr[:, 1])

    # GMST（與 coverage.py 同一套近似：以 UTC 近似 UT1，未納極移/章動）
    t_cent = ((jds - 2451545.0) + frs) / 36525.0
    gmst = np.deg2rad((280.46061837 + 360.98564736629 * (jds - 2451545.0 + frs)
                       + 0.000387933 * t_cent ** 2) % 360.0)
    cg, sg = np.cos(gmst), np.sin(gmst)

    obs = observer_ecef(lat_deg, lon_deg, h_km)
    sl, cl, so, co = obs["sl"], obs["cl"], obs["so"], obs["co"]
    x0, y0, z0 = obs["x0"], obs["y0"], obs["z0"]

    try:
        sa = _SatrecArray([Satrec.twoline2rv(idx[n]["line1"], idx[n]["line2"]) for n in usable])
        e_raw, r_raw, _ = sa.sgp4(jds, frs)
    except Exception as exc:  # noqa: BLE001 - 傳播失敗回錯誤而非拋出（fail-closed）
        logger.warning("重訪分析傳播失敗：%s", exc)
        return {"error": f"SGP4 批次傳播失敗：{exc}"}

    # ECI → ECEF → ENU → 仰角，全矩陣向量化 (n_sats, n_steps)
    xe = cg * r_raw[:, :, 0] + sg * r_raw[:, :, 1]
    ye = -sg * r_raw[:, :, 0] + cg * r_raw[:, :, 1]
    ze = r_raw[:, :, 2]
    dx, dy, dz = xe - x0, ye - y0, ze - z0
    up = cl * co * dx + cl * so * dy + sl * dz
    east = -so * dx + co * dy
    north = -sl * co * dx - sl * so * dy + cl * dz
    rng = np.sqrt(east ** 2 + north ** 2 + up ** 2)
    rng = np.where(rng > 1e-3, rng, 1e-3)
    el = np.rad2deg(np.arcsin(np.clip(up / rng, -1.0, 1.0)))
    el = np.where(e_raw == 0, el, -90.0)      # 傳播錯誤碼的時刻視為不可見

    n_ok = int((e_raw == 0).all(axis=1).sum())
    bin_edges = np.linspace(0, n_steps, TIMELINE_BINS + 1).astype(int)
    win_min = hours * 60.0

    by_mask: dict[str, Any] = {}
    for m in masks:
        above = el >= float(m)                       # (n_sats, n_steps)
        n_vis = above.sum(axis=0)                    # 同時可見顆數
        any_vis = n_vis > 0

        # 逐衛星過頂：每段連續可見即一次過頂
        pass_starts: list[int] = []
        pass_durs: list[float] = []
        pass_max_el: list[float] = []
        sat_revisit: list[float] = []     # 各衛星「自己再回來」的間隔中位數（分）
        sat_pass_counts: list[int] = []
        for si in range(above.shape[0]):
            runs_i = _runs(above[si])
            if not runs_i:
                continue
            sat_pass_counts.append(len(runs_i))
            own_starts = [a for a, _ in runs_i]
            if len(own_starts) >= 2:
                own_gaps = [(own_starts[k + 1] - own_starts[k]) * step_sec / 60.0
                            for k in range(len(own_starts) - 1)]
                sat_revisit.append(float(np.median(own_gaps)))
            for a, b in runs_i:
                pass_starts.append(a)
                pass_durs.append((b - a + 1) * step_sec / 60.0)
                pass_max_el.append(float(el[si, a:b + 1].max()))

        pass_starts.sort()
        n_passes = len(pass_starts)
        # 過頂間隔：連續兩次過頂開始的時間差
        intervals = [(pass_starts[i + 1] - pass_starts[i]) * step_sec / 60.0
                     for i in range(n_passes - 1)]

        # 無覆蓋空窗；頭尾被時窗切斷者不納入統計，另行回報
        gaps_all = _runs(~any_vis)
        gaps_inner, gap_edge_min = [], 0.0
        for a, b in gaps_all:
            dur = (b - a + 1) * step_sec / 60.0
            if a == 0 or b == n_steps - 1:
                gap_edge_min = max(gap_edge_min, dur)      # 被截斷，長度為下限
                continue
            gaps_inner.append({"start_utc": times[a].isoformat(), "minutes": round(dur, 1)})
        gap_mins = [g["minutes"] for g in gaps_inner]

        cov_pct = round(100.0 * float(any_vis.mean()), 2)
        # 覆蓋時間帶（可見顆數，取每格最大值）供前端畫條狀圖
        timeline = [int(n_vis[bin_edges[i]:max(bin_edges[i] + 1, bin_edges[i + 1])].max())
                    for i in range(TIMELINE_BINS)]

        by_mask[str(int(m))] = {
            "mask_deg": float(m),
            "n_passes": n_passes,
            "mean_revisit_min": round(win_min / n_passes, 2) if n_passes else None,
            # 單星重訪：同一顆衛星再次過頂的間隔（傳統 revisit period 定義）
            "sat_revisit_median_min": round(float(np.median(sat_revisit)), 1) if sat_revisit else None,
            "sat_revisit_min_min": round(min(sat_revisit), 1) if sat_revisit else None,
            "sat_passes_median": round(float(np.median(sat_pass_counts)), 1) if sat_pass_counts else None,
            "sats_multi_pass": len(sat_revisit),
            "median_interval_min": round(float(np.median(intervals)), 2) if intervals else None,
            "max_interval_min": round(max(intervals), 1) if intervals else None,
            "coverage_pct": cov_pct,
            "n_gaps": len(gaps_inner),
            "max_gap_min": round(max(gap_mins), 1) if gap_mins else 0.0,
            "median_gap_min": round(float(np.median(gap_mins)), 1) if gap_mins else 0.0,
            "gap_edge_censored_min": round(gap_edge_min, 1),
            "top_gaps": sorted(gaps_inner, key=lambda g: -g["minutes"])[:8],
            "sats_seen": int((above.any(axis=1)).sum()),
            "max_simultaneous": int(n_vis.max()),
            "mean_simultaneous": round(float(n_vis.mean()), 2),
            "median_pass_min": round(float(np.median(pass_durs)), 1) if pass_durs else None,
            "median_max_el_deg": round(float(np.median(pass_max_el)), 1) if pass_max_el else None,
            "timeline": timeline,
        }

    return {
        "station": {"lat": round(lat_deg, 4), "lon": round(lon_deg, 4), "h_km": h_km},
        "window": {
            "t0_utc": t0.isoformat(),
            "hours": hours,
            "step_sec": step_sec,
            "timeline_bins": TIMELINE_BINS,
        },
        "n_sats_requested": len(nids),
        "n_sats_propagated": len(usable),
        "n_sats_clean": n_ok,
        "masks": [float(m) for m in masks],
        "by_mask": by_mask,
    }
