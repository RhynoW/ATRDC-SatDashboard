"""把 Rust 單機版的碎片連鎖複製到線上版（單機版 RUST_SatDashboard 是唯一來源，線上版的副本請勿直接修改）。

複製內容：
  計算核心  RUST_SatDashboard/src/debris_engine.rs           → rust/debris_cli/src/debris_engine.rs
            RUST_SatDashboard/debris_cli/src/main.rs          → rust/debris_cli/src/main.rs（改用同目錄的 debris_engine.rs）
            RUST_SatDashboard/debris_cli/Cargo.toml、Cargo.lock → rust/debris_cli/
            RUST_SatDashboard/config/f107_monthly.csv、msis_density.csv → rust/debris_cli/config/
  前端      RUST_SatDashboard/web/ext/games_debris.js、games_debris_game.js、games_debris_i18n.js → scenario04/web/static/js/
  面板      RUST_SatDashboard/web/ext/games.html 的 <div id="tab-debris"> 區塊 → scenario04/web/templates/_game_debris_panel.html

用法（在 scenario-advanced01 目錄）：
  python tools/sync_debris.py           # 複製
  python tools/sync_debris.py --check   # 只檢查線上版副本是否與單機版一致（不一致時結束碼 1）
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
RUST = HERE.parent / "RUST_SatDashboard"
CLI = HERE / "rust" / "debris_cli"
JS = HERE / "scenario04" / "web" / "static" / "js"
TPL = HERE / "scenario04" / "web" / "templates"
HEAD = "// 由 scenario-advanced01/tools/sync_debris.py 自 RUST_SatDashboard 複製，請勿直接修改。\n"


def lf(s: str) -> str:
    return s.replace("\r\n", "\n")


def main_rs() -> str:
    s = lf((RUST / "debris_cli" / "src" / "main.rs").read_text(encoding="utf-8"))
    old = '#[path = "../../src/debris_engine.rs"]\nmod debris_engine;'
    if old not in s:
        raise SystemExit("debris_cli/src/main.rs 找不到 #[path] 宣告，請更新 sync_debris.py")
    return HEAD + s.replace(old, "mod debris_engine;")


def panel() -> str:
    html = lf((RUST / "web" / "ext" / "games.html").read_text(encoding="utf-8"))
    m = re.search(r'\n(\s*)<div id="tab-debris" hidden>\n(.*?)\n\1</div>\n', html, re.S)
    if not m:
        raise SystemExit("games.html 找不到 <div id=\"tab-debris\" hidden> 區塊")
    body = '<div id="tab-debris">\n' + m.group(2) + "\n</div>\n"
    if "{{" in body or "{%" in body:
        raise SystemExit("面板含 Jinja 語法字元，需要跳脫")
    return "{# 由 tools/sync_debris.py 自 RUST_SatDashboard/web/ext/games.html 擷取，請勿直接修改 #}\n" + body


def targets() -> dict[Path, str]:
    out: dict[Path, str] = {
        CLI / "src" / "debris_engine.rs": HEAD + lf((RUST / "src" / "debris_engine.rs").read_text(encoding="utf-8")),
        CLI / "src" / "main.rs": main_rs(),
        CLI / "Cargo.lock": lf((RUST / "debris_cli" / "Cargo.lock").read_text(encoding="utf-8")),
        TPL / "_game_debris_panel.html": panel(),
    }
    toml = lf((RUST / "debris_cli" / "Cargo.toml").read_text(encoding="utf-8"))
    out[CLI / "Cargo.toml"] = "# 由 scenario-advanced01/tools/sync_debris.py 自 RUST_SatDashboard 複製，請勿直接修改。\n" + toml
    for f in ("f107_monthly.csv", "msis_density.csv"):
        out[CLI / "config" / f] = lf((RUST / "config" / f).read_text(encoding="utf-8"))
    for f in ("games_debris.js", "games_debris_game.js", "games_debris_i18n.js"):
        out[JS / f] = lf((RUST / "web" / "ext" / f).read_text(encoding="utf-8"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if not RUST.is_dir():
        print(f"找不到單機版目錄：{RUST}")
        return 2
    stale = []
    for path, text in targets().items():
        cur = path.read_text(encoding="utf-8") if path.exists() else None
        if cur == text:
            continue
        stale.append(path)
        if not a.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    rel = [str(p.relative_to(HERE)) for p in stale]
    if a.check:
        print("一致" if not stale else "與單機版不一致：\n  " + "\n  ".join(rel))
        return 1 if stale else 0
    print(f"已更新 {len(stale)} 個檔案" + ("：\n  " + "\n  ".join(rel) if rel else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
