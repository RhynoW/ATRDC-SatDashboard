"""時間尺度。內部以「UTC 日（MJD 整數）＋當日秒」表示時刻，避免單一浮點 MJD 的微秒級捨入。

TT  = UTC + DAT + 32.184 s（DAT 取自 EOP，與 SOFA 閏秒表一致，見測試）
UT1 = UTC + (UT1−UTC)（EOP 內插）
傳播時間 τ 為自曆元起算的 SI 秒；傳播區間內不得跨越閏秒（propagate 會檢查）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .constants import DAY_S, MJD0, TT_MINUS_TAI_S

_MJD_UNIX0 = 40587  # 1970-01-01


@dataclass(frozen=True)
class UtcTime:
    mjd: int          # UTC 日的 MJD
    sec: float        # 當日秒 [0, 86400)

    @staticmethod
    def parse(iso: str) -> "UtcTime":
        s = iso.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            raise ValueError(f"時刻須含時區（建議以 Z 結尾）：{iso}")
        dt = dt.astimezone(timezone.utc)
        days = (dt.date() - datetime(1970, 1, 1).date()).days
        sec = dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond * 1e-6
        return UtcTime(_MJD_UNIX0 + days, float(sec))

    def plus(self, dt_s: float) -> "UtcTime":
        total = self.sec + dt_s
        dd = int(total // DAY_S)
        return UtcTime(self.mjd + dd, total - dd * DAY_S)

    def mjd_float(self) -> float:
        return self.mjd + self.sec / DAY_S

    def iso(self) -> str:
        days = self.mjd - _MJD_UNIX0
        whole = int(self.sec)
        us = int(round((self.sec - whole) * 1e6))
        if us >= 1_000_000:
            whole, us = whole + 1, us - 1_000_000
        base = datetime(1970, 1, 1, tzinfo=timezone.utc).timestamp() + days * DAY_S + whole
        dt = datetime.fromtimestamp(base, tz=timezone.utc).replace(microsecond=us)
        return dt.isoformat().replace("+00:00", "Z")

    def tt_jd(self, dat_s: float) -> tuple[float, float]:
        return MJD0 + self.mjd, (self.sec + dat_s + TT_MINUS_TAI_S) / DAY_S

    def ut1_jd(self, ut1_utc_s: float) -> tuple[float, float]:
        return MJD0 + self.mjd, (self.sec + ut1_utc_s) / DAY_S
