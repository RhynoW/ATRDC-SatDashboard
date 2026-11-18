"""回歸案例證據包（v0.3 第 12 節）：每個歷史 RPO 案例輸出原始 TLE 快照、篩選規則、程式版本與重跑結果。

用法：py -3.13 tools/build_evidence_pack.py
輸出：scenario04/phase2/evidence/<primary>_<secondary>/{tle_snapshot.json, rerun.json, README.md}
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scenario04.ingestion.db import resolve_db  # noqa: E402
from scenario04.physics import rpo  # noqa: E402

OUT = ROOT / "scenario04" / "phase2" / "evidence"
CASES = [(58573, 59884, None), (58204, 43874, None)]
DISCLAIMER = ("重建結果代表在上述 TLE 輸入、篩選規則與 SGP4 模型下的計算值，"
              "不作為軌道真值、實際碰撞機率或接近意圖的判讀依據。")


def git_rev() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def git_dirty() -> bool:
    r = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "scenario04/physics/rpo.py"],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def snapshot(con, nid, window, excl):
    _recs, _ep, name, df = rpo._load_recs(con, nid, window, excl)
    if df is None:
        return None
    cols = [c for c in df.columns if c != "object_name"]
    rows = []
    for _, r in df[cols].iterrows():
        row = {}
        for c in cols:
            v = r[c]
            row[c] = v.isoformat() if hasattr(v, "isoformat") else (None if v != v else (float(v) if isinstance(v, (int, float)) else v))
        rows.append(row)
    return {"norad_id": nid, "name": name, "n_elsets": len(rows), "columns": cols, "elsets": rows}


def main():
    db = resolve_db()
    st = Path(db).stat()
    rev, dirty = git_rev(), git_dirty()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for p, s, variant in CASES:
        preset = rpo.preset_for(p, s, variant)
        window = preset.get("window")
        excl = preset.get("exclude_epochs", {})
        con = rpo._connect_ro(db)
        try:
            snaps = {str(n): snapshot(con, n, window, excl.get(int(n))) for n in (p, s)}
        finally:
            con.close()
        data = rpo.compute_rpo_scene(p, s, variant=variant)
        d = OUT / f"{p}_{s}"
        d.mkdir(parents=True, exist_ok=True)
        meta = {
            "case": f"{p}×{s}", "variant": variant, "generated_utc": now,
            "data_source": {"db_file": Path(db).name, "db_size_bytes": st.st_size,
                            "db_mtime_utc": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                            "table": "raw_tle_archive", "origin": "Space-Track gp_history／CelesTrak（經本系統資料管線匯入）"},
            "filters": {"window": window, "span": preset.get("span"), "exclude_epochs": excl,
                        "dedup": "同一 epoch 只保留一筆", "propagation": "每個時刻取 epoch 最接近的 elset 以 SGP4 傳播"},
            "program": {"repo": "scenario-advanced01", "commit": rev, "rpo_py_modified_since_commit": dirty,
                        "module": "scenario04/physics/rpo.py compute_rpo_scene"},
            "disclaimer": DISCLAIMER,
        }
        (d / "tle_snapshot.json").write_text(json.dumps({"meta": meta, "objects": snaps}, ensure_ascii=False, indent=1), encoding="utf-8")
        rerun = {"meta": meta, "summary": data["summary"],
                 "events": data["meta"].get("events"), "focus": data["meta"].get("focus"),
                 "overlap": data["meta"].get("overlap")}
        (d / "rerun.json").write_text(json.dumps(rerun, ensure_ascii=False, indent=1), encoding="utf-8")
        sm = data["summary"]
        (d / "README.md").write_text(
            f"# 回歸案例證據包：{p} × {s}\n\n"
            f"- 產生時間：{now}\n- 資料：{Path(db).name}（{st.st_size:,} bytes），表 raw_tle_archive\n"
            f"- 程式：scenario-advanced01 commit `{rev}`" + ("（rpo.py 有未提交修改）" if dirty else "") + "\n"
            f"- 篩選：window={window}，span={preset.get('span')}，exclude_epochs={excl or '無'}\n"
            f"- elset 數：" + "、".join(f"{k} {v['n_elsets'] if v else 0} 筆" for k, v in snaps.items()) + "\n\n"
            f"## 重跑結果\n\n| 項目 | 值 |\n|---|---|\n"
            f"| TCA（1 秒精算） | {sm['t_min']} |\n| 最小距離 | {sm['d_min']} km |\n"
            f"| 取樣最小（{sm['fine_step_s']:.0f} s） | {sm.get('d_min_sampled')} km @ {sm.get('t_min_sampled')} |\n"
            f"| 全窗粗掃最小 | {sm['d_min_global']} km @ {sm['t_min_global']} |\n\n"
            f"檔案：`tle_snapshot.json`（原始 elset）、`rerun.json`（重跑輸出）。\n\n**聲明**：{DISCLAIMER}\n",
            encoding="utf-8")
        print(d.name, sm["t_min"], sm["d_min"])


if __name__ == "__main__":
    main()
