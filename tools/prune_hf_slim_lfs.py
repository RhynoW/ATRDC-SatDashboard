#!/usr/bin/env python3
"""prune_hf_slim_lfs.py — 永久刪除 HF Space 上舊版 slim DB 的 LFS blob（保留最新一版）。

HF Space 免費儲存上限 1 GB；scenario04/DB/space_db_slim.duckdb 每版 ~150 MB，
歷史版本累積會令 push 被拒（Repository storage limit reached）。本工具只刪
「同一路徑的較舊 LFS 版本」，其他檔案不動。

不用 HfApi.list_lfs_files()：hub 1.12 對缺 filename 的孤兒 blob（先前刪除／
歷史重寫殘留）直接 LFSFileInfo(**item) 會 KeyError（2026-09-02 實踩）。
改為自行分頁呼叫同一端點並跳過畸形項目。

注意：刪除後 HF 端會重寫 commit 歷史（SHA 變動）。若其後 push 出現 fetch-first，
依 update_slim_publish_hf.bat 內提示以 hf/main 為基底重放即可。
"""
import sys

from huggingface_hub import HfApi
from huggingface_hub.hf_api import LFSFileInfo
from huggingface_hub.utils import build_hf_headers, paginate

RID = "RhynoWu/ATRDC-SatDashboard"
PATH = "scenario04/DB/space_db_slim.duckdb"


def list_slim_lfs(api: HfApi):
    url = f"{api.endpoint}/api/spaces/{RID}/lfs-files"
    headers = build_hf_headers(token=api.token)
    out, skipped = [], 0
    for item in paginate(url, params={}, headers=headers):
        if item.get("filename") != PATH:
            skipped += "filename" not in item
            continue
        out.append(LFSFileInfo(**item))
    if skipped:
        print(f"（跳過 {skipped} 個無 filename 的孤兒 blob）")
    return out


def main() -> int:
    api = HfApi()
    fs = list_slim_lfs(api)
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
    left = sum(f.size for f in list_slim_lfs(api))
    print(f"完成；此路徑剩餘 LFS 用量 {left/1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
