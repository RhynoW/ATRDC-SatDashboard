#!/usr/bin/env python3
"""publish_db_to_dataset.py — 把 slim DB 發布到 HF Dataset 並重啟 Space。

DB 已移出 Space git(2026-09-07 根本解):
  - 上傳 DB/space_db_slim.duckdb → datasets/RhynoWu/satdashboard-db
  - super_squash_history 壓 Dataset 歷史(儲存只留最新版;Dataset 端壓縮無本地 git,零副作用)
  - restart Space(容器重啟 → resolve_db() 於開機時下載新 DB)
"""
import sys
from pathlib import Path

from huggingface_hub import HfApi

DATASET = "RhynoWu/satdashboard-db"
# 兩個 Space 共用同一個 Dataset 做 resolve_db()（見 scenario04/ingestion/db.py），
# 舊版與新版 i18n 都需要重啟才會拿到新 DB，缺一會讓其中一個 Space 資料卡在舊快照
SPACES = ["RhynoWu/ATRDC-SatDashboard", "RhynoWu/ATRDC-SatDashboard-i18n"]
DB = Path(__file__).resolve().parents[1] / "DB" / "space_db_slim.duckdb"


def main() -> int:
    if not DB.exists():
        print(f"[FAIL] 找不到 {DB}")
        return 1
    api = HfApi()
    print(f"上傳 {DB.stat().st_size/1e6:.0f} MB → {DATASET} …")
    api.upload_file(path_or_fileobj=str(DB), path_in_repo="space_db_slim.duckdb",
                    repo_id=DATASET, repo_type="dataset",
                    commit_message=f"slim DB update ({DB.stat().st_size/1e6:.0f} MB)")
    print("壓縮 Dataset 歷史（只留最新版，控制儲存額度）…")
    api.super_squash_history(repo_id=DATASET, repo_type="dataset")
    for space in SPACES:
        print(f"重啟 Space {space}（開機時自動下載新 DB）…")
        api.restart_space(space)
    print("[OK] 發布完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
