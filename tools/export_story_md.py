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
    "reentry": "再入估算（SGP4 近地點掠過 ＋ 數值 Monte Carlo；TLE-derived）",
}
API_OF = {"groupstats": "group_stats", "isrres": "isr_resolution", "radar": "radar_eval", "skyplot": "radar_eval"}

# ── 匯出字串表（依故事 lang；預設 zh）──
I18N = {
    "zh": {"updated": "故事內容更新", "snapshot": "資料快照", "exported": "文件匯出", "interactive": "互動版",
           "open": "開啟「{t}」", "open_sec": "開啟本節「{t}」", "open_home": "開啟故事首頁",
           "block": "互動區塊", "data": "資料", "page": "頁面", "prov": "資料口徑", "limits": "使用限制",
           "item": "項目", "content": "內容", "limit": "限制", "impact": "影響",
           "live_note": "註：API 與互動頁為即時查詢（每次請求以當時資料庫與 UTC 時刻計算），不綁定本文件之資料快照；本文所引數值以口徑表之快照時刻為準",
           "footer": "*本文件由 SatDashboard StoryMap 匯出（tools/export_story_md.py）。軌道數據為公開 TLE 以 SGP4 傳播之結果，"
                     "屬態勢展示等級；互動圖表請開啟線上版。*"},
    "ja": {"updated": "ストーリー更新日", "snapshot": "データスナップショット", "exported": "エクスポート時刻", "interactive": "インタラクティブ版",
           "open": "「{t}」を開く", "open_sec": "本節「{t}」を開く", "open_home": "ストーリーのトップを開く",
           "block": "インタラクティブ要素", "data": "データ", "page": "ページ", "prov": "データ定義", "limits": "利用上の制限",
           "item": "項目", "content": "内容", "limit": "制限", "impact": "影響",
           "live_note": "注：API とインタラクティブページはリアルタイム照会（リクエスト時点のデータベースと UTC 時刻で計算）であり、本ドキュメントのデータスナップショットには固定されない。本文の数値はデータ定義表のスナップショット時刻を基準とする",
           "footer": "*本ドキュメントは SatDashboard StoryMap（tools/export_story_md.py）から出力されています。軌道データは公開 TLE を SGP4 で"
                     "伝播した結果であり、状況認識表示レベルです。インタラクティブな図表はオンライン版を参照してください。*"},
}
KIND_LABEL_JA = {
    "positions": "TLE 伝播位置（3D・準リアルタイム）", "sat": "日次軌道要素の推移（/orbit）", "groupstats": "グループダッシュボード",
    "isrres": "センサー／分解能分類（公開情報に基づく分類）", "maneuvers": "マヌーバ候補イベント統計",
    "radar": "仮想地上追跡局の可視性・観測カバレッジ評価（API 名 radar_eval は既存実装を踏襲）",
    "skyplot": "パス Skyplot（radar_eval のビュー）", "cdm": "幾何学的接近イベント（単一時刻の距離スクリーニング、衝突リスク判定ではない）",
    "embed": "埋め込みページ", "toc": "章の概要",
    "reentry": "再突入推定（SGP4 近地点通過＋数値 Monte Carlo；TLE-derived）",
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


def usage_limits_md(lang: str = "zh", custom: list | None = None) -> str:
    T = I18N.get(lang, I18N["zh"])
    if custom:
        rows = [[k, v] for k, v in custom]
        intro = ("本故事以公開 TLE 歷史與 ESA／外部公開資訊撰寫；下表為本故事專用之使用限制。" if lang != "ja" else
                 "本ストーリーは公開 TLE 履歴と外部公開情報に基づく。以下は本ストーリー固有の利用上の制限。")
        return f"### {T['limits']}\n\n{intro}\n\n" + md_table([T["limit"], T["impact"]], rows)
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
    elif t == "reentry":
        parts.append(f"- {T['data']}：{link('reentry API', '/api/story/reentry')}")
        rp = reentry_md()
        if rp:
            parts.insert(0, rp)
    parts.append(f"- {T['live_note']}")
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


def sources_md(st: dict, lang: str = "zh") -> str:
    src = st.get("sources") or []
    if not src:
        return ""
    title = "### 歷史事件來源（claim-to-source）" if lang != "ja" else "### 出典（claim-to-source）"
    cols = (["claim_id", "內容", "來源", "來源支持範圍"] if lang != "ja" else ["claim_id", "内容", "出典", "出典が裏付ける範囲"])
    rows = [[c.get("id", ""), c.get("claim", ""),
             (f"[{c.get('source', '')}]({c['url']})" if c.get("url") else c.get("source", "")), c.get("scope", "")] for c in src]
    tag = ("標籤說明：[ESA-reported]＝外部公開資訊、[TLE-derived]＝本系統由 TLE 計算、[interpretation]＝作者解讀；"
           "本系統 TLE 推導結果與外部來源之數字分層標示，不互相背書。" if lang != "ja" else
           "タグ：[ESA-reported]＝外部公開情報、[TLE-derived]＝本システムの TLE 計算、[interpretation]＝著者の解釈。")
    return f"{title}\n\n{md_table(cols, rows)}\n\n> {tag}"


def reentry_md() -> str:
    """由 stories/data/reentry_cluster.json 產生估算摘要表（含與 ESA 預報比較、Salsa 回測）。"""
    p = STORIES / "data" / "reentry_cluster.json"
    if not p.exists():
        return ""
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = []
    for t in d.get("targets", {}).values():
        s1 = t.get("stage1", {}).get("reentry_pass") or {}
        mc = t.get("stage2_mc") or {}
        rows.append([t["name"], t.get("tle_epoch", "")[:10],
                     f"{s1.get('t', '—')}（{s1.get('lat', '')}°, {s1.get('lon', '')}°）",
                     f"{mc.get('t_median', '—')}（{mc.get('lat_median', '')}°, {mc.get('lon_median', '')}°；5–95% 跨度 {mc.get('spread_hours', '—')} h）",
                     f"{t.get('esa_t', '')}（±{t.get('esa_unc_min', '')} min，{t.get('esa_region', '')}）",
                     f"S1 {(t.get('stage1_vs_esa') or {}).get('dt_hours_vs_esa', '—')} h／S2 {(t.get('stage2_vs_esa') or {}).get('dt_hours_vs_esa', '—')} h"])
    md = md_table(["衛星", "最後 TLE", "階段一：SGP4 近地點掠過 [TLE-derived]", "階段二：數值 MC 中位 [TLE-derived]",
                   "ESA 預報 [ESA-reported]", "本系統 − ESA"], rows)
    hc = d.get("hindcast") or {}
    if hc.get("cases"):
        hrows = [[f"{c['lead_days']} 天前 TLE（{c['tle_epoch'][:10]}）", f"{c.get('stage1_err_h', '—')} h", f"{c.get('stage2_err_h', '—')} h",
                  f"{c.get('mc_err_h', '—')} h（跨度 {c.get('mc_spread_h', '—')} h）"] for c in hc["cases"]]
        md += "\n\n**Salsa 2024-09-08 18:47Z 回測（誤差＝本系統 − ESA 實際）**\n\n" + md_table(["前置時間", "階段一誤差", "階段二誤差", "MC 中位誤差"], hrows)
    cal = d.get("calibration") or {}
    if cal.get("best_scale") is not None:
        md += (f"\n\n> 密度尺度校準：以 Salsa 回測取 NRLMSIS 密度尺度 ×{cal['best_scale']}"
               f"（掃描 {cal.get('scales')}，平均誤差 {cal.get('mean_err_h_by_scale')}），套用於 Samba／Tango。")
    stf = d.get("spacetrack_forecast") or {}
    if stf and "error" not in stf:
        parts = []
        for n, rows_ in stf.items():
            if rows_:
                r0 = rows_[0]
                parts.append(f"{n}：{r0.get('_class')} {r0.get('DECAY_EPOCH') or ''}（{r0.get('SOURCE')}，訊息 {r0.get('MSG_EPOCH')}）")
        if parts:
            md += "\n\n> Space-Track 18 SDS 衰減預報（日級）：" + "；".join(parts)
    m = d.get("method", {})
    md += f"\n\n> 產生時間 {d.get('generated_at')}；方法：{m.get('stage1', '')}／{m.get('stage2', '')}。{m.get('caveat', '')}"
    return md


def provenance_md(lang: str = "zh", st: dict | None = None) -> str:
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
        ["資料齡篩選通過（不代表全部傳播成功）", (f"{fmt('fresh_sat_count_7d')} 機；最新 TLE の経過日数 ≤ 7 日（鮮度フィルタ）；SGP4 エラーコードと減衰／再突入状態は個別未確認"
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
        ["APP 版本", f"git commit {fmt('app_commit')}"],
        ["文件狀態", pv.get("status") or "技術展示／非操作級"],
        ["匯出時間", pv.get("generated_at")],
    ]
    uses_isr = bool(st and any(sec.get("type") == "isrres" for sec in st.get("sections", [])))
    if uses_isr:
        rows.insert(-3, ["分類規則版本", f"ISR_RES_RULES v{fmt('classification_version')}（commit {fmt('app_commit')}）"])
    if st and st.get("tle_ranges"):
        rows.insert(6, ["本故事案例可用 TLE 範圍", st["tle_ranges"]])
    if lang == "ja":
        ver = fmt("propagator").split("python-sgp4 ")[-1].split("，")[0] if "python-sgp4" in fmt("propagator") else "?"
        rows = [
            ["データソース", "Space-Track GP（公開 TLE）；本システムの DuckDB による日次集計"],
            ["カタログ物体数", f"{fmt('catalog_sat_count')} 物体（NORAD ID で重複排除、履歴を含む）"],
            ["TLE レコード数", f"{fmt('tle_record_count')} 件"],
            ["TLE epoch 範囲", f"{(pv.get('tle_epoch_min') or '')[:10]} 〜 {(pv.get('tle_epoch_max') or '')[:10]}"],
            ["epoch 品質注記", "エクスポート時刻に対し、データベースには epoch が遅い少数のレコード（GEO 等で一般的）が含まれる；"
                             "履歴再生や「現在」の状態値としては使用せず、最新 epoch と経過日数の算出から除外済み"],
            ["データ鮮度フィルタ通過（全物体の伝播成功を意味しない）",
             f"{fmt('fresh_sat_count_7d')} 物体；最新 TLE の経過日数 ≤ 7 日。ただし SGP4 エラーコードおよび減衰・再突入状態は個別確認していない"],
            ["最新 TLE epoch（≤ エクスポート時）", (pv.get("tle_epoch_latest_past") or "")[:16].replace("T", " ") + " UTC"],
            ["TLE データ経過日数", f"{pv.get('tle_age_days')} 日（エクスポート時刻に対する最新 TLE epoch）" if pv.get("tle_age_days") is not None else "—"],
            ["データスナップショット（DB 更新）", (pv.get("db_updated_at") or "")[:16].replace("T", " ") + " UTC"],
            ["伝播モデル", f"python-sgp4 {ver}；軌道周期に応じて SGP4 または SDP4 を自動選択"],
            ["座標系", "SGP4 出力は TEME。UTC を UT1 の近似として GMST を計算し TEME から ECEF へ回転した後、WGS-84 楕円体から測地緯度・経度・楕円体高を算出。"
                     "極運動・章動・UT1−UTC・完全な ITRF 地球姿勢パラメータは未考慮（地上位置は状況認識表示レベル）"],
            ["精度レベル", "公開 TLE レベル（LEO では along-track 誤差が 1〜3 km/日のオーダーで増大）。精密暦ではなく、運用判断には不適"],
            ["幾何学的接近スクリーニング", "単一伝播時刻（リクエスト時 UTC）における全カタログのペアワイズ距離スクリーニング（KD-tree）であり、時間窓内の TCA 探索ではない。"
                                   "3D 展開後に、2衛星の TLE 重複期間を 30 分間隔で粗走査（区間が長い場合は総点数 ≤1,500 となるよう間隔を拡大）して全体最小距離を求め、"
                                   "最接近時刻 ±12 h を 60 秒間隔で精走査する。距離閾値は初期スクリーニングにのみ使用"],
            ["Pc proxy（衝突リスク順位付け代理値）", "Chan (2008) 2-D 簡略式：相対 RTN 座標系での固定的な概念的標準偏差 σ_R/σ_T を合成した単一の相対共分散"
                                          "（σ_R/T/N = 100/500/100 m；σ_N は 3D 楕円体描画のみに使用）、等価衝突半径 5 m、最接近点 B-plane 仮定。"
                                          "2衛星それぞれの CDM 共分散の合成ではなく、Pc proxy はイベントの順位付けのみに用いる"],
            ["マヌーバ候補", "隣接 TLE の軌道長半径ジャンプ |Δa| 閾値（LEO 0.5 km、MEO/GEO 2 km、間隔 ≤5 日）による候補イベント；"
                        "Δv は Δa から Δv≈n·Δa/2 で換算した等価値；代替説明：TLE 品質の変動、抗力モデル誤差、データ欠落"],
            ["APP バージョン", f"git commit {fmt('app_commit')}"],
            ["ドキュメント状態", "技術デモ／運用レベルではない"],
            ["エクスポート時刻", pv.get("generated_at")],
        ]
        if uses_isr:
            rows.insert(-3, ["分類ルール版", f"ISR_RES_RULES v{fmt('classification_version')}（commit {fmt('app_commit')}）"])
        if st and st.get("tle_ranges"):
            rows.insert(6, ["本ストーリーの事例で利用可能な TLE 範囲", st["tle_ranges"]])
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
    out += [usage_limits_md(lang, st.get("limits")), ""]
    if with_prov:
        out += [provenance_md(lang, st), ""]
    for sec in st.get("sections", []):
        out += [f"## {sec.get('title', '')}", ""]
        if sec.get("body"):
            out += [sec["body"], ""]
        extra = sec_extra(sec, st["id"], lang)
        if extra:
            out += [extra, ""]
    src = sources_md(st, lang)
    if src:
        out += [src, ""]
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
