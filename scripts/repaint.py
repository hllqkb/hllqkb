"""每天重画 GitHub 主页：用日期当种子画一幅低多边形地形，再刷新 README 里的当日信息块。
新近文章列表不在这里，由 .github/workflows/blog-posts.yml 读博客 RSS 更新。

只用标准库，GitHub Actions 每天 08:30（北京时间）跑一次；同一天重跑结果相同。
"""

from __future__ import annotations

import json
import math
import random
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
README = ROOT / "README.md"

POEM_API = "https://v1.jinrishici.com/all.json"

START = "<!-- DAILY:START -->"
END = "<!-- DAILY:END -->"

GRID = 22  # 地形网格边长
WIDTH, HEIGHT = 800, 460
SEA_LEVEL = 0.28

FALLBACK_POEMS = [
    ("行到水穷处，坐看云起时。", "王维"),
    ("山重水复疑无路，柳暗花明又一村。", "陆游"),
    ("长风破浪会有时，直挂云帆济沧海。", "李白"),
    ("纸上得来终觉浅，绝知此事要躬行。", "陆游"),
    ("千淘万漉虽辛苦，吹尽狂沙始到金。", "刘禹锡"),
]


@dataclass(frozen=True)
class Palette:
    name: str
    sky_top: str
    sky_bottom: str
    water: str
    bands: tuple[str, ...]  # 由低到高
    sun: str


DAY_PALETTES = [
    Palette("晨雾", "#dbe8f2", "#f6efe4", "#8fb8cf", ("#c9d6a3", "#9fbf86", "#6f9a6b", "#5b7a63", "#eef1f0"), "#e8735a"),
    Palette("秋原", "#f3e2c7", "#fbf3e6", "#7fa9b8", ("#e2c58f", "#d39b5f", "#b8704a", "#8a5a46", "#f4ece2"), "#c8443b"),
    Palette("青峦", "#d6ece6", "#f2f7f2", "#6fb0b0", ("#b9dbb0", "#83bf95", "#4f957d", "#3d6e66", "#e6f0ec"), "#e09a3e"),
    Palette("海岛", "#cfe4f5", "#eef6fb", "#5d9fd0", ("#efe1b5", "#a8cf8e", "#6faf72", "#4f7f68", "#f2f5f7"), "#f08a4b"),
]

NIGHT_PALETTES = [
    Palette("夜雾", "#0d1420", "#1b2636", "#1f3a52", ("#2c4038", "#36513f", "#406249", "#4d6b5c", "#a9b7c2"), "#f1e7c4"),
    Palette("秋夜", "#140f14", "#2a1f24", "#22384a", ("#4a3a2a", "#5e4430", "#6e4a36", "#5a4040", "#c9bdb2"), "#f3d9a4"),
    Palette("青夜", "#0b1716", "#16292a", "#1d4446", ("#264136", "#2d5545", "#336650", "#2f5450", "#a6c2bb"), "#e9e4c8"),
    Palette("海夜", "#0a1424", "#14233a", "#1c4266", ("#4d4733", "#3a5a3e", "#3b6448", "#33524a", "#b3c1cc"), "#f5e6b8"),
]


def today_cn() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def fetch(url: str, timeout: float = 10) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hllqkb-profile-repaint"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:  # 网络失败不影响作画
        print(f"fetch failed: {url}: {exc}")
        return None


def value_noise(rng: random.Random, size: int, cells: int) -> list[list[float]]:
    """在 cells×cells 的随机格点上做平滑插值，返回 size×size 的 [0,1] 噪声。"""
    lattice = [[rng.random() for _ in range(cells + 1)] for _ in range(cells + 1)]

    def smooth(t: float) -> float:
        return t * t * (3 - 2 * t)

    out: list[list[float]] = []
    for i in range(size):
        row: list[float] = []
        for j in range(size):
            u = i / (size - 1) * cells
            v = j / (size - 1) * cells
            i0, j0 = min(int(u), cells - 1), min(int(v), cells - 1)
            fu, fv = smooth(u - i0), smooth(v - j0)
            a = lattice[i0][j0] * (1 - fu) + lattice[i0 + 1][j0] * fu
            b = lattice[i0][j0 + 1] * (1 - fu) + lattice[i0 + 1][j0 + 1] * fu
            row.append(a * (1 - fv) + b * fv)
        out.append(row)
    return out


def heightfield(rng: random.Random) -> list[list[float]]:
    """三层噪声叠加，再乘一个偏心的岛形衰减，让每天的山峰位置不同。"""
    n = GRID + 1
    octaves = [(value_noise(rng, n, 3), 0.6), (value_noise(rng, n, 6), 0.3), (value_noise(rng, n, 11), 0.1)]
    cx, cy = rng.uniform(0.35, 0.65), rng.uniform(0.35, 0.65)
    field: list[list[float]] = []
    for i in range(n):
        row: list[float] = []
        for j in range(n):
            h = sum(layer[i][j] * w for layer, w in octaves)
            d = math.hypot(i / (n - 1) - cx, j / (n - 1) - cy)
            falloff = max(0.0, 1 - (d / 0.58) ** 2)
            row.append(h * (0.2 + 1.1 * falloff))
        field.append(row)
    top = max(max(r) for r in field)
    return [[v / top for v in r] for r in field]


def mix(hex_a: str, factor: float) -> str:
    """按 factor 调亮或调暗颜色（<1 变暗，>1 变亮）。"""
    r, g, b = (int(hex_a[k:k + 2], 16) for k in (1, 3, 5))
    if factor >= 1:
        t = factor - 1
        r, g, b = (round(c + (255 - c) * t) for c in (r, g, b))
    else:
        r, g, b = (round(c * factor) for c in (r, g, b))
    return f"#{min(r, 255):02x}{min(g, 255):02x}{min(b, 255):02x}"


def band_color(p: Palette, h: float) -> str:
    if h <= SEA_LEVEL:
        return p.water
    t = (h - SEA_LEVEL) / (1 - SEA_LEVEL)
    idx = min(int(t * len(p.bands)), len(p.bands) - 1)
    return p.bands[idx]


def render(field: list[list[float]], p: Palette, sun_pos: tuple[float, float], night: bool) -> str:
    w = 16.5  # 等距投影单格半宽
    hh = w / 2
    lift = 130.0  # 高度放大
    ox, oy = WIDTH / 2, 100.0

    def project(i: int, j: int) -> tuple[float, float, float]:
        z = max(field[i][j], SEA_LEVEL)
        return ox + (i - j) * w, oy + (i + j) * hh - z * lift, z

    light = (-0.5, -0.6, 0.62)
    norm = math.sqrt(sum(c * c for c in light))
    light = tuple(c / norm for c in light)

    polys: list[str] = []
    # i+j 从小到大即从远到近，后画的盖住先画的
    cells = sorted(((i, j) for i in range(GRID) for j in range(GRID)), key=lambda c: c[0] + c[1])
    for i, j in cells:
        for tri in (((i, j), (i + 1, j), (i + 1, j + 1)), ((i, j), (i + 1, j + 1), (i, j + 1))):
            pts = [project(a, b) for a, b in tri]
            # 用格点坐标和高度求法向量算明暗
            (a0, b0), (a1, b1), (a2, b2) = tri
            za, zb, zc = (max(field[a][b], SEA_LEVEL) * 6 for a, b in tri)
            ux, uy, uz = a1 - a0, b1 - b0, zb - za
            vx, vy, vz = a2 - a0, b2 - b0, zc - za
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            nlen = math.sqrt(nx * nx + ny * ny + nz * nz) or 1
            if nz < 0:
                nx, ny, nz = -nx, -ny, -nz
            shade = max(0.0, (nx * light[0] + ny * light[1] + nz * light[2]) / nlen)
            avg_h = sum(field[a][b] for a, b in tri) / 3
            base = band_color(p, avg_h)
            factor = (0.55 + 0.6 * shade) if not night else (0.6 + 0.5 * shade)
            fill = mix(base, factor)
            d = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts)
            polys.append(f'<polygon points="{d}" fill="{fill}" stroke="{fill}" stroke-width="0.6"/>')

    sx, sy = sun_pos
    glow = "0.35" if night else "0.25"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}">'
        f'<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{p.sky_top}"/><stop offset="1" stop-color="{p.sky_bottom}"/></linearGradient>'
        f'<radialGradient id="glow"><stop offset="0" stop-color="{p.sun}" stop-opacity="{glow}"/>'
        f'<stop offset="1" stop-color="{p.sun}" stop-opacity="0"/></radialGradient></defs>'
        f'<rect width="{WIDTH}" height="{HEIGHT}" rx="14" fill="url(#sky)"/>'
        f'<circle cx="{sx:.0f}" cy="{sy:.0f}" r="70" fill="url(#glow)"/>'
        f'<circle cx="{sx:.0f}" cy="{sy:.0f}" r="20" fill="{p.sun}"/>'
        + "".join(polys)
        + "</svg>\n"
    )


def poem_of_day(rng: random.Random) -> tuple[str, str]:
    raw = fetch(POEM_API)
    if raw:
        try:
            data = json.loads(raw)
            content, author = str(data["content"]).strip(), str(data["author"]).strip()
            if content and author:
                return content, author
        except (ValueError, KeyError) as exc:
            print(f"poem parse failed: {exc}")
    return rng.choice(FALLBACK_POEMS)


def daily_block(date: str, palette_name: str, poem: tuple[str, str]) -> str:
    lines = [
        START,
        "<!-- 这一块由 .github/workflows/daily.yml 每天早上自动重写，手改会被覆盖 -->",
        '<p align="center">',
        "  <picture>",
        '    <source media="(prefers-color-scheme: dark)" srcset="assets/terrain-dark.svg">',
        f'    <img src="assets/terrain.svg" width="100%" alt="今日地形 {date}">',
        "  </picture>",
        "</p>",
        f'<p align="center"><sub>今日地形 · {date} · {palette_name} · 以日期为种子，每天早上重画一次'
        f" &nbsp;·&nbsp; 「{escape(poem[0])}」 {escape(poem[1])}</sub></p>",
    ]
    lines.append(END)
    return "\n".join(lines)


def update_readme(block: str) -> None:
    text = README.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        raise SystemExit(f"README 里找不到 {START} … {END} 标记")
    README.write_text(pattern.sub(lambda _: block, text, count=1), encoding="utf-8")


def main() -> None:
    now = today_cn()
    date = now.strftime("%Y-%m-%d")
    rng = random.Random(date)

    idx = rng.randrange(len(DAY_PALETTES))
    field = heightfield(rng)
    # 太阳放在左右上角，避开中间的山峰
    sun = (rng.choice([rng.uniform(70, 190), rng.uniform(610, 730)]), rng.uniform(50, 80))

    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "terrain.svg").write_text(render(field, DAY_PALETTES[idx], sun, night=False), encoding="utf-8")
    (ASSETS / "terrain-dark.svg").write_text(render(field, NIGHT_PALETTES[idx], sun, night=True), encoding="utf-8")

    block = daily_block(date, DAY_PALETTES[idx].name, poem_of_day(rng))
    update_readme(block)
    print(f"repainted {date} ({DAY_PALETTES[idx].name})")


if __name__ == "__main__":
    main()
