"""建議名稱：辨識 Space-Track 尚未命名的「OBJECT A/B/C…」可能的真實名稱。

依可靠度由高到低：
1. official       —— Space-Track SATCAT 已正式命名（目錄名稱仍是 OBJECT x，但 SATCAT 名稱已更新）；high
2. inferred       —— GCAT 逐顆判讀（McDowell, planet4589.org, CC BY 4.0；約落後數個月），或單一酬載發射的任務名；medium
3. group_inferred —— 只能確認是某次發射的 N 顆之一：命名到群組（「XX 群組衛星（推論）」），不指派 A/B/C 個體身分
另有 conflict（來源矛盾）、unresolved（無可靠來源，維持 OBJECT x）。推論名稱一律加「（推論）」並附來源鏈，
只作為人工複核的候選，不會自動套用。
資料來自 DB 內 satcat／gcat_sat／gcat_launch 表（本機每日管線 tools/update_satcat.py 寫入、跟著 DB 發布）。
對應 Rust 單機版 src/suggest.rs。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from flask import Blueprint, jsonify, request

from ..ingestion.db import resolve_db
from ..ingestion.index import get_sat_index
from .satadmin import get_satcat, intl_code, satcat_meta

logger = logging.getLogger(__name__)
bp = Blueprint("suggest", __name__)

OBJ_RE = re.compile(r"^OBJECT [A-Z]{1,3}$")

# 常見中國衛星計畫拼音 → 中文（長詞優先）
ZH = [
    ("Tongxin Jishu Shiyan", "通信技術試驗"), ("Weixing Hulianwang Jishu Shiyan", "衛星互聯網技術試驗"),
    ("Weixing Hulianwang Digui", "衛星互聯網低軌"), ("Weixing Hulianwang", "衛星互聯網"), ("Weili Kongjian", "微厘空間"),
    ("Hai Yang", "海洋"), ("Haiyang", "海洋"), ("Yaogan", "遙感"), ("Shijian", "實踐"), ("Shiyan", "試驗"), ("Gaofen", "高分"),
    ("Jilin-1", "吉林一號"), ("Jilin", "吉林"), ("Tianhui", "天繪"), ("Chuangxin", "創新"), ("Huanjing", "環境"), ("Beidou", "北斗"),
    ("Fengyun", "風雲"), ("Tianlian", "天鏈"), ("Zhongxing", "中星"), ("Qianfan", "千帆"), ("Tanluzhe", "探路者"), ("Ziyuan", "資源"),
    ("Tianqi", "天啟"), ("Xingshidai", "星時代"), ("Guowang", "國網"), ("Tianping", "天平"), ("Tiantong", "天通"), ("Kuaizhou", "快舟"),
    ("Chang Zheng", "長征"), ("Zhuque", "朱雀"), ("Guanlan", "觀瀾"), ("Tiankun", "天鯤"), ("Shenzhou", "神舟"), ("Tianzhou", "天舟"),
    ("Pujiang", "浦江"), ("Taijing", "泰景"), ("Siwei", "四維"), ("Superview", "高景"), ("Zhangheng", "張衡"), ("Huoyan", "火眼"),
]
NUM = {"1": "一", "2": "二", "3": "三", "4": "四", "5": "五", "6": "六", "7": "七", "8": "八", "9": "九"}


def _cn_num(n: str) -> str:
    """1–99 → 中文數字（三十、五十一）；其餘保留阿拉伯數字"""
    if not n.isdigit() or not 0 < int(n) < 100:
        return n
    v = int(n)
    if v < 10:
        return NUM[str(v)]
    t, u = divmod(v, 10)
    return ("" if t == 1 else NUM[str(t)]) + "十" + (NUM[str(u)] if u else "")


def to_zh(name: str) -> str | None:
    """拼音名稱 → 中文；沒有可轉換的部分回傳 None。先換計畫名，再換「N hao M zu K xing」格式"""
    s = name.strip()
    if not s:
        return None
    if re.fullmatch(r"YG\d+(?:/YG\d+)*", s):
        return "／".join(f"遙感{_cn_num(x)}號" for x in re.findall(r"\d+", s))
    out = s
    for a, b in ZH:
        out = re.sub(rf"(?<![A-Za-z]){re.escape(a)}(?![A-Za-z]) ?", b, out)
    out = re.sub(r" ?(\d+) hao ([A-Z]) (\d+) xing", lambda m: f"{_cn_num(m.group(1))}號{m.group(2)}星{m.group(3)}星", out)
    out = re.sub(r" ?(\d+) hao (\d+) zu (\d+) xing", lambda m: f"{_cn_num(m.group(1))}號{m.group(2)}組{m.group(3)}星", out)
    out = re.sub(r" ?(\d+) hao (\d+) xing", lambda m: f"{_cn_num(m.group(1))}號{m.group(2)}星", out)
    out = re.sub(r" ?(\d+) hao\b", lambda m: f"{_cn_num(m.group(1))}號", out)
    out = re.sub(r" (\d+) xing\b", r" \1星", out)
    return out if out != s else None


# ── GCAT 表（DB 檔更新才重讀）────────────────────────────────────────────────
_g: dict[str, Any] = {"mtime": -1.0, "sat": {}, "launch": {}, "meta": None}


def gcat() -> dict[str, Any]:
    db = resolve_db()
    if db is None:
        return _g
    try:
        mt = db.stat().st_mtime
    except OSError:
        return _g
    if mt == _g["mtime"]:
        return _g
    import duckdb
    sat, launch, meta = {}, {}, None
    try:
        with duckdb.connect(str(db), read_only=True) as con:
            tabs = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
            if "gcat_sat" in tabs:
                for n, tag, piece, typ, name, pl in con.execute("SELECT norad_id, launch_tag, piece, type, name, plname FROM gcat_sat").fetchall():
                    sat[int(n)] = {"launch_tag": tag or "", "piece": piece or "", "type": typ or "", "name": name or "", "plname": pl or ""}
            if "gcat_launch" in tabs:
                for tag, d, lv, fl, mi, ag in con.execute("SELECT launch_tag, ldate, lv, flight, mission, agency FROM gcat_launch").fetchall():
                    launch[tag] = {"tag": tag, "date": d or "", "lv": lv or "", "flight": fl or "", "mission": mi or "", "agency": ag or ""}
            if "gcat_meta" in tabs:
                r = con.execute("SELECT fetched_at, sat_count, launch_count, sat_updated, launch_updated FROM gcat_meta LIMIT 1").fetchone()
                if r:
                    meta = {"fetched_at": r[0], "sat_count": r[1], "launch_count": r[2], "sat_updated": r[3], "launch_updated": r[4]}
    except Exception as e:
        logger.warning("讀取 GCAT 表失敗：%s", e)
    _g.update(mtime=mt, sat=sat, launch=launch, meta=meta)
    return _g


def _letter(intl: str) -> str:
    return intl[8:] if len(intl) > 8 else ""


def _digits(x: str) -> list[str]:
    return [d.lstrip("0") or "0" for d in re.findall(r"\d+", x)]


GCAT_URL = "https://planet4589.org/space/gcat/"
ST_URL = "https://www.space-track.org/#catalog"
PAYLOAD = ("PAYLOAD", "PAY")
ISS_TAG = "1998-067"


def suggest_one(nid: int, idx: dict, sc: dict, g: dict, mates_by_tag: dict[str, list[int]], sc_meta: dict | None) -> dict:
    """建議名稱與證據等級。

    name_status：official（Space-Track 已正式命名）／inferred（GCAT 逐顆判讀，或單一酬載發射的任務名）／
                 group_inferred（只能確認是某次發射的 N 顆之一，命名到群組）／conflict（來源互相矛盾）／unresolved
    confidence：high／medium／low；sources：每個依據的來源、網址與資料日期，套用時寫進備註以便回溯
    """
    s = idx.get(nid)
    rec = sc.get(nid) or {}
    cur = s["name"] if s else rec.get("name", "")
    intl = (intl_code(s["line1"]) if s else "") or rec.get("intl", "")
    tag, letter = intl[:8], _letter(intl)
    gs = g["sat"].get(nid)
    la = g["launch"].get(tag)
    gm = g["meta"] or {}
    mates = []
    for m in mates_by_tag.get(tag, [])[:80]:
        mr, ms, mg = sc.get(m) or {}, idx.get(m), g["sat"].get(m)
        mates.append({"norad_id": m, "intl": mr.get("intl", ""), "name": (ms or {}).get("name") or mr.get("name", ""),
                      "object_type": mr.get("object_type", ""), "gcat_name": (mg or {}).get("name", ""), "self": m == nid})
    n_pay = sum(1 for x in mates if x["object_type"] in PAYLOAD) or len(mates)
    sources, warnings = [], []
    st_src = {"source": "Space-Track SATCAT", "url": ST_URL, "date": (sc_meta or {}).get("fetched_at", "")}
    g_sat_src = {"source": "GCAT satcat（McDowell, CC BY 4.0）", "url": GCAT_URL, "date": gm.get("sat_updated", "")}
    g_la_src = {"source": "GCAT launch log（McDowell, CC BY 4.0）", "url": GCAT_URL, "date": gm.get("launch_updated", "")}
    official = rec.get("name", "")
    sc_type = rec.get("object_type", "")
    # 類型交叉驗證：SATCAT 說是酬載，GCAT 卻是火箭箭體／碎片／元件
    if gs and sc_type in PAYLOAD and gs["type"] and not gs["type"].upper().startswith("P"):
        warnings.append(f"類型不一致：SATCAT {sc_type}、GCAT {gs['type']}")
    status, conf, name, raw = "unresolved", None, None, None
    if official and not OBJ_RE.match(official) and official != cur:
        status, conf, name, raw = "official", "high", official, official
        sources.append(st_src)
        if sc_type and sc_type not in PAYLOAD:
            warnings.append(f"Space-Track 已判定非酬載（{sc_type}）")
        if gs and gs["name"] and _digits(gs["name"]) and _digits(official) and _digits(gs["name"])[:1] != _digits(official)[:1]:
            status, conf = "conflict", "medium"
            warnings.append(f"Space-Track「{official}」與 GCAT「{gs['name']}」編號不一致")
            sources.append(g_sat_src)
    elif gs and gs["name"] and not OBJ_RE.match(gs["name"].upper()):
        raw = gs["name"]
        zh = to_zh(raw)
        name = f"{zh or raw}（推論）"
        status, conf = "inferred", "medium"
        sources.append(g_sat_src)
        if la:
            sources.append(g_la_src)
    elif la and tag != ISS_TAG:
        raw = la["mission"] or la["flight"] or ""
        base = to_zh(raw) or raw
        sources.append(g_la_src)
        if not base:
            base, conf = f"{la['lv']} {la['date'][:4]} 發射", "low"
        if n_pay <= 1:
            status, conf = "inferred", conf or "medium"
            name = f"{base}（推論）"
        else:
            status, conf = "group_inferred", conf or "medium"
            name = f"{base} 群組衛星（推論）"
            warnings.append(f"同次發射 {n_pay} 顆酬載，A/B/C 與個別衛星的對應尚未建立")
    if warnings and status in ("inferred", "group_inferred") and conf == "medium":
        conf = "low"
    if s is None:
        warnings.append("不在目前 TLE 目錄")
    srcs = "；".join(f"{x['source']} {x['date']}".strip() for x in sources)
    note = ""
    if name:
        note = f"[{status}/{conf}] 依據：{srcs}" + (f"；原始名稱 {raw}" if raw and raw != name else "") +                (f"；國際編號 {intl}（發射 {tag}、序號 {letter}）" if intl else "") + (f"；注意：{'；'.join(warnings)}" if warnings else "")
    # 分層判讀：發射群組（事實）／候選任務（推論）／個體對應／任務類型與用途（一律未知，不自動歸因）
    mission_raw = (la or {}).get("mission") or (la or {}).get("flight") or ""
    if tag == ISS_TAG:  # 自國際太空站部署的立方衛星：國際編號沿用 1998-067（曙光號），發射紀錄不代表其任務
        mission_raw, la = "自國際太空站部署", None
    layers = {
        "launch_group": {"value": f"{tag}（{la['date'][:11].strip()} {la['lv']}）" if la else (f"{tag}（國際太空站部署）" if tag == ISS_TAG else (tag or None)),
                         "confidence": "high" if (la or tag == ISS_TAG) else ("medium" if tag else None)},
        "candidate_mission": {"value": (to_zh(mission_raw) or mission_raw) or None, "confidence": "medium" if mission_raw else None},
        "individual": {"value": name if status in ("official", "inferred", "conflict") else None,
                       "status": {"official": "confirmed", "inferred": "inferred", "conflict": "disputed"}.get(status, "unconfirmed")},
        "mission_type": "unknown", "capability": "unknown",
    }
    return {"norad_id": nid, "current_name": cur, "intl_code": intl, "launch_tag": tag, "piece": letter, "in_catalog": s is not None, "layers": layers,
            "object_type": sc_type, "official_name": official or None, "gcat": gs, "launch": la, "mates": mates, "payloads_in_launch": n_pay,
            "suggest": name, "raw_name": raw, "name_status": status, "confidence": conf, "sources": sources, "warnings": warnings, "note": note[:500]}


@bp.get("/api/satadmin/suggest")
def api_suggest():
    """?norad=1,2,3 指定衛星；?scope=objects 列出目錄中所有名稱為 OBJECT x 的衛星（最多 limit 筆）"""
    idx = get_sat_index()
    sc = get_satcat(fetch=False)
    g = gcat()
    if request.args.get("scope") == "objects":
        lim = min(int(request.args.get("limit", 1000)), 3000)
        ids = sorted((n for n, s in idx.items() if OBJ_RE.match(s["name"].strip().upper())), reverse=True)[:lim]
    else:
        ids = [int(x) for x in request.args.get("norad", "").replace(",", " ").split() if x.isdigit()][:500]
    tags = {}
    for n, r in sc.items():
        t = (r.get("intl") or "")[:8]
        if t:
            tags.setdefault(t, []).append(n)
    for v in tags.values():
        v.sort()
    meta = satcat_meta()
    rows = [suggest_one(n, idx, sc, g, tags, meta) for n in ids]
    cnt = {k: sum(1 for r in rows if r["name_status"] == k) for k in ("official", "inferred", "group_inferred", "conflict", "unresolved")}
    return jsonify({"results": rows, "count": len(rows), "counts": cnt, "satcat_loaded": bool(sc), "gcat": g["meta"],
                    "credit": "GCAT: Jonathan C. McDowell, General Catalog of Artificial Space Objects, planet4589.org (CC-BY)"})
