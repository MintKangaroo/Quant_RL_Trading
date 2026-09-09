"""홈 화면 아이콘 — 아이폰 사파리 "홈 화면에 추가" 가 집는 apple-touch-icon (2026-09-07).

    .venv/bin/python tools/make_app_icon.py

시트 화면과 같은 팔레트(app.css: --bg #050505 · --accent #4c6ef5 · --up #22c55e · --down #ef4444 · --rule #2a2a2a).
검은 바탕에 괘선, 오르는 캔들 일곱, 종가를 잇는 파란 선. 글자는 없다 — iOS 는 아이콘 밑에 이름을 따로 쓴다.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path("quant_rl_trading/dashboard/static/icons")
BG, RULE, ACCENT, UP, DOWN, TEXT = "#050505", "#1c1c1c", "#4c6ef5", "#22c55e", "#ef4444", "#e8e8e8"
S = 4  # 슈퍼샘플링
N = 1024 * S


def draw() -> Image.Image:
    img = Image.new("RGB", (N, N), BG)
    d = ImageDraw.Draw(img)
    # 괘선 — 시트의 가로줄
    for i in range(1, 8):
        y = int(N * i / 8)
        d.line([(0, y), (N, y)], fill=RULE, width=2 * S)
    # 캔들 7개 — 저점에서 고점으로, 중간에 한 번 꺾인다
    raw = [(0.62, 0.55, 0.66, 0.50), (0.55, 0.48, 0.58, 0.44), (0.48, 0.52, 0.55, 0.45),
           (0.52, 0.40, 0.54, 0.36), (0.40, 0.34, 0.43, 0.30), (0.34, 0.38, 0.40, 0.32), (0.38, 0.22, 0.40, 0.18)]
    # 세로로 가운데에 오게 늘린다 (0.18~0.66 → 0.20~0.80)
    candles = [tuple(0.20 + (v - 0.18) * 1.25 for v in c) for c in raw]
    xs = [0.14 + 0.72 * i / 6 for i in range(7)]
    w = 0.075 * N
    closes = [(x * N, c * N) for x, (_o, c, _lo, _hi) in zip(xs, candles)]
    # 종가 선은 캔들 **뒤**에 — 글로우 한 겹 위에 본선
    glow = Image.new("RGB", (N, N), BG)
    gd = ImageDraw.Draw(glow)
    gd.line(closes, fill=ACCENT, width=int(0.07 * N), joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(radius=0.035 * N))
    img = Image.blend(img, Image.composite(glow, img, glow.convert("L").point(lambda v: min(255, v * 2))), 0.6)
    d = ImageDraw.Draw(img)
    d.line(closes, fill=ACCENT, width=int(0.026 * N), joint="curve")
    for x, (o, c, lo, hi) in zip(xs, candles):
        cx = x * N
        color = UP if c < o else DOWN  # y 축은 아래로 자란다: c<o 면 올랐다
        d.line([(cx, lo * N), (cx, hi * N)], fill=color, width=int(0.014 * N))
        top, bot = min(o, c) * N, max(o, c) * N
        d.rectangle([cx - w / 2, top, cx + w / 2, bot], fill=color)
    r = 0.032 * N
    ex, ey = closes[-1]
    d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=TEXT)
    return img


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    base = draw()
    for size, name in ((180, "apple-touch-icon.png"), (192, "icon-192.png"), (512, "icon-512.png"), (32, "favicon-32.png")):
        base.resize((size, size), Image.LANCZOS).save(OUT / name, optimize=True)
        print(f"{OUT / name} {size}px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
