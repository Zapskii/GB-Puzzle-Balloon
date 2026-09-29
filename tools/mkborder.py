#!/usr/bin/env python3
"""Generate art/border_sgb.png -- the Super Game Boy border for BUBBLE.

The border is a 256x224 image; the 160x144 game window (x 48..207, y 40..183)
stays transparent, because that is where the SGB puts the Game Boy's screen.
`make border` then turns the PNG into border_data.c with png2asset.

Drawn here rather than hand-painted, and the lettering comes from the game's
own font (FONT_ORDER / FONT_GLYPHS in main.c) rather than a second one
invented alongside it -- the border is the same hand as the title screen.
Colours are capped at 16 so the whole border packs into a single SGB palette.

    tools/mkborder.py && make border
"""
import re

from PIL import Image, ImageDraw

OUT_PNG = "art/border_sgb.png"
MAIN_C = "main.c"

W, H = 256, 224
WIN = (48, 40, 208, 184)                # game window: x0, y0, x1, y1 (exclusive)

# The palette. 16 is the SGB border's per-palette limit, so staying under it
# means one palette and no per-cell attributes to reason about.
BG = (10, 14, 34, 255)                  # night sky navy
BG_D = (4, 6, 18, 255)                  # the bezel around the screen
FRAME = (72, 84, 140, 255)
TITLE = (252, 216, 96, 255)
OUT = (20, 24, 48, 255)                 # bubble outline
BUBBLES = [                             # (body, highlight), as in the game
    ((216, 60, 56, 255), (248, 148, 128, 255)),      # red
    ((240, 196, 48, 255), (252, 238, 150, 255)),     # yellow
    ((72, 184, 88, 255), (160, 232, 150, 255)),      # green
    ((64, 120, 216, 255), (150, 200, 248, 255)),     # blue
]
R = 13                                  # bubble radius: 27px, so they touch
PITCH = 28


def read_font():
    """The game's own 5x7 glyphs, straight out of main.c.

    FONT_ORDER is the glyph order and each FONT_GLYPHS row is a 5-bit shape in
    the top bits of a byte.  Parsing rather than copying means the border says
    'PUZZLE BALLOON' in exactly the letters the title screen does, and follows
    if the font ever grows.
    """
    src = open(MAIN_C).read()
    order = re.search(r'FONT_ORDER\[\]\s*=\s*"([^"]*)"', src).group(1)
    body = re.search(r'FONT_GLYPHS\[.*?\]\s*=\s*\{(.*?)\n\};', src, re.S).group(1)
    rows = [re.findall(r'0x([0-9A-Fa-f]{2})', g) for g in
            re.findall(r'\{([^}]*)\}', body)]
    # A glyph is 7 rows of 5 pixels, ink in bits 7..3.
    return {ch: ["".join("#" if int(r, 16) & (0x80 >> c) else "."
                        for c in range(5)) for r in g[:7]]
            for ch, g in zip(order, rows)}


def draw_text(d, font, x, y, s, scale, col):
    for ch in s:
        if ch != " ":
            for r, row in enumerate(font[ch]):
                for c, bit in enumerate(row):
                    if bit == "#":
                        d.rectangle([x + c * scale, y + r * scale,
                                     x + c * scale + scale - 1,
                                     y + r * scale + scale - 1], fill=col)
        x += 6 * scale                  # 5px ink + 1px gap


def bubble(d, cx, cy, n):
    """One bubble, shaded the way the game's tiles are: outline, body,
    and a specular dot up and to the left."""
    body, hi = BUBBLES[n % len(BUBBLES)]
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=OUT)
    d.ellipse([cx - R + 2, cy - R + 2, cx + R - 2, cy + R - 2], fill=body)
    hr = 3
    d.ellipse([cx - 6 - hr, cy - 6 - hr, cx - 6 + hr, cy - 6 + hr], fill=hi)


def main():
    font = read_font()
    im = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(im)

    # The bezel the screen sits in, and a highlight line just outside it.
    d.rectangle([40, 32, W - 41, H - 33], fill=BG_D)
    d.rectangle([WIN[0] - 2, WIN[1] - 2, WIN[2] + 1, WIN[3] + 1], outline=FRAME, width=2)

    # Bubbles round the outside: one at each top corner, columns down the sides,
    # a row along the bottom.  Cycling the colour keeps neighbours distinct.
    n = 0
    for cx in (16, W - 16):
        bubble(d, cx, 16, n)
        n += 1
    for cy in range(44, 185, PITCH):
        for cx in (20, W - 20):
            bubble(d, cx, cy, n)
            n += 1
    for cx in range(16, W - 15, PITCH):
        bubble(d, cx, H - 16, n)
        n += 1

    # "PUZZLE BALLOON" at 2x, centred, with a rule under it and the byline below:
    # the title is 2x and the credit 1x, so the name reads as a caption rather
    # than as a second title. The credit is in FRAME, the bezel blue, to keep the
    # gold title the only thing shouting.
    text = "PUZZLE BALLOON"
    scale = 2
    tw = len(text) * 6 * scale - scale
    tx = (W - tw) // 2
    draw_text(d, font, tx, 6, text, scale, TITLE)
    d.rectangle([tx, 22, tx + tw - 1, 22], fill=FRAME)

    credit = "BY ZAPSKI"
    cw = len(credit) * 6 - 1
    draw_text(d, font, (W - cw) // 2, 26, credit, 1, FRAME)

    # The game window: the SGB shows the GB screen here, so it stays empty.
    d.rectangle([WIN[0], WIN[1], WIN[2] - 1, WIN[3] - 1], fill=(0, 0, 0, 0))

    im.save(OUT_PNG)
    colours = {c for _, c in im.getcolors(1 << 16)}
    print("%s: %dx%d, %d colours (limit 16)" % (OUT_PNG, W, H, len(colours)))
    assert len(colours) <= 16, "one SGB palette holds 16 colours"
    # The window is the hole the SGB shows the Game Boy's screen through, and it
    # is the ONLY transparent part: any other transparent pixel is a hole in the
    # border, whatever happens to be behind it.  So the clear pixels must count
    # out to the window exactly.
    clear = sum(n for n, c in im.getcolors(1 << 16) if c[3] == 0)
    want = (WIN[2] - WIN[0]) * (WIN[3] - WIN[1])
    assert clear == want, "%d transparent pixels, window is %d" % (clear, want)

    # Easy to think this script is the whole job: it only writes the PNG, and
    # `make` alone will not pick it up (border_data.c is generated, and only by
    # `make border`). Skip the second step and the ROM keeps the old border,
    # which looks exactly like the change not working.
    print("next: make border && make   # PNG -> border_data.c -> ROM")


if __name__ == "__main__":
    main()
