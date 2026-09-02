#!/usr/bin/env python3
"""prune_hf_slim_lfs.py — 永久刪除 HF Space 上舊版 slim DB 的 LFS blob（保留最新一版）。

HF Space 免費儲存上限 1 GB；scenario04/DB/space_db_slim.duckdb 每版 ~150 MB，
歷史版本累積會令 push 被拒（Repository storage limit reached）。本工具只刪
「同一路徑的較舊 LFS 版本」，其他檔案不動。

注意：刪除後 HF 端會重寫 commit 歷史（SHA 變動）。若其後 push 出現 fetch-first，
依 update_slim_publish_hf.bat 內提示以 hf/main 為基底重放即可（2026-09-02 實證）。
"""
import sys
from huggingface_hub import HfApi

RID = "RhynoWu/ATRDC-SatDashboard"
PATH = "scenario04/DB/space_db_slim.duckdb"

def main() -> int:
    api = HfApi()
    fs = [f for f in api.list_lfs_files(RID, repo_type="space") if f.filename == PATH]
    if len(fs) <= 1:
        print(f"僅 {len(fs)} 版，無需清理。")
        return 0
    fs.sort(key=lambda f: f.pushed_at)
    victims, keep = fs[:-1], fs[-1]
    print(f"保留 {keep.pushed_at:%Y-%m-%d} 版（{keep.size/1e6:.0f} MB）；"
          f"刪除 {len(victims)} 個舊版：")
    for f in victims:
        print(f"  - {f.pushed_at:%Y-%m-%d %H:%M} {f.size/1e6:6.0f} MB")
    api.permanently_delete_lfs_files(RID, victims, repo_type="space")
    left = sum(f.size for f in api.list_lfs_files(RID, repo_type="space"))
    print(f"完成；剩餘 LFS 用量 {left/1e6:.0f} MB / 1000 MB")
    return 0

if __name__ == "__main__":
    sys.exit(main())
