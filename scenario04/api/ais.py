"""AIS 船舶追蹤：伺服器端合併兩個來源的船位，提供給地球儀（前端 web/static/js/ais.js）。

來源
- aisstream.io（WebSocket 串流）：全球志工接收站，台灣周邊稀疏。預設只訂閱台灣與東亞
  （DEFAULT_BBOX，可用 AIS_BBOX 覆寫）。aisstream 不允許瀏覽器直連，由本伺服器持有連線。
  連線開 permessage-deflate（2026-09 起未壓縮連線有頻寬上限）；握手逾時 45 秒（實測 TLS 約 1.5 秒，
  101 升級回應要再等約 13 秒）；不主動 ping（aisstream 不一定及時回 pong，實測被判逾時斷線），
  改用 NO_DATA_SEC 看門狗；斷線以指數退避＋抖動重連。每段連線長度記在 sessions，用來看斷線頻率。
- 航港局「臺灣海域船舶動態資訊系統」（https://mpbais.motcmpb.gov.tw/aismpb/）：網頁本身使用的
  GeoJSON 端點 tools/geojsonais.ashx，台灣周邊約 8,000～9,000 艘，每 60 秒輪詢一次。
  這不是政府資料開放平台上的正式資料集，且該端點只對自家網頁（Referer）回資料；HF Space 等海外主機
  也連不到（實測逾時）。因此線上版預設關閉（AIS_MPB=1 啟用），Rust 單機版預設開啟。

共通
- 有人開 AIS 圖層（/api/ais/ships、/api/ais/ship）才連線／輪詢；閒置 AIS_IDLE_SEC（預設 600 秒）停止。
- 以 MMSI 合併：位置以較新的回報為準（航港局用其 Record_Time，台灣時間）；靜態欄位（船名、船型…）
  aisstream 有就用 aisstream，缺的才用航港局補。每艘記錄各來源最後收到時間（src）。
- 船位只存在記憶體；超過 1 小時沒收到即移除；航跡保留 6 小時（每艘至多每 60 秒一點）。

金鑰：環境變數 AISSTREAM_API_KEY（HF Secret），或本機 scenario-advanced01/aisstream_key.txt（不提交、不打包）。
其他環境變數：AIS_BBOX（JSON，[[[lat,lon],[lat,lon]], …]）、AIS_MPB（1 啟用航港局來源，預設關閉）、AIS_IDLE_SEC。
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request

from . import json_response

logger = logging.getLogger(__name__)
bp = Blueprint("ais", __name__)

WS_URL = "wss://stream.aisstream.io/v0/stream"
MSG_TYPES = ["PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport",
             "ShipStaticData", "StaticDataReport"]
MPB_URL = "https://mpbais.motcmpb.gov.tw/aismpb/tools/geojsonais.ashx"
MPB_REFERER = "https://mpbais.motcmpb.gov.tw/aismpb/"
MPB_INTERVAL = 60         # 航港局輪詢間隔（同其網頁的自動更新）
STALE_SEC = 3600          # 超過 1 小時沒收到 → 移除
TRACK_SEC = 6 * 3600      # 航跡保留 6 小時
TRACK_STEP = 60           # 航跡每艘至多每 60 秒記一點
MAX_SHIPS = 5000          # 單次回傳上限
NO_DATA_SEC = 120         # aisstream 連線中超過此秒數沒收到任何資料 → 視為失聯重連
HDG_KEEP_SEC = 300        # 航港局沒有艏向；較新的航港局位置沿用 5 分鐘內 aisstream 的艏向
DEFAULT_BBOX = [[[0.0, 100.0], [50.0, 150.0]]]   # 台灣與東亞（緯 0～50°N、經 100～150°E）
SRC_CODE = {"aisstream": "A", "mpb": "M"}
TW = timezone(timedelta(hours=8))


def _bbox_env():
    raw = os.getenv("AIS_BBOX", "").strip()
    if not raw:
        return DEFAULT_BBOX
    try:
        v = json.loads(raw)
        assert isinstance(v, list) and v and all(len(b) == 2 and all(len(c) == 2 for c in b) for b in v)
        return v
    except Exception:  # noqa: BLE001
        logger.warning("AIS_BBOX 格式錯誤，改用預設（台灣與東亞）：%s", raw[:80])
        return DEFAULT_BBOX


def _load_key() -> str:
    """金鑰：環境變數 AISSTREAM_API_KEY（HF Secret）優先，其次本機 scenario-advanced01/aisstream_key.txt（已列入 .gitignore／.dockerignore）。"""
    k = os.getenv("AISSTREAM_API_KEY", "").strip()
    if k:
        return k
    from pathlib import Path
    f = Path(__file__).resolve().parents[2] / "aisstream_key.txt"
    try:
        return f.read_text(encoding="utf-8").strip() if f.is_file() else ""
    except OSError:
        return ""


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _clean(s):
    return " ".join(str(s).replace("@", " ").split()) if s else ""


def _mpb_time(s) -> float | None:
    try:
        return datetime.strptime(str(s).strip(), "%Y/%m/%d %H:%M:%S").replace(tzinfo=TW).timestamp()
    except (TypeError, ValueError):
        return None


def _new_src() -> dict:
    return {"state": "idle", "error": "", "msgs": 0, "last_msg": 0.0, "since": 0.0,
            "reconnects": 0, "sessions": deque(maxlen=20)}


class AisHub:
    """兩個 AIS 來源的連線／輪詢與合併後的船舶狀態（執行緒安全）。"""

    def __init__(self, key: str | None = None, connector=None, mpb: bool | None = None, mpb_fetch=None):
        self.key = key if key is not None else _load_key()
        self.bbox = _bbox_env()
        self.idle_sec = float(os.getenv("AIS_IDLE_SEC", "600"))
        self.mpb_on = (os.getenv("AIS_MPB", "0").strip() == "1") if mpb is None else mpb
        self._connect = connector          # 測試注入；None → websockets
        self._mpb_fetch = mpb_fetch        # 測試注入；None → requests
        self.lock = threading.Lock()
        self.ships: dict[int, dict] = {}
        self.tracks: dict[int, deque] = {}
        self.threads: dict[str, threading.Thread] = {}
        self.src = {"aisstream": _new_src(), "mpb": _new_src()}
        self.last_access = 0.0
        self._last_prune = 0.0

    # ── 相容舊介面（測試與狀態）──
    @property
    def state(self) -> str:
        return self.src["aisstream"]["state"]

    @property
    def error(self) -> str:
        return self.src["aisstream"]["error"]

    @property
    def thread(self):
        return self.threads.get("aisstream")

    # ── 對外 ──
    def touch(self) -> None:
        self.last_access = time.time()
        with self.lock:
            for name, ok, fn in (("aisstream", bool(self.key), self._run_ws), ("mpb", self.mpb_on, self._run_mpb)):
                th = self.threads.get(name)
                if ok and (th is None or not th.is_alive()):
                    self.src[name]["state"] = "connecting"
                    th = self.threads[name] = threading.Thread(target=fn, name="ais-" + name, daemon=True)
                    th.start()

    def _src_status(self, name: str) -> dict:
        s = self.src[name]
        st = s["state"]
        if name == "aisstream" and not self.key:
            st = "no_key"
        ses = list(s["sessions"])
        return {"state": st, "error": s["error"], "msgs": s["msgs"], "last_msg": s["last_msg"] or None,
                "since": s["since"] or None, "reconnects": s["reconnects"],
                "sessions": [round(x) for x in ses],
                "avg_session": round(sum(ses) / len(ses)) if ses else None}

    def status(self) -> dict:
        with self.lock:
            n = len(self.ships)
        srcs = {"aisstream": self._src_status("aisstream")}
        if self.mpb_on:
            srcs["mpb"] = self._src_status("mpb")
        states = [v["state"] for v in srcs.values()]
        overall = next((s for s in ("live", "connecting", "retry", "idle") if s in states), "no_key")
        a = srcs["aisstream"]
        return {"state": overall, "has_key": bool(self.key), "key_settable": False, "ships": n,
                "msgs": a["msgs"], "last_msg": a["last_msg"], "since": a["since"], "error": a["error"],
                "idle_sec": self.idle_sec, "bbox": self.bbox, "sources": srcs,
                "source": "+".join(srcs)}

    def query(self, w, s, e, n, moving=False, limit=MAX_SHIPS, srcs: set | None = None) -> dict:
        now = time.time()
        self._prune(now)
        out = []
        with self.lock:
            total = len(self.ships)
            for sh in self.ships.values():
                lat, lon = sh["lat"], sh["lon"]
                if not (s <= lat <= n):
                    continue
                if (w <= e and not (w <= lon <= e)) or (w > e and not (lon >= w or lon <= e)):
                    continue
                if moving and not _is_moving(sh):
                    continue
                if srcs is not None and not (srcs & sh.get("src", {}).keys()):
                    continue
                out.append(sh)
        out.sort(key=lambda x: -x["t"])
        trunc = len(out) > limit
        rows = [[x["mmsi"], round(x["lat"], 5), round(x["lon"], 5), x.get("cog"), x.get("hdg"), x.get("sog"),
                 x.get("type"), x.get("name", ""), x.get("nav"),
                 "".join(SRC_CODE[k] for k in SRC_CODE if k in x.get("src", {}))] for x in out[:limit]]
        return {**self.status(), "total": total, "n": len(rows), "truncated": trunc,
                "cols": ["mmsi", "lat", "lon", "cog", "hdg", "sog", "type", "name", "nav", "src"], "rows": rows}

    def ship(self, mmsi: int) -> dict | None:
        with self.lock:
            sh = self.ships.get(mmsi)
            if not sh:
                return None
            tr = [list(p) for p in self.tracks.get(mmsi, ())]
            return {**sh, "src": dict(sh.get("src", {})), "track": tr}

    # ── 共用：寫入位置（呼叫端持鎖）──
    def _put_pos(self, mmsi, lat, lon, t, src, sog=None, cog=None, hdg=None, nav=None, nav_set=False, cls=None):
        sh = self.ships.get(mmsi)
        if sh is None:
            sh = self.ships[mmsi] = {"mmsi": mmsi, "t": 0.0}
        sh.setdefault("src", {})[src] = max(t, sh.get("src", {}).get(src, 0.0))
        if t < sh["t"]:
            return sh, False             # 較舊的回報不蓋掉較新的位置
        keep_hdg = src == "mpb" and sh.get("hdg_t", 0) and t - sh["hdg_t"] <= HDG_KEEP_SEC
        sh.update(lat=lat, lon=lon, t=t, sog=sog, cog=cog)
        if src == "aisstream":
            sh["hdg"], sh["hdg_t"] = hdg, t
        elif not keep_hdg:
            sh["hdg"] = None
        if nav_set:
            sh["nav"] = nav
        if cls:
            sh["cls"] = cls
        tr = self.tracks.setdefault(mmsi, deque(maxlen=TRACK_SEC // TRACK_STEP + 1))
        if not tr or t - tr[-1][0] >= TRACK_STEP:
            tr.append((round(t), round(lat, 5), round(lon, 5)))
        return sh, True

    # ── aisstream 訊息（可單獨測試）──
    def handle(self, msg: dict, now: float | None = None) -> None:
        now = now or time.time()
        mt = msg.get("MessageType")
        body = (msg.get("Message") or {}).get(mt) or {}
        meta = {k.lower(): v for k, v in (msg.get("MetaData") or {}).items()}
        mmsi = meta.get("mmsi") or body.get("UserID")
        try:
            mmsi = int(mmsi)
        except (TypeError, ValueError):
            return
        if mmsi <= 0:
            return
        s = self.src["aisstream"]
        s["msgs"] += 1
        s["last_msg"] = now
        with self.lock:
            sh = self.ships.get(mmsi)
            if mt in ("PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"):
                lat, lon = _num(body.get("Latitude")), _num(body.get("Longitude"))
                if lat is None or lon is None:
                    lat, lon = _num(meta.get("latitude")), _num(meta.get("longitude"))
                if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
                    return
                if body.get("Valid") is False:
                    return
                sog, cog, hdg = _num(body.get("Sog")), _num(body.get("Cog")), _num(body.get("TrueHeading"))
                nav = _num(body.get("NavigationalStatus")) if mt == "PositionReport" else None
                sh, _ = self._put_pos(
                    mmsi, lat, lon, now, "aisstream",
                    sog=None if sog is None or sog >= 102.3 else round(sog, 1),
                    cog=None if cog is None or cog >= 360 else round(cog, 1),
                    hdg=None if hdg is None or hdg >= 360 else int(hdg),
                    nav=None if nav is None or nav == 15 else int(nav), nav_set=mt == "PositionReport",
                    cls="A" if mt == "PositionReport" else "B")
                if mt == "ExtendedClassBPositionReport":
                    self._static(sh, body)
            elif mt in ("ShipStaticData", "StaticDataReport"):
                if sh is None:
                    return              # 沒有位置的靜態資料不留（等位置到再補，靜態約 6 分鐘重送一次）
                if mt == "StaticDataReport":
                    a, b = body.get("ReportA") or {}, body.get("ReportB") or {}
                    if a.get("Valid"):
                        self._static(sh, {"Name": a.get("Name")})
                    if b.get("Valid"):
                        self._static(sh, {"Type": b.get("ShipType"), "CallSign": b.get("CallSign"),
                                          "Dimension": b.get("Dimension")})
                else:
                    self._static(sh, body)
            if sh is not None and not sh.get("name"):
                nm = _clean(meta.get("shipname"))
                if nm:
                    sh["name"] = nm

    # ── 航港局 GeoJSON（可單獨測試）──
    def handle_mpb(self, data: dict) -> int:
        n = 0
        with self.lock:
            for f in (data or {}).get("features") or []:
                p = f.get("properties") or {}
                try:
                    mmsi = int(str(p.get("MMSI", "")).strip())
                except ValueError:
                    continue
                lat, lon, t = _num(p.get("Latitude")), _num(p.get("Longitude")), _mpb_time(p.get("Record_Time"))
                if mmsi <= 0 or lat is None or lon is None or t is None:
                    continue
                if abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
                    continue
                sog, cog, nav = _num(p.get("SOG")), _num(p.get("COG")), None
                try:
                    nav = int(str(p.get("Navigational_Status", "")).strip())
                except ValueError:
                    pass
                sh, _ = self._put_pos(
                    mmsi, lat, lon, t, "mpb",
                    sog=None if sog is None or sog >= 102.3 else round(sog, 1),
                    cog=None if cog is None or cog >= 360 else round(cog, 1),
                    nav=None if nav is None or nav == 15 else nav, nav_set=True)
                # 靜態欄位只補缺（aisstream 的較完整）；剔除常見的佔位值
                if not sh.get("name"):
                    nm = _clean(p.get("ShipName"))
                    if nm and nm not in ("1",):
                        sh["name"] = nm
                if not sh.get("type"):
                    tp = _num(p.get("Ship_and_Cargo_Type"))
                    if tp:
                        sh["type"] = int(tp)
                if not sh.get("callsign"):
                    cs = _clean(p.get("Call_Sign"))
                    if cs and cs.strip("0") and cs != "1234567":
                        sh["callsign"] = cs
                if not sh.get("imo"):
                    try:
                        imo = int(str(p.get("IMO_Number", "")).strip() or 0)
                    except ValueError:
                        imo = 0
                    if 1000000 <= imo <= 9999999:
                        sh["imo"] = imo
                n += 1
        return n

    @staticmethod
    def _static(sh: dict, b: dict) -> None:
        nm = _clean(b.get("Name"))
        if nm:
            sh["name"] = nm
        tp = _num(b.get("Type"))
        if tp:
            sh["type"] = int(tp)
        for src, dst in (("CallSign", "callsign"), ("Destination", "dest")):
            v = _clean(b.get(src))
            if v:
                sh[dst] = v
        imo = _num(b.get("ImoNumber"))
        if imo:
            sh["imo"] = int(imo)
        d = b.get("Dimension") or {}
        if isinstance(d, dict) and any(_num(d.get(k)) for k in "ABCD"):
            L = (_num(d.get("A")) or 0) + (_num(d.get("B")) or 0)
            W = (_num(d.get("C")) or 0) + (_num(d.get("D")) or 0)
            if L:
                sh["length"] = L
            if W:
                sh["width"] = W
        dr = _num(b.get("MaximumStaticDraught"))
        if dr:
            sh["draught"] = dr
        eta = b.get("Eta") or {}
        if isinstance(eta, dict) and eta.get("Month"):
            sh["eta"] = "%02d-%02d %02d:%02d" % (eta.get("Month", 0), eta.get("Day", 0),
                                                 eta.get("Hour", 0), eta.get("Minute", 0))

    def _prune(self, now: float) -> None:
        if now - self._last_prune < 30:
            return
        self._last_prune = now
        with self.lock:
            for m in [m for m, s in self.ships.items() if now - s["t"] > STALE_SEC]:
                self.ships.pop(m, None)
                self.tracks.pop(m, None)
            for tr in self.tracks.values():
                while tr and now - tr[0][0] > TRACK_SEC:
                    tr.popleft()

    # ── 背景工作 ──
    def _idle(self) -> bool:
        return time.time() - self.last_access > self.idle_sec

    def _sleep(self, sec: float) -> None:
        end = time.time() + sec
        while time.time() < end and not self._idle():
            time.sleep(0.5)

    def _open(self):
        if self._connect:
            return self._connect()
        from websockets.sync.client import connect
        return connect(WS_URL, open_timeout=45, close_timeout=3, max_size=2 ** 22, compression="deflate",
                       ping_interval=None)

    def _run_ws(self) -> None:
        s = self.src["aisstream"]
        backoff = 2.0
        while not self._idle():
            t_live = 0.0
            try:
                s["state"] = "connecting"
                with self._open() as ws:
                    ws.send(json.dumps({"APIKey": self.key, "BoundingBoxes": self.bbox,
                                        "FilterMessageTypes": MSG_TYPES}))
                    logger.info("aisstream 已連線，訂閱範圍 %s", self.bbox)
                    last_rx = time.time()
                    while not self._idle():
                        try:
                            raw = ws.recv(timeout=20)
                        except TimeoutError:
                            if time.time() - last_rx > NO_DATA_SEC:
                                raise RuntimeError(f"{NO_DATA_SEC} 秒沒有收到資料，重新連線")
                            continue
                        last_rx = time.time()
                        if isinstance(raw, (bytes, bytearray)):
                            raw = raw.decode("utf-8", "replace")
                        try:
                            m = json.loads(raw)
                        except ValueError:
                            continue
                        if "error" in m:
                            raise RuntimeError(str(m["error"])[:200])
                        if m.get("MessageType") == "SubscriptionConfirmation":
                            continue
                        if s["state"] != "live":
                            s["state"], s["since"], s["error"], backoff = "live", time.time(), "", 2.0
                            t_live = time.time()
                        self.handle(m)
                        self._prune(time.time())
            except Exception as exc:  # noqa: BLE001  斷線、金鑰錯誤、超過連線數都會到這裡
                if t_live:
                    s["sessions"].append(time.time() - t_live)
                    s["reconnects"] += 1
                s["error"] = f"{type(exc).__name__}: {exc}"[:300]
                logger.warning("aisstream 連線中斷（本段 %.0f 秒）：%s（%.0f 秒後重連）",
                               time.time() - t_live if t_live else 0, s["error"], backoff)
                s["state"] = "retry"
                self._sleep(backoff * (0.7 + 0.6 * random.random()))
                backoff = min(backoff * 2, 120.0)
        s["state"] = "idle"
        logger.info("aisstream 閒置 %.0f 秒，已斷線", self.idle_sec)

    def _fetch_mpb(self) -> dict:
        if self._mpb_fetch:
            return self._mpb_fetch()
        import requests
        r = requests.get(MPB_URL, timeout=45, headers={
            "User-Agent": "Mozilla/5.0 (ATRDC SatDashboard AIS layer)", "Referer": MPB_REFERER})
        r.raise_for_status()
        return r.json()

    def _run_mpb(self) -> None:
        s = self.src["mpb"]
        fails = 0
        while not self._idle():
            t0 = time.time()
            try:
                n = self.handle_mpb(self._fetch_mpb())
                s["msgs"] += n
                s["last_msg"] = time.time()
                if s["state"] != "live":
                    s["since"] = time.time()
                s["state"], s["error"], fails = "live", "", 0
                self._prune(time.time())
                wait = MPB_INTERVAL - (time.time() - t0)
            except Exception as exc:  # noqa: BLE001
                fails += 1
                s["error"] = f"{type(exc).__name__}: {exc}"[:300]
                s["state"] = "retry"
                logger.warning("航港局 AIS 讀取失敗：%s", s["error"])
                wait = min(MPB_INTERVAL * fails, 600)
            self._sleep(max(wait, 5))
        s["state"] = "idle"


def _is_moving(sh: dict) -> bool:
    if sh.get("nav") in (1, 5, 6):        # 錨泊、繫泊、擱淺
        return False
    sog = sh.get("sog")
    return sog is not None and sog >= 0.5


HUB = AisHub()


@bp.get("/api/ais/status")
def api_ais_status():
    return json_response(HUB.status())


@bp.get("/api/ais/ships")
def api_ais_ships():
    HUB.touch()
    a = request.args

    def f(k, d, lo, hi):
        try:
            return max(lo, min(float(a.get(k, d)), hi))
        except ValueError:
            return d
    w, e = f("w", -180, -180, 180), f("e", 180, -180, 180)
    s, n = f("s", -90, -90, 90), f("n", 90, -90, 90)
    lim = int(f("limit", MAX_SHIPS, 1, MAX_SHIPS))
    srcs = {x for x in a.get("src", "").split(",") if x in SRC_CODE} if a.get("src") else None
    return json_response(HUB.query(w, s, e, n, moving=a.get("moving") == "1", limit=lim, srcs=srcs))


@bp.get("/api/ais/ship/<int:mmsi>")
def api_ais_ship(mmsi: int):
    HUB.touch()
    sh = HUB.ship(mmsi)
    if sh is None:
        return json_response({"error": "not found"}), 404
    sh.pop("hdg_t", None)
    return json_response(sh)
