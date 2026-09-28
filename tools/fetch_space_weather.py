#!/usr/bin/env python3
"""fetch_space_weather.py — 每日更新 CelesTrak 太空天氣檔（F10.7／Ap）。

- SW-All.csv        → scenario-advanced01/DB/ 與 scenario04/DB/（分段 M/A 再入預測用；
                       publish_db_to_dataset.py 隨 slim DB 一併上傳 HF Dataset）
- SW-Last5Years.csv → 父專案 space_weather_ap.csv（atmospheric_drag.py 阻力殘差用）

寫入前檢查：表頭含 DATE／F10.7_OBS／AP_AVG，且最後一筆 OBS 觀測日距今 ≤ MAX_LAG_DAYS；
不符則保留舊檔並以非零結束碼回報。用法：python tools/fetch_space_weather.py
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

APP = Path(__file__).resolve().parents[1]
PARENT = APP.parent
SRC = {
    "all": "https://celestrak.org/SpaceData/SW-All.csv",
    "last5": "https://celestrak.org/SpaceData/SW-Last5Years.csv",
}
TARGETS = {
    "all": [APP / "DB" / "SW-All.csv", APP / "scenario04" / "DB" / "SW-All.csv"],
    "last5": [PARENT / "space_weather_ap.csv"],
}
MAX_LAG_DAYS = 4


def _validate(text: str) -> date:
    rd = csv.DictReader(io.StringIO(text))
    need = {"DATE", "F10.7_OBS", "AP_AVG", "F10.7_DATA_TYPE"}
    if not need <= set(rd.fieldnames or []):
        raise ValueError(f"表頭缺欄位：{need - set(rd.fieldnames or [])}")
    last_obs = None
    for r in rd:
        if r.get("F10.7_DATA_TYPE") == "OBS" and r.get("AP_AVG"):
            last_obs = r["DATE"]
    if last_obs is None:
        raise ValueError("找不到任何 OBS 觀測列")
    d = datetime.strptime(last_obs, "%Y-%m-%d").date()
    lag = (datetime.now(timezone.utc).date() - d).days
    if lag > MAX_LAG_DAYS:
        raise ValueError(f"最後觀測日 {d} 已落後 {lag} 天（上限 {MAX_LAG_DAYS}）")
    return d


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(text, encoding="utf-8", newline="")
    tmp.replace(path)


def main() -> int:
    rc = 0
    for key, url in SRC.items():
        try:
            r = requests.get(url, timeout=(10, 120), headers={"User-Agent": "SatDashboard/1.0"})
            r.raise_for_status()
            text = r.text.replace("\r\n", "\n")
            last = _validate(text)
            for p in TARGETS[key]:
                _write(p, text)
            print(f"[OK] {url.rsplit('/', 1)[-1]}：最後觀測日 {last}，{len(text) / 1e6:.1f} MB → "
                  + ", ".join(str(p) for p in TARGETS[key]))
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {url}：{exc}（保留舊檔）")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
