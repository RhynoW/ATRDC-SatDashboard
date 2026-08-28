#!/usr/bin/env python3
"""
export_story_md.py — 把 StoryMap 故事 JSON 匯出成 Markdown
================================================================
用法：
  python tools/export_story_md.py cluster-samba-tango            # → docs/story_cluster-samba-tango.md
  python tools/export_story_md.py cluster-samba-tango -o out.md
  python tools/export_story_md.py --all
  python tools/export_story_md.py --all --no-provenance          # 不讀 DB（純文件轉換）

- 故事 JSON 先以 config/stories/schema.json 驗證（jsonschema），不合即中止。
- 互動區塊（positions／sat／groupstats／embed…）以說明文字＋線上連結取代（urlencode）。
- 文件開頭附「資料口徑」：資料來源、TLE epoch 範圍／資料齡、傳播模型、座標系、Pc／機動方法。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlencode

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
STORIES = APP / "scenario04" / "config" / "stories"
SCHEMA = STORIES / "schema.json"
BASE_URL = "https://rhynowu-atrdc-satdashboard.hf.space"

KIND_LABEL = {
    "positions": "TLE 傳播位置（3D，近即時）", "sat": "逐日軌道要素時序（/orbit）", "groupstats": "群組儀表板",
    "isrres": "感測器／解析度分類", "maneuvers": "機動候選事件統計", "radar": "假想雷達站效益評估",
    "skyplot": "過頂 Skyplot（radar_eval 之視圖）", "cdm": "幾何接近事件（篩選，非碰撞風險判定）",
    "embed": "內嵌頁面", "toc": "章節總覽",
}
API_OF = {"groupstats": "group_stats", "isrres": "isr_resolution", "radar": "radar_eval", "skyplot": "radar_eval"}


def validate(story: dict) -> None:
    import jsonschema
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(story, schema)


def load_story(sid: str) -> dict:
    for q in sorted(STORIES.glob("*.json")):
        d = json.loads(q.read_text(encoding="utf-8"))
        if d.get("id") == sid:
            validate(d)
            return d
    raise SystemExit(f"找不到故事 {sid}")


def url(path: str, **params) -> str:
    qs = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return f"{BASE_URL}{path}" + (f"?{qs}" if qs else "")


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
    parts = [f"*（互動區塊：{KIND_LABEL.get(t, t)}）*"]
    if t == "sat":
        for n in sec.get("norads", []):
            parts.append(f"- NORAD {n}：{url('/orbit', norad=n, start=sec.get('start'))}")
    elif t == "positions":
        m = sec.get("mode", "all")
        val = ",".join(map(str, sec["ids"])) if m == "ids" else sec.get("val")
        parts.append(f"- 資料：{url('/api/story/positions', mode=m, val=val)}")
    elif t == "embed":
        parts.append(f"- 頁面：{BASE_URL}{sec.get('url', '')}")
    elif t in API_OF:
        parts.append(f"- 資料：{url('/api/story/' + API_OF[t], group=sec.get('group'))}")
    elif t == "maneuvers":
        parts.append(f"- 資料：{url('/api/story/maneuvers')}")
    elif t == "cdm":
        parts.append(f"- 資料：{url('/api/conjunctions', threshold_km=sec.get('threshold_km', 10))}")
    anchor = f"#{sec['anchor']}" if sec.get("anchor") else ""
    parts.append(f"- 互動版：{BASE_URL}/story/{sid}{anchor}")
    return "\n".join(parts)


def provenance_md() -> str:
    """由本機 DB 取資料口徑（與 /api/story/provenance 同一函式）。"""
    try:
        from scenario04.api.story import provenance
        pv = provenance()
    except Exception as exc:  # noqa: BLE001
        return f"> 資料口徑：無法取得（{exc}）"
    rows = [
        ["資料來源", pv.get("source")],
        ["TLE epoch 範圍", f"{(pv.get('tle_epoch_min') or '')[:10]} ～ {(pv.get('tle_epoch_max') or '')[:10]}"
                           f"（{pv.get('valid_sat_count') or '—'} 顆）"],
        ["TLE 最新 epoch（≤ 匯出時）", (pv.get("tle_epoch_latest_past") or "")[:16].replace("T", " ") + " UTC"],
        ["TLE 資料齡", f"{pv.get('tle_age_days')} 天（匯出時）" if pv.get("tle_age_days") is not None else "—"],
        ["資料庫更新", (pv.get("db_updated_at") or "")[:16].replace("T", " ") + " UTC"],
        ["傳播模型", pv.get("propagator")],
        ["座標系", pv.get("frame")],
        ["精度等級", pv.get("accuracy")],
        ["碰撞機率", pv.get("pc_model")],
        ["機動候選", pv.get("maneuver_method")],
        ["匯出時間", pv.get("generated_at")],
    ]
    return "### 資料口徑\n\n" + md_table(["項目", "內容"], rows)


def export(sid: str, with_prov: bool = True) -> str:
    st = load_story(sid)
    out = [f"# {st['title']}", ""]
    if st.get("subtitle"):
        out += [f"**{st['subtitle']}**", ""]
    if st.get("hero_note"):
        out += [st["hero_note"], ""]
    out += [f"> 更新：{st.get('updated', '')}　|　互動版：{BASE_URL}/story/{st['id']}", ""]
    if with_prov:
        out += [provenance_md(), ""]
    for sec in st.get("sections", []):
        out += [f"## {sec.get('title', '')}", ""]
        if sec.get("body"):
            out += [sec["body"], ""]
        extra = sec_extra(sec, st["id"])
        if extra:
            out += [extra, ""]
    out += ["---", "",
            "*本文件由 SatDashboard StoryMap 匯出（tools/export_story_md.py）。軌道數據為公開 TLE 以 SGP4 傳播之結果，"
            "屬態勢展示等級；互動圖表請開啟線上版。*", ""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("story_id", nargs="?")
    ap.add_argument("-o", "--output")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-provenance", action="store_true", help="不讀 DB，省略資料口徑段")
    a = ap.parse_args()
    if a.all:
        ids = [json.loads(q.read_text(encoding="utf-8"))["id"] for q in sorted(STORIES.glob("*.json"))
               if q.name != "schema.json"]
    elif a.story_id:
        ids = [a.story_id]
    else:
        ap.error("需指定 story_id 或 --all")
    docs = APP / "docs"
    docs.mkdir(exist_ok=True)
    for sid in ids:
        md = export(sid, with_prov=not a.no_provenance)
        dst = Path(a.output) if (a.output and not a.all) else docs / f"story_{sid}.md"
        dst.write_text(md, encoding="utf-8")
        print(f"✅ {sid} → {dst}（{len(md):,} 字元）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
