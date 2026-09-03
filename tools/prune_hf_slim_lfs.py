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
import re
import sys

from huggingface_hub import HfApi
from huggingface_hub.hf_api import LFSFileInfo
from huggingface_hub.utils import build_hf_headers, get_session, hf_raise_for_status, paginate

RID = "RhynoWu/ATRDC-SatDashboard"
PATH = "scenario04/DB/space_db_slim.duckdb"


def _lfs_url(api: HfApi) -> str:
    return f"{api.endpoint}/api/spaces/{RID}/lfs-files"


def _list_raw(api: HfApi) -> list[dict]:
    return list(paginate(_lfs_url(api), params={},
                         headers=build_hf_headers(token=api.token)))


def list_slim_lfs(api: HfApi):
    out = [LFSFileInfo(**item) for item in _list_raw(api)
           if item.get("filename") == PATH]
    return out


def _is_orphan(item: dict) -> bool:
    """孤兒＝不被任何 commit 引用、卻仍佔儲存額度的 blob。

    HF 的 permanently_delete_lfs_files 把被刪版本變成 filename=None 的孤兒
    （空間並未真正釋放，2026-09-02 實測 865 MB 中孤兒佔 ~695 MB）；push 中途
    失敗的上傳殘留則以 64 位 hex 字串當 filename。兩者皆可安全刪除。
    """
    fn = item.get("filename")
    return fn is None or re.fullmatch(r"[0-9a-f]{64}", fn) is not None


def delete_orphans(api: HfApi) -> None:
    orphans = [i for i in _list_raw(api) if _is_orphan(i)]
    if not orphans:
        return
    shas = [i.get("fileOid") or i.get("file_oid") for i in orphans]
    if not all(shas):
        print("  ⚠ 部分孤兒項目無 fileOid，略過孤兒清理")
        return
    tot = sum(i.get("size", 0) for i in orphans)
    print(f"刪除 {len(orphans)} 個孤兒 blob（{tot/1e6:.0f} MB）…")
    r = get_session().post(f"{_lfs_url(api)}/batch",
                           headers=build_hf_headers(token=api.token),
                           json={"deletions": {"sha": shas, "rewriteHistory": True}})
    hf_raise_for_status(r)


def main() -> int:
    api = HfApi()
    delete_orphans(api)
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
    delete_orphans(api)   # 刪除動作本身會產生孤兒 → 立即清掉，空間才真正釋放
    total = sum(i.get("size", 0) for i in _list_raw(api))
    print(f"完成；repo LFS 總用量 {total/1e6:.0f} MB / 1000 MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
