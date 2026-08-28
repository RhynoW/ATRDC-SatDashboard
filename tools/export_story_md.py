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
    "isrres": "感測器／解析度分類（公開資料分類）", "maneuvers": "機動候選事件統計",
    "radar": "假想地面追蹤站可見性與觀測覆蓋評估（API 名稱 radar_eval 沿用既有實作，輸出為幾何可見性指標）",
    "skyplot": "過頂 Skyplot（radar_eval 之視圖；API 名稱沿用既有實作，輸出為幾何可見性指標）",
    "cdm": "幾何接近事件（單一時刻距離篩選，非碰撞風險判定）",
    "embed": "內嵌頁面", "toc": "章節總覽",
}
API_OF = {"groupstats": "group_stats", "isrres": "isr_resolution", "radar": "radar_eval", "skyplot": "radar_eval"}

# ── 匯出字串表（依故事 lang；預設 zh）──
I18N = {
    "zh": {"updated": "故事內容更新", "snapshot": "資料快照", "exported": "文件匯出", "interactive": "互動版",
           "open": "開啟「{t}」", "open_sec": "開啟本節「{t}」", "open_home": "開啟故事首頁",
           "block": "互動區塊", "data": "資料", "page": "頁面", "prov": "資料口徑", "limits": "使用限制",
           "item": "項目", "content": "內容", "limit": "限制", "impact": "影響",
           "footer": "*本文件由 SatDashboard StoryMap 匯出（tools/export_story_md.py）。軌道數據為公開 TLE 以 SGP4 傳播之結果，"
                     "屬態勢展示等級；互動圖表請開啟線上版。*"},
    "ja": {"updated": "ストーリー更新日", "snapshot": "データスナップショット", "exported": "エクスポート時刻", "interactive": "インタラクティブ版",
           "open": "「{t}」を開く", "open_sec": "本節「{t}」を開く", "open_home": "ストーリーのトップを開く",
           "block": "インタラクティブ要素", "data": "データ", "page": "ページ", "prov": "データ定義", "limits": "利用上の制限",
           "item": "項目", "content": "内容", "limit": "制限", "impact": "影響",
           "footer": "*本ドキュメントは SatDashboard StoryMap（tools/export_story_md.py）から出力されています。軌道データは公開 TLE を SGP4 で"
                     "伝播した結果であり、状況認識表示レベルです。インタラクティブな図表はオンライン版を参照してください。*"},
}
KIND_LABEL_JA = {
    "positions": "TLE 伝播位置（3D・準リアルタイム）", "sat": "日次軌道要素の推移（/orbit）", "groupstats": "グループダッシュボード",
    "isrres": "センサー／分解能分類（公開情報に基づく分類）", "maneuvers": "マヌーバ候補イベント統計",
    "radar": "仮想地上追跡局の可視性・観測カバレッジ評価（API 名 radar_eval は既存実装を踏襲）",
    "skyplot": "パス Skyplot（radar_eval のビュー）", "cdm": "幾何学的接近イベント（単一時刻の距離スクリーニング、衝突リスク判定ではない）",
    "embed": "埋め込みページ", "toc": "章の概要",
}
LIMITS_JA = [
    ("公開 TLE + SGP4", "位置は準リアルタイム伝播の推定値であり精密暦ではない；精密編隊や運用レベルの判断には不適"),
    ("固定共分散 σ_R/T/N", "Pc は proxy／順位付け値であり CDM 衝突リスクではない；3σ 楕円体は概念図"),
    ("軌道長半径ジャンプ閾値", "マヌーバ結果は「候補イベント」であり確認済みマヌーバではない；Δv は近円・接線インパルスの等価推定"),
    ("幾何学的可視性モデル", "地上局評価は可視性／観測カバレッジであり、レーダー方程式を含まず探知能力を意味しない"),
    ("公開分解能の推定", "センサー／分解能分類は公開情報に基づく分類であり、実際のミッション性能ではない"),
    ("名称ルール分類", "星座／国／用途はカタログ名称と metadata ルールで判定し、データの完全性に影響される"),
]
LIMITS_INTRO_JA = ("本展示は取得可能な最新の公開 TLE を基礎とし、SGP4/SDP4 による準リアルタイム軌道伝播で、星座の状況、軌道異常候補、"
                   "地上局可視性、近距離の幾何学的イベントを統合したものです。結果は公開データと簡略モデルによる技術デモであり、"
                   "精密暦、実際のレーダー探知能力、確認済みマヌーバ、運用レベルの衝突確率を示すものではありません。")


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


def link(label: str, path: str, **params) -> str:
    """Markdown 連結；含查詢參數時附可讀條件（避免 percent-encoding 讓讀者看不懂）。"""
    u = url(path, **params)
    cond = ", ".join(f"{k}={v}" for k, v in params.items() if v not in (None, ""))
    return f"[{label}]({u})" + (f"（{cond}）" if cond else "")


LIMITS = [
    ("公開 TLE + SGP4", "位置為近即時傳播估計，非精密星曆；不適合精密編隊或操作級決策"),
    ("固定協方差 σ_R/T/N", "Pc 為 proxy／排序值，非 CDM 碰撞風險；3σ 橢球為示意"),
    ("半長軸跳變門檻", "機動結果為「候選事件」，非確認機動；Δv 為近圓切向脈衝之等效估算"),
    ("幾何可見性模型", "地面站評估為可見性／觀測覆蓋，不含雷達方程式，不代表偵測能力"),
    ("公開解析度推估", "感測器／解析度分類為公開資料分類，不代表實際任務效能"),
    ("名稱規則分類", "星系／國別／用途由目錄名稱與 metadata 規則判定，受資料完整性影響"),
]


def usage_limits_md(lang: str = "zh") -> str:
    T = I18N.get(lang, I18N["zh"])
    if lang == "ja":
        rows = [[k, v] for k, v in LIMITS_JA]
        return f"### {T['limits']}\n\n{LIMITS_INTRO_JA}\n\n" + md_table([T["limit"], T["impact"]], rows)
    rows = [[k, v] for k, v in LIMITS]
    return (f"### {T['limits']}\n\n本展示以最新可取得之公開 TLE 為基礎，使用 SGP4/SDP4 進行近即時軌道傳播，"
            "整合星座態勢、軌道異常候選、地面站可見性與近距離幾何事件。結果屬公開資料與簡化模型的技術展示，"
            "不代表精密星曆、實際雷達偵測能力、已確認機動或操作級碰撞機率。\n\n" + md_table([T["limit"], T["impact"]], rows))


def md_table(cols: list[str], rows: list[list]) -> str:
    esc = lambda c: str(c).replace("|", "\\|")
    out = ["| " + " | ".join(esc(c) for c in cols) + " |",
           "|" + "|".join("---" for _ in cols) + "|"]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def sec_extra(sec: dict, sid: str, lang: str = "zh") -> str:
    T = I18N.get(lang, I18N["zh"])
    labels = KIND_LABEL_JA if lang == "ja" else KIND_LABEL
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
    parts = [f"*（{T['block']}：{labels.get(t, t)}）*"]
    if t == "sat":
        for n in sec.get("norads", []):
            parts.append(f"- NORAD {n}：{link('/orbit', '/orbit', norad=n, start=sec.get('start'))}")
    elif t == "positions":
        m = sec.get("mode", "all")
        val = ",".join(map(str, sec["ids"])) if m == "ids" else sec.get("val")
        parts.append(f"- {T['data']}：{link('positions API', '/api/story/positions', mode=m, val=val)}")
    elif t == "embed":
        parts.append(f"- {T['page']}：[{sec.get('url', '')}]({BASE_URL}{sec.get('url', '')})")
    elif t in API_OF:
        parts.append(f"- {T['data']}：{link(API_OF[t] + ' API', '/api/story/' + API_OF[t], group=sec.get('group'))}")
    elif t == "maneuvers":
        parts.append(f"- {T['data']}：{link('maneuvers API', '/api/story/maneuvers')}")
    elif t == "cdm":
        parts.append(f"- {T['data']}：{link('conjunctions API', '/api/conjunctions', threshold_km=sec.get('threshold_km', 10))}")
    if sec.get("anchor"):
        parts.append(f"- {T['interactive']}：[{T['open_sec'].format(t=sec.get('title', ''))}]({BASE_URL}/story/{sid}#{sec['anchor']})")
    else:
        parts.append(f"- {T['interactive']}：[{T['open_home']}]({BASE_URL}/story/{sid})")
    return "\n".join(parts)


PROV_ROWS_JA = {
    "資料來源": "データソース", "目錄衛星數": "カタログ衛星数", "TLE 記錄數": "TLE レコード数", "TLE epoch 範圍": "TLE epoch 範囲",
    "epoch 品質註記": "epoch 品質注記", "通過資料齡篩選": "データ鮮度フィルタ通過", "TLE 最新 epoch（≤ 匯出時）": "最新 TLE epoch（≤ エクスポート時）",
    "TLE 資料齡": "TLE データ経過日数", "資料快照（DB 更新）": "データスナップショット（DB 更新）", "傳播模型": "伝播モデル",
    "座標系": "座標系", "精度等級": "精度レベル", "幾何接近篩選": "幾何学的接近スクリーニング", "Pc proxy（碰撞風險排序代理值）": "Pc proxy（衝突リスク順位付け代理値）",
    "機動候選": "マヌーバ候補", "分類規則版本": "分類ルール版", "APP 版本": "APP バージョン", "文件狀態": "ドキュメント状態", "匯出時間": "エクスポート時刻",
}


def provenance_md(lang: str = "zh") -> str:
    """由本機 DB 取資料口徑（與 /api/story/provenance 同一函式）。內容為中文技術描述；日文版翻譯欄位名。"""
    T = I18N.get(lang, I18N["zh"])
    try:
        from scenario04.api.story import provenance
        pv = provenance()
    except Exception as exc:  # noqa: BLE001
        return f"> {T['prov']}：{exc}"
    fmt = lambda k: str(pv.get(k)) if pv.get(k) is not None else "—"
    rows = [
        ["資料來源", pv.get("source")],
        ["目錄衛星數", f"{fmt('catalog_sat_count')} 顆（去重 NORAD，含歷史）"],
        ["TLE 記錄數", f"{fmt('tle_record_count')} 筆"],
        ["TLE epoch 範圍", f"{(pv.get('tle_epoch_min') or '')[:10]} ～ {(pv.get('tle_epoch_max') or '')[:10]}"],
        ["epoch 品質註記", ("エクスポート時刻に対し、データベースには epoch が遅い少数のレコード（GEO 等で一般的）が含まれる；"
                          "これらは履歴再生や「現在」の状態値としては使用せず、最新 epoch と経過日数の算出から除外済み" if lang == "ja" else
                          "相對於文件匯出時間，資料庫含少數 epoch 較晚之紀錄（GEO 等常見）；"
                          "此類紀錄不作為歷史回放或「目前」狀態值，計算最新 epoch 與資料齡時已排除")],
        ["通過資料齡篩選", (f"{fmt('fresh_sat_count_7d')} 機；最新 TLE の経過日数 ≤ 7 日（鮮度フィルタ）；SGP4 エラーコードと減衰／再突入状態は個別未確認"
                          if lang == "ja" else f"{fmt('fresh_sat_count_7d')} 顆；{pv.get('fresh_criteria') or ''}")],
        ["TLE 最新 epoch（≤ 匯出時）", (pv.get("tle_epoch_latest_past") or "")[:16].replace("T", " ") + " UTC"],
        ["TLE 資料齡", (("—" if pv.get("tle_age_days") is None else
                        (f"{pv.get('tle_age_days')} 日（エクスポート時刻に対する最新 TLE epoch）" if lang == "ja" else
                         f"{pv.get('tle_age_days')} 天（相對於文件匯出時間之最新 TLE epoch）")))],
        ["資料快照（DB 更新）", (pv.get("db_updated_at") or "")[:16].replace("T", " ") + " UTC"],
        ["傳播模型", pv.get("propagator")],
        ["座標系", pv.get("frame")],
        ["精度等級", pv.get("accuracy")],
        ["幾何接近篩選", pv.get("screening")],
        ["Pc proxy（碰撞風險排序代理值）", pv.get("pc_model")],
        ["機動候選", pv.get("maneuver_method")],
        ["分類規則版本", f"ISR_RES_RULES v{fmt('classification_version')}（commit {fmt('app_commit')}）"],
        ["APP 版本", f"git commit {fmt('app_commit')}"],
        ["文件狀態", pv.get("status") or "技術展示／非操作級"],
        ["匯出時間", pv.get("generated_at")],
    ]
    if lang == "ja":
        rows = [[PROV_ROWS_JA.get(k, k), v] for k, v in rows]
    return f"### {T['prov']}\n\n" + md_table([T["item"], T["content"]], rows)


def story_lang(st: dict) -> str:
    return st.get("lang") or ("ja" if st.get("id", "").endswith("-ja") else "zh")


def export(sid: str, with_prov: bool = True) -> str:
    st = load_story(sid)
    lang = story_lang(st)
    T = I18N.get(lang, I18N["zh"])
    out = [f"# {st['title']}", ""]
    if st.get("subtitle"):
        out += [f"**{st['subtitle']}**", ""]
    if st.get("hero_note"):
        out += [st["hero_note"], ""]
    snap = ""
    if with_prov:
        try:
            from scenario04.api.story import provenance
            pv0 = provenance()
            snap = (f"  \n> {T['snapshot']}：{(pv0.get('db_updated_at') or '')[:16].replace('T', ' ')} UTC"
                    f"  \n> {T['exported']}：{pv0.get('generated_at')}")
        except Exception:  # noqa: BLE001
            snap = ""
    out += [f"> {T['updated']}：{st.get('updated', '')}{snap}  \n> {T['interactive']}："
            f"[{T['open'].format(t=st['title'])}]({BASE_URL}/story/{st['id']})", ""]
    out += [usage_limits_md(lang), ""]
    if with_prov:
        out += [provenance_md(lang), ""]
    for sec in st.get("sections", []):
        out += [f"## {sec.get('title', '')}", ""]
        if sec.get("body"):
            out += [sec["body"], ""]
        extra = sec_extra(sec, st["id"], lang)
        if extra:
            out += [extra, ""]
    out += ["---", "", T["footer"], ""]
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
