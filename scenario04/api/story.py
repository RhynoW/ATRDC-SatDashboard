"""StoryMaps 式敘事頁：故事定義存於 config/stories/*.json，通用渲染器呈現。

路由：
  GET /story             故事清單頁
  GET /story/<sid>       單一故事頁（story.html + story.js 依 JSON 渲染）
  GET /api/story/list    故事清單（id/title/subtitle/updated）
  GET /api/story/<sid>   完整故事 JSON
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from flask import Blueprint, render_template, request

from . import json_response
from ..config import settings

bp = Blueprint("story", __name__)
logger = logging.getLogger(__name__)

STORIES_DIR = settings.PACKAGE_DIR / "config" / "stories"
SCHEMA_FILE = STORIES_DIR / "schema.json"
_SID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_schema_cache: dict | None = None


def _validate(d: dict, name: str) -> bool:
    """以 schema.json 驗證故事；不合者記 WARNING 並略過（避免壞檔拖垮整個清單）。"""
    global _schema_cache
    try:
        import jsonschema
    except ImportError:
        return True
    if _schema_cache is None:
        _schema_cache = json.loads(SCHEMA_FILE.read_text(encoding="utf-8")) if SCHEMA_FILE.exists() else {}
    if not _schema_cache:
        return True
    try:
        jsonschema.validate(d, _schema_cache)
        return True
    except jsonschema.ValidationError as exc:
        logger.warning("story %s 不符 schema：%s（路徑 %s）", name, exc.message, list(exc.absolute_path))
        return False


def _load_all() -> list[dict]:
    out = []
    if not STORIES_DIR.is_dir():
        return out
    for p in sorted(STORIES_DIR.glob("*.json")):
        if p.name == "schema.json":
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("id") and _validate(d, p.name):
                out.append(d)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("story 檔載入失敗 %s: %s", p.name, exc)
    return out


def _find(sid: str) -> dict | None:
    if not _SID_RE.match(sid):
        return None
    for d in _load_all():
        if d["id"] == sid:
            return d
    return None


@bp.get("/story")
def story_index():
    return render_template("story.html", story_id="")


@bp.get("/story/<sid>")
def story_page(sid: str):
    return render_template("story.html", story_id=sid if _SID_RE.match(sid) else "")


@bp.get("/api/story/list")
def api_story_list():
    return json_response([
        {"id": d["id"], "title": d.get("title", d["id"]),
         "subtitle": d.get("subtitle", ""), "updated": d.get("updated", "")}
        for d in _load_all()
    ])


@bp.get("/api/story/<sid>")
def api_story(sid: str):
    d = _find(sid)
    if d is None:
        return json_response({"error": f"story '{sid}' 不存在"}), 404
    return json_response(d)


# ── 整合展示群組（constellation 標籤 或 名稱正則）─────────────────────────────
GROUPS: dict[str, dict] = {
    "gps":      {"label": "GPS",            "regex": r"^NAVSTAR"},
    "beidou":   {"label": "北斗/BeiDou",     "regex": r"^BEIDOU"},
    "starlink": {"label": "Starlink",       "constellation": "Starlink"},
    "oneweb":   {"label": "OneWeb",         "constellation": "OneWeb"},
    # 口徑對齊 USSF Space Threat Fact Sheet／UPI：具光學、多光譜、雷達、射頻感測器之
    # 軍用＋民用＋商業遙感衛星（519）＋北斗導航（62）＝ 581（2026-08 盤點）
    "prc_isr":  {"label": "大陸 ISR/遙感（含北斗）", "country": "中國",
                 "regex": r"^YAOGAN|^GAOFEN|^GF-|JILIN|^JL-?1|^TIANHUI|^TH-|^HAIYANG|^HY-"
                          r"|^ZIYUAN|^ZY-|^ZY |^HUANJING|^HJ-|^HJS|^LUDI|^SHIYAN|^SY-|^SHIJIAN|^SJ-|^SJ "
                          r"|^TJS|^YUNHAI|^NINGXIA|^FENGYUN|^FY-|^TIANMU|^YUNYAO"
                          r"|ZHUHAI|OVS-|OHS-|^BEIJING|BJ-|TAIJING|SIWEI|SUPERVIEW|GAOJING|^TIANYI|^TY-"
                          r"|HEAD-|^HEAD|CHAOHU|HISEA|^CHUANG|^STTW|^GJZ|^KL-|^KL |^CBERS|^PIESAT|^HONGTU"
                          r"|^BEIDOU"},
    "prc_comm": {"label": "大陸通訊星系",      "country": "中國",
                 "regex": r"QIANFAN|SPACESAIL|HULIANWANG|GUOWANG|SATNET|CHINASAT|ZHONGXING|APSTAR|TIANLIAN|TIANTONG"},
}
_EXCL_PURPOSE = {"碎片", "火箭體"}


def group_members(idx: dict, key: str) -> list[int]:
    g = GROUPS[key]
    rx = re.compile(g["regex"], re.I) if g.get("regex") else None
    out = []
    for n, i in idx.items():
        if i.get("purpose") in _EXCL_PURPOSE:
            continue
        if g.get("country") and i.get("country") != g["country"]:
            continue
        if g.get("constellation") and i.get("constellation") != g["constellation"]:
            continue
        if rx and not rx.search(i.get("name", "")):
            continue
        out.append(n)
    return out


def _tle_elements(line2: str):
    """由 TLE line2 取傾角(°)與平均高度(km)。"""
    try:
        inc = float(line2[8:16]); mm = float(line2[52:63])
        a = (398600.4418 / (mm * 2 * 3.141592653589793 / 86400.0) ** 2) ** (1 / 3)
        return inc, a - 6378.137
    except Exception:  # noqa: BLE001
        return None, None


@bp.get("/api/story/groups")
def api_story_groups():
    from ..ingestion.index import get_sat_index
    idx = get_sat_index()
    return json_response({k: {"label": g["label"], "n": len(group_members(idx, k))}
                          for k, g in GROUPS.items()})


@bp.get("/api/story/group_stats")
def api_story_group_stats():
    """群組儀表板：數量、高度／傾角分佈、發射年份歷史、代表衛星。"""
    from collections import Counter
    from ..ingestion.index import get_sat_index
    from ..ingestion.metadata import load_sat_metadata_csv
    key = request.args.get("group", "")
    if key not in GROUPS:
        return json_response({"error": f"group 需為 {list(GROUPS)}"}), 400
    idx = get_sat_index()
    ids = group_members(idx, key)
    meta = load_sat_metadata_csv()
    alt_bins = Counter(); inc_bins = Counter(); years = Counter(); regimes = Counter()
    alts = []
    for n in ids:
        inc, alt = _tle_elements(idx[n].get("line2", ""))
        if alt is None:
            continue
        alts.append(alt)
        alt_bins[int(alt // 100) * 100 if alt < 2000 else (20000 if alt < 30000 else 35000)] += 1
        inc_bins[int(inc // 10) * 10] += 1
        regimes["LEO" if alt < 2000 else ("MEO" if alt < 30000 else "GEO/IGSO")] += 1
        ld = (meta.get(n) or {}).get("launch_date") or (meta.get(n) or {}).get("launch") or ""
        if len(ld) >= 4 and ld[:4].isdigit():
            years[ld[:4]] += 1
    sample = sorted(ids)[-8:][::-1]
    return json_response({
        "group": key, "label": GROUPS[key]["label"], "n": len(ids),
        "regimes": dict(regimes),
        "alt_hist": dict(sorted(alt_bins.items())),
        "inc_hist": dict(sorted(inc_bins.items())),
        "launch_years": dict(sorted(years.items())),
        "alt_median": round(sorted(alts)[len(alts) // 2], 1) if alts else None,
        "sample": [{"norad": n, "name": idx[n]["name"]} for n in sample],
    })


@bp.get("/api/story/maneuvers")
def api_story_maneuvers():
    """今年度機動偵測成果（tools/build_maneuvers_2026.py 預算之靜態 JSON）。"""
    p = STORIES_DIR / "data" / "maneuvers_2026.json"
    if not p.exists():
        return json_response({"error": "尚未預算（執行 tools/build_maneuvers_2026.py）"}), 404
    return json_response(json.loads(p.read_text(encoding="utf-8")), max_age=3600)


# ── 偵照衛星感測器／光學解析度分類（型號級別、公開文獻推估；首個符合規則者為準）──
# res: 光學最佳公開解析度級別；sensor: 光學/SAR/射頻訊號/氣象掩星/技術試驗/導航
ISR_RES_RULES: list[tuple[str, str, str, str, str]] = [
    # (系列, regex, sensor, res_class, 註記)
    ("遙感-30/20/25/31/39/40 三星組", r"^YAOGAN[- ]?(30|20|25|31|39|40)\b", "射頻訊號", "—", "海洋目標電子偵察三星組（公開分析）"),
    ("遙感 SAR 型（-1/3/6/10/13/18/23/29/33）", r"^YAOGAN[- ]?(1|3|6|10|13|18|23|29|33)\b", "SAR", "≈1 m（推估）", "公開分析歸類為雷達型"),
    ("遙感-41（GEO 光學）", r"^YAOGAN[- ]?41\b", "光學", "1–5 m", "地球同步高軌光學（2023）"),
    ("遙感 光學型（其餘）", r"^YAOGAN", "光學", "≤0.5 m（推估）", "軍用高解析光學，官方未公開"),
    ("高分-11", r"^GAOFEN[- ]?11\b", "光學", "≤0.5 m", "公開報導約 0.1–0.5 m 級"),
    ("高分-2/7/14（0.5–1 m）", r"^GAOFEN[- ]?(2|7|14)\b", "光學", "0.5–1 m", "GF-2 0.8 m、GF-7 0.65 m 立體"),
    ("高分-3/12（SAR）", r"^GAOFEN[- ]?(3|12)\b", "SAR", "≈1 m", "C 波段 1 m 條帶"),
    ("高分-4（GEO 凝視）", r"^GAOFEN[- ]?4\b", "光學", ">30 m", "GEO 50 m 可見光／400 m 紅外"),
    ("高分-5（高光譜）", r"^GAOFEN[- ]?5\b", "光學", "5–30 m", "高光譜 30 m"),
    ("高分-1/6/13 等", r"^GAOFEN|^GF-", "光學", "1–5 m", "2 m 全色／8 m 多光譜（寬幅 16 m）"),
    ("吉林一號 寬幅/高分 03/04", r"JILIN|^JL-?1", "光學", "0.5–1 m", "GF03 0.75 m、寬幅 01 0.5 m"),
    ("四維／SuperView（高景）", r"SIWEI|SUPERVIEW|GAOJING", "光學", "≤0.5 m", "0.5 m（高景一號）/0.42 m"),
    ("北京三號", r"^BEIJING|BJ-", "光學", "≤0.5 m", "0.5 m 敏捷"),
    ("珠海一號 OVS（視頻）", r"OVS-", "光學", "0.5–1 m", "0.9 m 視頻"),
    ("珠海一號 OHS（高光譜）", r"OHS-|ZHUHAI", "光學", "5–30 m", "10 m 高光譜"),
    ("天繪-1（測繪）", r"^TIANHUI[- ]?1|^TH-1", "光學", "1–5 m", "2 m 全色／5 m 立體"),
    ("天繪-2（SAR 干涉）", r"^TIANHUI[- ]?2|^TH-2", "SAR", "≈3 m", "雙星 InSAR"),
    ("天繪 其他", r"^TIANHUI|^TH-", "光學", "1–5 m", "測繪"),
    ("資源三號／CBERS", r"^ZIYUAN|^ZY-|^ZY |^CBERS", "光學", "1–5 m", "ZY-3 2.1 m 立體"),
    ("環境／陸地探測", r"^HUANJING|^HJ-|^HJS|^LUDI", "光學", "5–30 m", "HJ 16–30 m；陸地探測一號為 L 波段 SAR"),
    ("海絲／巢湖（商業 SAR）", r"HISEA|CHAOHU", "SAR", "≈1 m", "商業 C 波段"),
    ("海洋系列", r"^HAIYANG|^HY-", "光學", ">30 m", "海洋水色 250 m–1 km（HY-2 為微波）"),
    ("風雲／雲海（氣象）", r"^FENGYUN|^FY-|^YUNHAI", "氣象掩星", "—", "氣象成像 250 m–1 km 級"),
    ("天目／雲遙（GNSS 掩星）", r"^TIANMU|^YUNYAO", "氣象掩星", "—", "無成像"),
    ("寧夏一號（射頻）", r"^NINGXIA", "射頻訊號", "—", "商業電子偵察星座（公開分析）"),
    ("TJS 通信技術試驗（GEO）", r"^TJS", "射頻訊號", "—", "疑似 GEO 訊號情報／早期預警"),
    ("實驗／實踐（技術試驗）", r"^SHIYAN|^SY-|^SHIJIAN|^SJ-|^SJ ", "技術試驗", "—", "載荷多未公開"),
    ("其他商業 EO", r"^TIANYI|^TY-|HEAD-|^HEAD|^CHUANG|^STTW|^GJZ|^KL-|^KL |^PIESAT|^HONGTU|TAIJING", "光學", "1–5 m", "型號各異"),
    ("北斗（導航）", r"^BEIDOU", "導航", "—", "無成像感測器"),
]
_RES_ORDER = ["≤0.5 m", "≤0.5 m（推估）", "0.5–1 m", "1–5 m", "5–30 m", ">30 m"]


@bp.get("/api/story/isr_resolution")
def api_story_isr_resolution():
    """群組之感測器類型與光學解析度級別統計（型號級別公開推估）。"""
    from collections import Counter
    from ..ingestion.index import get_sat_index
    key = request.args.get("group", "prc_isr")
    if key not in GROUPS:
        return json_response({"error": f"group 需為 {list(GROUPS)}"}), 400
    idx = get_sat_index()
    rules = [(s, re.compile(rx, re.I), sensor, res, note) for s, rx, sensor, res, note in ISR_RES_RULES]
    sensor_c, res_c, series_c = Counter(), Counter(), Counter()
    unknown = 0
    for n in group_members(idx, key):
        nm = idx[n]["name"]
        for s, rx, sensor, res, note in rules:
            if rx.search(nm):
                sensor_c[sensor] += 1; series_c[s] += 1
                if sensor in ("光學",):
                    res_c[res] += 1
                elif sensor == "SAR":
                    res_c["SAR " + res] += 1
                break
        else:
            unknown += 1
    series = [{"series": s, "n": series_c[s], "sensor": sensor, "res": res, "note": note}
              for s, _rx, sensor, res, note in rules if series_c[s]]
    res_sorted = sorted(res_c.items(), key=lambda kv: (
        _RES_ORDER.index(kv[0]) if kv[0] in _RES_ORDER else 90 + (0 if kv[0].startswith("SAR") else 1)))
    return json_response({
        "group": key, "label": GROUPS[key]["label"],
        "sensor": dict(sensor_c.most_common()), "resolution": dict(res_sorted),
        "series": sorted(series, key=lambda x: -x["n"]), "unknown": unknown,
        "note": "分類為型號級別之公開文獻推估（Gunter's Space Page、公開分析），非官方規格；軍用遙感系列之解析度為推估。",
    })


_RADAR_CACHE: dict = {}


@bp.get("/api/story/radar_eval")
def api_story_radar_eval():
    """台灣假想雷達站效益評估：group=prc_isr（預設）、n=樣本數（≤60）、hours、mask。"""
    import time as _time
    from ..ingestion.index import get_sat_index
    from ..physics.radar_eval import evaluate
    key = request.args.get("group", "prc_isr")
    if key not in GROUPS:
        return json_response({"error": f"group 需為 {list(GROUPS)}"}), 400
    n = max(5, min(int(request.args.get("n", 40)), 60))
    hours = max(6.0, min(float(request.args.get("hours", 24)), 48.0))
    mask = max(0.0, min(float(request.args.get("mask", 5)), 30.0))
    ck = (key, n, hours, mask)
    hit = _RADAR_CACHE.get(ck)
    if hit and _time.monotonic() - hit[0] < 1800:
        return json_response(hit[1])
    idx = get_sat_index()
    ids = group_members(idx, key)
    # 只取 LEO（GEO 對地面站幾何恆定，評估無意義）；取 NORAD 由大到小（較新）之前 n 顆
    sats = []
    for nid in sorted(ids, reverse=True):
        inc, alt = _tle_elements(idx[nid].get("line2", ""))
        if alt is not None and alt < 2000 and idx[nid].get("line1"):
            sats.append((nid, idx[nid]["name"], idx[nid]["line1"], idx[nid]["line2"]))
        if len(sats) >= n:
            break
    res = evaluate(sats, hours=hours, mask_deg=mask)
    res["group"] = key; res["label"] = GROUPS[key]["label"]
    _RADAR_CACHE[ck] = (_time.monotonic(), res)
    return json_response(res)


@bp.get("/api/story/track")
def api_story_track():
    """台灣站對指定衛星之未來過頂 az/el 序列（norad 必填）。"""
    from ..ingestion.index import get_sat_index
    from ..physics.radar_eval import track_passes
    try:
        nid = int(request.args.get("norad", ""))
    except ValueError:
        return json_response({"error": "norad 必填"}), 400
    info = get_sat_index().get(nid)
    if not info or not info.get("line1"):
        return json_response({"error": f"NORAD {nid} 無 TLE"}), 404
    res = track_passes(info["line1"], info["line2"])
    res["norad"] = nid; res["name"] = info["name"]
    return json_response(res)


@bp.get("/api/story/positions")
def api_story_positions():
    """故事頁世界地圖用之衛星即時位置。

    Query params:
        mode  all | country | constellation | purpose | ids
        val   mode=country/constellation/purpose 之值；mode=ids 時為逗號分隔 NORAD
    Returns: sats=[[norad, lat, lon, alt_km], …]、names（≤60 顆時附名稱）
    """
    from ..ingestion.index import get_sat_index
    from ..physics.propagator_cache import get_cache
    from ..physics.propagate import propagate_batch

    mode = request.args.get("mode", "all").strip()
    val = request.args.get("val", "").strip()
    idx = get_sat_index()

    if mode == "ids":
        ids = [int(x) for x in val.split(",") if x.strip().isdigit()]
        sel = [n for n in ids if n in idx]
    elif mode == "group":
        if val not in GROUPS:
            return json_response({"error": f"group 需為 {list(GROUPS)}"}), 400
        sel = group_members(idx, val)
    elif mode in ("country", "constellation", "purpose"):
        if not val:
            return json_response({"error": "val 不可空白"}), 400
        sel = [n for n, i in idx.items() if i.get(mode) == val]
    elif mode == "all":
        sel = [n for n, i in idx.items() if i.get("line1")]
    else:
        return json_response({"error": "mode 必須為 all/country/constellation/purpose/ids"}), 400

    cache = get_cache()
    snap = cache.get_snapshot(sel) if cache.ready else {}
    missing = [n for n in sel if n not in snap]
    if missing and (len(missing) <= 2000 or not snap):
        for nid, pos in zip(missing, propagate_batch(missing, idx)):
            if pos is not None:
                snap[nid] = {"lat": round(pos[0], 4), "lon": round(pos[1], 4),
                             "alt_km": round(pos[2], 1)}

    sats = [[n, snap[n]["lat"], snap[n]["lon"], snap[n]["alt_km"]]
            for n in sel if n in snap]
    # 名稱：除全目錄（3 萬筆）外一律附上，供 3D 圖標名（前端自行控制標籤密度）
    names = ({str(n): idx[n]["name"] for n in sel if n in snap}
             if mode != "all" else {})
    from datetime import datetime, timezone
    return json_response({"mode": mode, "val": val, "count": len(sats),
                          "sats": sats, "names": names,
                          "timestamp": datetime.now(timezone.utc).isoformat()})


# ── 資料口徑（provenance）：供故事頁「口徑列」與 Markdown 匯出 ───────────────
def provenance() -> dict:
    """資料來源、TLE epoch 範圍／資料齡、傳播模型、座標系、不確定性假設。"""
    from datetime import datetime, timezone
    from ..ingestion.db import get_db_info
    try:
        import sgp4
        sgp4_ver = getattr(sgp4, "__version__", "?")
    except Exception:  # noqa: BLE001
        sgp4_ver = "?"
    info = get_db_info()
    # 資料齡以「不晚於現在之最新 epoch」計（GEO 等常見未來 epoch，直接用 max 會得到負值）
    age = None
    latest_past = None
    try:
        from ..ingestion.db import resolve_db
        import duckdb
        db = resolve_db()
        if db is not None:
            with duckdb.connect(str(db), read_only=True) as con:
                latest_past = con.execute(
                    f"SELECT max(epoch_utc) FROM {settings.RAW_TABLE} WHERE epoch_utc <= now()").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("provenance latest_past 查詢失敗: %s", exc)
    # 目錄／有效衛星數拆分：≤7 天內有 TLE 者視為「可用於目前傳播」
    n_fresh7 = None
    try:
        if db is not None:
            with duckdb.connect(str(db), read_only=True) as con:
                n_fresh7 = con.execute(
                    f"SELECT count(DISTINCT norad_id) FROM {settings.RAW_TABLE} "
                    "WHERE epoch_utc >= now() - INTERVAL 7 DAY AND epoch_utc <= now() + INTERVAL 1 DAY").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("provenance n_fresh7 查詢失敗: %s", exc)
    # 程式版本（git commit）
    commit = None
    try:
        import subprocess
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(settings.APP_DIR),
                                capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:  # noqa: BLE001
        commit = None
    ref = latest_past or info.get("epoch_max")
    if ref:
        try:
            em = ref if isinstance(ref, datetime) else datetime.fromisoformat(str(ref))
            if em.tzinfo is None:
                em = em.replace(tzinfo=timezone.utc)
            age = round((datetime.now(timezone.utc) - em).total_seconds() / 86400.0, 1)
            latest_past = em.isoformat(timespec="seconds")
        except ValueError:
            pass
    return {
        "source": "Space-Track GP（公開 TLE）；本系統 DuckDB 每日彙整",
        "db_name": info.get("db_name"), "db_updated_at": info.get("db_updated_at"),
        "tle_epoch_min": info.get("epoch_min"), "tle_epoch_max": info.get("epoch_max"),
        "tle_epoch_latest_past": latest_past, "tle_age_days": age,
        "catalog_sat_count": info.get("valid_sat_count"),       # 去重 NORAD 數（含歷史）
        "tle_record_count": info.get("total_records"),          # 所有 TLE 筆數
        "fresh_sat_count_7d": n_fresh7,                         # ≤7 天內有 TLE，可用於目前傳播
        "valid_sat_count": info.get("valid_sat_count"),         # 舊欄位（相容）
        "app_commit": commit,
        "propagator": f"SGP4/SDP4（python-sgp4 {sgp4_ver}）",
        "frame": "SGP4 輸出 TEME；以 UTC 近似 GMST 作 TEME→ECEF 旋轉，再依 WGS-84 橢球求地理經緯度與大地高；"
                 "未納入極移、章動、UT1−UTC 與完整 ITRF 地球定向參數（地面位置屬態勢展示等級）",
        "accuracy": "公開 TLE 級（LEO 沿軌 1–3 km/日量級增長），非精密星曆；不宜作為操作級決策依據",
        "status": "技術展示／非操作級",
        "pc_model": "Chan (2008) 2-D 近似；σ_R/T/N = "
                    f"{settings.SIGMA_R_KM*1000:.0f}/{settings.SIGMA_T_KM*1000:.0f}/{settings.SIGMA_N_KM*1000:.0f} m "
                    "為固定假設值（非 CDM 協方差），Pc 僅供排序",
        "maneuver_method": "相鄰 TLE 半長軸跳變 |Δa| 門檻（LEO 0.5 km、MEO/GEO 2 km，間隔 ≤5 天）之候選事件；"
                           "Δv 由 Δa 以 Δv≈n·Δa/2 換算之等效值；替代解釋：TLE 品質波動、阻力模型誤差、資料缺漏",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


@bp.get("/api/story/provenance")
def api_story_provenance():
    return json_response(provenance(), max_age=300)
