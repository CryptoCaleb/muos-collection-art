#!/usr/bin/env python3
"""Regenerate Collection Art's UI assets (run on a PC with Pillow installed).

    pip install pillow
    python tools/make_assets.py [path/to/font-bold.ttf] [path/to/font-regular.ttf]

Writes CollectionArt/assets/{font,logo,logo_small,icon_big}.png and the matching
.bin files (zlib-compressed raw pixels) that the app loads at start-up, plus the
launcher glyph. Defaults to DejaVu Sans, which ships with most Linux distros.
"""
import json, os, struct, sys, zlib
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "CollectionArt")
ASSETS = os.path.join(ROOT, "assets")
FB = sys.argv[1] if len(sys.argv) > 1 else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FR = sys.argv[2] if len(sys.argv) > 2 else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

BG = (9, 11, 18)
ACC = (84, 104, 227)
TILE_COLS = [(236, 64, 52), (66, 103, 178), (43, 170, 160), (244, 184, 40)]


def save_bin(path, im):
    ch = 1 if im.mode == "L" else 3
    im = im.convert("L" if ch == 1 else "RGB")
    data = struct.pack(">4sHHB", b"CAB1", im.width, im.height, ch) + zlib.compress(im.tobytes(), 6)
    with open(path, "wb") as f:
        f.write(data)


def save(name, im):
    im.save(os.path.join(ASSETS, name + ".png"))
    save_bin(os.path.join(ASSETS, name + ".bin"), im)


def font_atlas():
    chars = [chr(c) for c in range(32, 127)] + ["✓", "•", "…", "·"]
    specs = [("s", FR, 15), ("m", FB, 18), ("l", FB, 24), ("x", FB, 40)]
    pad = 8
    rows, meta = [], {}
    for key, path, size in specs:
        f = ImageFont.truetype(path, size)
        asc, desc = f.getmetrics(); lh = asc + desc
        xs, x = {}, pad
        for c in chars:
            w = max(1, int(round(f.getlength(c)))); xs[c] = [x, w]; x += w + pad
        im = Image.new("L", (x, lh), 0); d = ImageDraw.Draw(im)
        for c in chars:
            d.text((xs[c][0], 0), c, font=f, fill=255)
        rows.append((key, im, xs, lh))
    W = max(r[1].width for r in rows); H = sum(r[3] for r in rows) + 4 * len(rows)
    atlas = Image.new("L", (W, H), 0); y = 0
    for key, im, xs, lh in rows:
        atlas.paste(im, (0, y))
        meta[key] = {"y": y, "h": lh, "chars": {str(ord(c)): v for c, v in xs.items()}}
        y += lh + 4
    save("font", atlas)
    json.dump(meta, open(os.path.join(ASSETS, "font.json"), "w"))


def icon(sz):
    im = Image.new("RGBA", (sz, sz), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    g = max(2, sz // 14); c = (sz - g) // 2; r = max(2, sz // 8)
    for i, col in enumerate(TILE_COLS):
        x = (i % 2) * (c + g); y = (i // 2) * (c + g)
        d.rounded_rectangle([x, y, x + c - 1, y + c - 1], radius=r, fill=col + (255,))
    return im


def logo(scale):
    isz = int(64 * scale)
    f1 = ImageFont.truetype(FB, int(34 * scale)); f2 = ImageFont.truetype(FR, int(15 * scale))
    t1, t2 = "Collection Art", "COVER COLLAGES FOR muOS"
    gap = int(16 * scale)
    W = int(isz + gap + max(f1.getlength(t1), f2.getlength(t2))) + 4
    im = Image.new("RGB", (W, isz), BG); ic = icon(isz); im.paste(ic, (0, 0), ic)
    d = ImageDraw.Draw(im)
    d.text((isz + gap, int(2 * scale)), t1, font=f1, fill=(255, 255, 255))
    d.text((isz + gap + 2, int(44 * scale)), t2, font=f2, fill=ACC)
    return im


def main():
    os.makedirs(ASSETS, exist_ok=True)
    font_atlas()
    save("logo", logo(1.0))
    save("logo_small", logo(0.6))
    big = Image.new("RGB", (120, 120), BG); ic = icon(120); big.paste(ic, (0, 0), ic)
    save("icon_big", big)
    # launcher glyph: white silhouette, recoloured by the active theme
    g = Image.new("RGBA", (24, 24), (0, 0, 0, 0)); d = ImageDraw.Draw(g)
    for x, y in [(2, 2), (13, 2), (2, 13), (13, 13)]:
        d.rounded_rectangle([x, y, x + 8, y + 8], radius=2, fill=(255, 255, 255, 255))
    os.makedirs(os.path.join(ROOT, "glyph"), exist_ok=True)
    g.save(os.path.join(ROOT, "glyph", "collectionart.png"))
    print("assets written to", os.path.normpath(ASSETS))


if __name__ == "__main__":
    main()
