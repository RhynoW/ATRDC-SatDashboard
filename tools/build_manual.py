"""操作手冊 Markdown → /manual 頁面（scenario04/web/templates/manual.html）。

手冊原稿：docs/SatDashboard_操作手冊_v<版本>.md。產出為靜態 HTML（不需執行期 Markdown 套件，Rust 單機版離線也能顯示），
Rust 版由 RUST_SatDashboard/scripts/sync_web.py 同步。修改手冊後重跑：

  py -3.13 tools/build_manual.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUT = ROOT / "scenario04" / "web" / "templates" / "manual.html"

PAGE = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>操作手冊 — SatDashboard</title>
<style>
:root{--bg:#0A0D13;--panel:#12161f;--line:rgba(255,255,255,.10);--ink:#dfe6f0;--muted:#8b96a8;
      --acc:#35C6F4;--hi:#FFD700;--mono:"Consolas","Courier New",monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.75 "Segoe UI","Microsoft JhengHei",sans-serif}
.layout{display:grid;grid-template-columns:250px minmax(0,1fr);gap:28px;max-width:1180px;margin:0 auto;padding:24px 16px 80px}
nav.toc{position:sticky;top:16px;align-self:start;max-height:calc(100vh - 32px);overflow:auto;
        background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;font-size:13px}
nav.toc .home{display:block;color:var(--acc);text-decoration:none;font-family:var(--mono);margin-bottom:8px}
nav.toc ul{list-style:none;margin:0;padding:0}
nav.toc ul ul{padding-left:12px}
nav.toc a{color:var(--ink);text-decoration:none;display:block;padding:2px 0}
nav.toc a:hover{color:var(--acc)}
main{min-width:0}
h1{font-size:26px;margin:0 0 10px;color:#fff}
h2{font-size:20px;margin:36px 0 10px;padding-top:8px;border-top:1px solid var(--line);color:var(--hi)}
h3{font-size:16px;margin:24px 0 8px;color:var(--acc)}
a{color:var(--acc)}
blockquote{margin:12px 0;padding:8px 14px;background:rgba(53,198,244,.07);border-left:3px solid var(--acc);border-radius:4px;color:var(--ink)}
blockquote p{margin:4px 0}
table{border-collapse:collapse;width:100%;margin:10px 0 16px;font-size:14px;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
th{background:var(--panel);color:var(--muted);font-weight:600}
code{font-family:var(--mono);font-size:13px;background:rgba(255,255,255,.07);padding:1px 5px;border-radius:4px}
pre{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;overflow-x:auto}
pre code{background:none;padding:0}
hr{display:none}
@media (max-width:820px){.layout{grid-template-columns:1fr}nav.toc{position:static;max-height:none}}
@media print{nav.toc{display:none}.layout{display:block}body{background:#fff;color:#000}h2,h3,h1{color:#000}}
</style>
</head>
<body>
<div class="layout">
<nav class="toc"><a class="home" href="/">&larr; 返回主頁</a>__TOC__</nav>
<main>
__BODY__
</main>
</div>
</body>
</html>
"""


def latest_manual() -> Path:
    files = sorted(DOCS.glob("SatDashboard_操作手冊_v*.md"),
                   key=lambda p: tuple(int(x) for x in re.findall(r"\d+", p.stem.split("_v")[-1])))
    if not files:
        raise SystemExit("找不到 docs/SatDashboard_操作手冊_v*.md")
    return files[-1]


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else latest_manual()
    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc"],
                           extension_configs={"toc": {"toc_depth": "2-3"}})
    body = md.convert(src.read_text(encoding="utf-8"))
    # 外部連結開新分頁；站內（/ 開頭）維持原分頁
    body = re.sub(r'<a href="(https?://[^"]+)"', r'<a href="\1" target="_blank" rel="noopener"', body)
    html = PAGE.replace("__TOC__", md.toc).replace("__BODY__", body)
    assert not any(s in html for s in ("{{", "{%", "{#")), "產出含 Jinja 語法，Flask render_template 會誤判"
    OUT.write_text(html, encoding="utf-8", newline="\n")
    print(f"{src.name} → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
