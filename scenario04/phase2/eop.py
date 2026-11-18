"""地球定向參數（EOP）：讀取 CelesTrak EOP-All.csv，於 MJD 線性內插。

欄位單位：X、Y、DX、DY 為角秒；UT1-UTC、LOD 為秒；DAT = TAI−UTC（秒）；DATA_TYPE O=觀測、P=預測。
UT1−UTC 在閏秒處跳 1 秒，故以 UT1−TAI（= UT1−UTC − DAT）內插後再加回當時的 DAT。
"""
from __future__ import annotations

import csv
import hashlib
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from .constants import ARCSEC_TO_RAD

TESTDATA = Path(__file__).resolve().parent / "testdata"
SNAPSHOT = TESTDATA / "eop_snapshot_20261118.csv"


@dataclass(frozen=True)
class EopValues:
    xp_rad: float
    yp_rad: float
    ut1_utc_s: float
    lod_s: float
    dx_rad: float
    dy_rad: float
    dat_s: float
    predicted: bool


class Eop:
    def __init__(self, path: Path):
        self.path = Path(path)
        raw = self.path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.mjd: list[float] = []
        self.rows: list[tuple] = []
        last_obs = None
        for r in csv.DictReader(raw.decode("utf-8").splitlines()):
            mjd = float(r["MJD"])
            dat = float(r["DAT"])
            self.mjd.append(mjd)
            self.rows.append((float(r["X"]), float(r["Y"]), float(r["UT1-UTC"]) - dat, float(r["LOD"]),
                              float(r["DX"]), float(r["DY"]), dat, r["DATA_TYPE"].strip() != "O"))
            if r["DATA_TYPE"].strip() == "O":
                last_obs = r["DATE"]
        if len(self.mjd) < 2:
            raise ValueError(f"EOP 檔資料不足：{path}")
        self.first_mjd, self.last_mjd = self.mjd[0], self.mjd[-1]
        self.last_observed = last_obs

    def version(self) -> dict:
        return {"file": self.path.name, "sha256": self.sha256[:16], "mjd_range": [self.first_mjd, self.last_mjd],
                "last_observed": self.last_observed}

    def at(self, mjd_utc: float) -> EopValues:
        if not (self.first_mjd <= mjd_utc <= self.last_mjd):
            raise ValueError(f"MJD {mjd_utc:.5f} 超出 EOP 範圍 {self.first_mjd}–{self.last_mjd}")
        i = min(max(bisect_right(self.mjd, mjd_utc) - 1, 0), len(self.mjd) - 2)
        m0, m1 = self.mjd[i], self.mjd[i + 1]
        f = (mjd_utc - m0) / (m1 - m0)
        a, b = self.rows[i], self.rows[i + 1]
        lin = [a[k] + f * (b[k] - a[k]) for k in range(6)]
        # DAT 取當日值（閏秒在 UTC 日界生效）
        dat = a[6] if f < 1.0 else b[6]
        return EopValues(
            xp_rad=lin[0] * ARCSEC_TO_RAD, yp_rad=lin[1] * ARCSEC_TO_RAD,
            ut1_utc_s=lin[2] + dat, lod_s=lin[3],
            dx_rad=lin[4] * ARCSEC_TO_RAD, dy_rad=lin[5] * ARCSEC_TO_RAD,
            dat_s=dat, predicted=bool(a[7] or b[7]),
        )


_DEFAULT: Eop | None = None


def default_eop() -> Eop:
    """執行期優先用 DB/EOP-All.csv（每日更新），沒有則用凍結快照。測試一律用 load_snapshot()。"""
    global _DEFAULT
    if _DEFAULT is None:
        live = Path(__file__).resolve().parents[1] / "DB" / "EOP-All.csv"
        _DEFAULT = Eop(live if live.exists() else SNAPSHOT)
    return _DEFAULT


def load_snapshot() -> Eop:
    return Eop(SNAPSHOT)
