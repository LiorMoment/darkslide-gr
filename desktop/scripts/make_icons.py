"""Draw the DARKSLIDE GR icons with Pillow, 4x supersampled: the app icon (no small line, on the
macOS grid), the start-up and README icons (with the small DARKSLIDE GR line), the browser tab
icon and GitHub's social preview card.

Usage: python3 make_icons.py <fonts> <out>
<fonts> holds DMMono-Medium.ttf, DMMono-Regular.ttf and NotoSansSC.ttf (Google Fonts, SIL OFL 1.1;
not kept in this repository). Copy the results to desktop/icon.png, desktop/splash.png,
web/favicon.png, docs/images/logo.png and docs/images/social-preview.png.
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONTS, OUT = Path(sys.argv[1]), Path(sys.argv[2])
BG, FG, SUB, EDGE = (0x26, 0x26, 0x24), (0xED, 0xED, 0xEA), (0x9A, 0x9A, 0x96), (0x4C, 0x4B, 0x47)
SS = 4

def mono(weight, px):
    return ImageFont.truetype(str(FONTS / ("DMMono-%s.ttf" % weight)), px)

def noto(px, wght):
    f = ImageFont.truetype(str(FONTS / "NotoSansSC.ttf"), px)
    f.set_variation_by_axes([wght])
    return f

def spaced(d, cx, baseline, text, font, fill, spacing):
    """Centred text with extra letter spacing (like CSS letter-spacing: after every glyph)."""
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) + spacing * len(text)
    x = cx - total / 2 + spacing / 2
    for ch, w in zip(text, widths):
        d.text((x, baseline), ch, font=font, fill=fill, anchor="ls")
        x += w + spacing

def icon_layer(size, with_text, stroke=None, edge=False):
    """The icon at `size` px on a transparent ground."""
    S = size * SS
    k = S / 1024
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=228 * k, fill=BG,
                        outline=EDGE if edge else None, width=round(4 * k) if edge else 0)
    w = (28 if with_text else (stroke or 32)) * k
    for pts in (((200, 320), (200, 200), (320, 200)), ((704, 200), (824, 200), (824, 320)),
                ((824, 704), (824, 824), (704, 824)), ((320, 824), (200, 824), (200, 704))):
        p = [(x * k, y * k) for x, y in pts]
        d.line(p, fill=FG, width=round(w), joint=None)
        # square the outer corner
        cx, cy = p[1]
        d.rectangle([cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2], fill=FG)
    if with_text:
        d.text((512 * k, 560 * k), "DS", font=mono("Medium", round(280 * k)), fill=FG, anchor="ms")
        spaced(d, 518 * k, 684 * k, "DARKSLIDE GR", mono("Regular", round(50 * k)), SUB, 12 * k)
    else:
        d.text((512 * k, 618 * k), "DS", font=mono("Medium", round(340 * k)), fill=FG, anchor="ms")
    return im.resize((size, size), Image.LANCZOS)

OUT.mkdir(parents=True, exist_ok=True)
app = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))                  # the app's icon (MyGo makes the sizes):
app.paste(icon_layer(824, False), (100, 100), icon_layer(824, False))  # macOS grid, 824 inside 1024
app.save(OUT / "icon.png")
icon_layer(512, True).save(OUT / "logo.png")                         # README
icon_layer(320, True).save(OUT / "splash.png")                       # desktop start-up screen
fav = icon_layer(64, False, stroke=48)
fav.save(OUT / "favicon.png")                                        # browser tab
fav.resize((32, 32), Image.LANCZOS).save(OUT / "favicon-32.png")

# GitHub social preview 1280x640
W, H = 1280 * SS, 640 * SS
card = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(card)
card.paste(icon_layer(400 * SS, True, edge=True), (96 * SS, 120 * SS), icon_layer(400 * SS, True, edge=True))
x = 576 * SS
def left(text, y, font, fill, spacing=0):
    if spacing:
        cx = x
        for ch in text:
            d.text((cx, y), ch, font=font, fill=fill, anchor="ls")
            cx += font.getlength(ch) + spacing
    else:
        d.text((x, y), text, font=font, fill=fill, anchor="ls")
left("DARKSLIDE GR", 236 * SS, mono("Medium", 56 * SS), FG, 0.08 * 56 * SS)
left("给 GR III / GR IIIx 改预设名、图标和胶片色彩", 300 * SS, noto(26 * SS, 400), (0xB5, 0xB5, 0xB0))
left("Preset names, icons and film looks for the GR III / IIIx", 344 * SS, noto(20 * SS, 400), SUB)
tag = mono("Regular", 16 * SS)
tx = x
for t in ("Mac · Windows", "本机运行 · 开源"):
    f = tag if t.isascii() else noto(16 * SS, 400)
    w = f.getlength(t)
    d.rectangle([tx, 384 * SS, tx + w + 28 * SS, 420 * SS], outline=(0x64, 0x63, 0x5E), width=SS)
    d.text((tx + 14 * SS, 408 * SS), t, font=f, fill=FG, anchor="ls")
    tx += w + 28 * SS + 12 * SS
left("github.com/LiorMoment/darkslide-gr · 非理光官方 / not affiliated with RICOH", 470 * SS, noto(16 * SS, 400), (0x92, 0x92, 0x8D))
card.resize((1280, 640), Image.LANCZOS).save(OUT / "social-preview.png")
print("done", sorted(p.name for p in OUT.iterdir()))
