#!/usr/bin/env python3
"""
export_story_md.py — 把 StoryMap 故事 JSON 匯出成 Markdown
================================================================
用法：
  python tools/export_story_md.py cluster-samba-tango            # → docs/story_cluster-samba-tango.md
  python tools/export_story_md.py cluster-samba-tango -o out.md
  python tools/export_story_md.py --all

互動區塊（positions／sat／groupstats／embed…）以說明文字＋線上連結取代。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

APP = Path(__file__).resolve().parents[1]
STORIES = APP / "scenario04" / "config" / "stories"
BASE_URL = "https://rhynowu-atrdc-satdashboard.hf.space"

KIND_LABEL = {
    "positions": "3D 即時位置", "sat": "逐日軌道要素時序（/orbit）", "groupstats": "群組儀表板",
    "isrres": "感測器／解析度分類", "maneuvers": "機動候選事件統計", "radar": "假想雷達站效益評估",
    "skyplot": "過頂 Skyplot", "cdm": "接近事件", "embed": "內嵌頁面", "toc": "章節總覽",
}


def md_table(cols: list[str], rows: list[list]) -> str:
    esc = lambda c: str(c).replace("|", "\\|")
    out = ["| " + " | ".join(esc(c) for c in cols) + " |",
           "|" + "|".join("---" for _ in cols) + "|"]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def sec_extra(sec: dict, sid: str) -> str:
    t = sec.get("type", "text")
    if t == "table":
        s = md_table(sec.get("columns", []), sec.get("rows", []))
        if sec.get("note"):
            s += f"\n\n> {sec['note']}"
        return s
    if t == "toc":
        return "\n".join(f"- {it.get('icon', '')} **{it.get('label', '')}** — {it.get('sub', '')}"
                         for it in sec.get("items", []))
    if t == "text":
        return ""
    # 互動區塊 → 連結
    parts = [f"*（互動區塊：{KIND_LABEL.get(t, t)}）*"]
    if t == "sat":
        for n in sec.get("norads", []):
            u = f"{BASE_URL}/orbit?norad={n}"
            if sec.get("start"):
                u += f"&start={sec['start']}"
            parts.append(f"- NORAD {n}：{u}")
    elif t == "positions":
        m = sec.get("mode", "all")
        q = f"mode={m}"
        if sec.get("ids"):
            q += "&val=" + ",".join(map(str, sec["ids"]))
        elif sec.get("val"):
            q += f"&val={sec['val']}"
        parts.append(f"- 資料：{BASE_URL}/api/story/positions?{q}")
    elif t == "embed":
        parts.append(f"- 頁面：{BASE_URL}{sec.get('url', '')}")
    elif t in ("groupstats", "isrres", "radar", "skyplot"):
        g = sec.get("group", "")
        api = {"groupstats": "group_stats", "isrres": "isr_resolution", "radar": "radar_eval",
               "skyplot": "radar_eval"}[t]
        parts.append(f"- 資料：{BASE_URL}/api/story/{api}?group={g}")
    elif t == "maneuvers":
        parts.append(f"- 資料：{BASE_URL}/api/story/maneuvers")
    elif t == "cdm":
        parts.append(f"- 資料：{BASE_URL}/api/conjunctions?threshold_km={sec.get('threshold_km', 10)}")
    parts.append(f"- 互動版：{BASE_URL}/story/{sid}" + (f"#{sec['anchor']}" if sec.get("anchor") else ""))
    return "\n".join(parts)


def export(sid: str) -> str:
    p = STORIES / (sid.replace("-", "_") + ".json")
    if not p.exists():
        cands = [q for q in STORIES.glob("*.json")
                 if json.loads(q.read_text(encoding="utf-8")).get("id") == sid]
        if not cands:
            raise SystemExit(f"找不到故事 {sid}")
        p = cands[0]
    st = json.loads(p.read_text(encoding="utf-8"))
    out = [f"# {st['title']}", ""]
    if st.get("subtitle"):
        out += [f"**{st['subtitle']}**", ""]
    if st.get("hero_note"):
        out += [st["hero_note"], ""]
    out += [f"> 更新：{st.get('updated', '')}　|　互動版：{BASE_URL}/story/{st['id']}", ""]
    for sec in st.get("sections", []):
        out += [f"## {sec.get('title', '')}", ""]
        if sec.get("body"):
            out += [sec["body"], ""]
        extra = sec_extra(sec, st["id"])
        if extra:
            out += [extra, ""]
    out += ["---", "",
            "*本文件由 SatDashboard StoryMap 匯出（tools/export_story_md.py）；軌道數據來自本系統 TLE 資料庫，"
            "互動圖表請開啟線上版。*", ""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("story_id", nargs="?")
    ap.add_argument("-o", "--output")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    ids = ([json.loads(q.read_text(encoding="utf-8"))["id"] for q in sorted(STORIES.glob("*.json"))]
           if a.all else [a.story_id])
    if not ids or ids == [None]:
        ap.error("需指定 story_id 或 --all")
    docs = APP / "docs"
    docs.mkdir(exist_ok=True)
    for sid in ids:
        md = export(sid)
        dst = Path(a.output) if (a.output and not a.all) else docs / f"story_{sid}.md"
        dst.write_text(md, encoding="utf-8")
        print(f"✅ {sid} → {dst}（{len(md):,} 字元）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
