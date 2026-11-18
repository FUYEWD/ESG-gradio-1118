"""產生 PWA 圖示（需要 Pillow：pip install pillow）。圖示已隨專案附上，通常不必重新執行。"""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "static" / "icons"
OUT.mkdir(parents=True, exist_ok=True)
C1, C2 = (79, 70, 229), (6, 182, 212)  # indigo -> cyan


def gradient(size):
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size - 2)
            px[x, y] = tuple(int(C1[i] + (C2[i] - C1[i]) * t) for i in range(3))
    return img


def draw_glyph(img, scale):
    """在中央畫長條圖圖示；scale 為圖案占整張圖的比例（maskable 需 <= 0.6）"""
    s = img.size[0]
    d = ImageDraw.Draw(img)
    g = s * scale
    x0, y0 = (s - g) / 2, (s - g) / 2
    bar_w, gap = g * 0.2, g * 0.1
    heights = [0.45, 0.75, 1.0]
    base = y0 + g
    x = x0 + (g - (3 * bar_w + 2 * gap)) / 2
    for h in heights:
        d.rounded_rectangle([x, base - g * h, x + bar_w, base], radius=bar_w * 0.3, fill="white")
        x += bar_w + gap
    d.rounded_rectangle([x0, base + g * 0.06, x0 + g, base + g * 0.1], radius=g * 0.02, fill="white")


def make(name, size, scale, rounded):
    base = 512  # 先以 512 繪製再縮放，邊緣較平滑
    img = gradient(base)
    draw_glyph(img, scale)
    if rounded:
        mask = Image.new("L", (base, base), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, base, base], radius=int(base * 0.22), fill=255)
        img = img.convert("RGBA")
        img.putalpha(mask)
    img = img.resize((size, size), Image.LANCZOS)
    img.save(OUT / name)


make("icon-192.png", 192, 0.52, True)
make("icon-512.png", 512, 0.52, True)
make("icon-maskable-512.png", 512, 0.42, False)   # 滿版 + 安全區
make("apple-touch-icon.png", 180, 0.50, False)    # iOS 會自行裁圓角
print("icons done")
