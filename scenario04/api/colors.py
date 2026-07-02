"""前端顯示用顏色映射。"""
from __future__ import annotations

PURPOSE_COLORS = {
    "有效載荷": "#4CAF50", "碎片": "#FF9800",
    "火箭體":   "#9E9E9E", "不明物體": "#607D8B",
}
COUNTRY_COLORS = {
    "美國": "#3F51B5", "俄羅斯/蘇聯": "#F44336", "中國": "#FF5722",
    "英國": "#2196F3", "法國": "#9C27B0", "日本": "#E91E63",
    "印度": "#FF9800", "ESA": "#00BCD4", "其他": "#78909C", "不明": "#455A64",
}
CONSTELLATION_COLORS = {
    "Starlink": "#1565C0", "OneWeb": "#00897B", "Kuiper": "#FF8F00",
    "千帆/Qianfan": "#C62828", "Iridium": "#558B2F", "Globalstar": "#6A1B9A",
    "Planet/Flock": "#2E7D32", "Spire": "#00838F", "吉林/Jilin": "#AD1457",
    "遙感/Yaogan": "#B71C1C", "高分": "#E64A19", "風雲": "#0277BD",
    "其他衛星": "#546E7A",
}
ERA_COLORS = {
    "< 1 年": "#F44336", "1–5 年": "#FF9800",
    "5–10 年": "#4CAF50", "> 10 年": "#607D8B", "不明": "#455A64",
}


def get_color(ftype: str, label: str) -> str:
    maps = {
        "purpose":       PURPOSE_COLORS,
        "country":       COUNTRY_COLORS,
        "constellation": CONSTELLATION_COLORS,
        "era":           ERA_COLORS,
    }
    return maps.get(ftype, {}).get(label, "#78909C")
