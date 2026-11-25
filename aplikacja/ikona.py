#!/usr/bin/env python3
"""Makes the Dock icon and the iPhone (PWA) icon of "Lektor Gier na konsoli" from ONE square artwork.

The artwork (aplikacja/ikona-zrodlo.jpg, our own picture made with aplikacja/grafiki.py) fills the whole square; this
script cuts it into the macOS icon shape and draws the PL badge on top (letters are never left to the image model).
Usage: ikona.py out.png [--pelne] [--zrodlo art.png]
  --pelne   full-bleed opaque square for the iPhone icon (iOS rounds the corners itself; alpha would turn black)
Exit: 0 written · 2 the artwork is missing or not a picture (no silent fallback to some other icon).
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

S = 1024
M, R = 100, 185  # macOS-style margin and corner radius
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
ZRODLO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ikona-zrodlo.jpg")


def odznaka(img, x, y, w):
    """Polish flag badge with "PL", drawn on its own layer with a soft shadow, then composited (a translucent colour
    drawn straight onto the icon would punch holes in it, and macOS then frames it in grey as a legacy icon)."""
    h = round(w * 0.62)
    warstwa = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(warstwa)
    d.rounded_rectangle((x, y, x + w, y + h), round(w * 0.11), fill=(220, 20, 60, 255), outline=(255, 255, 255, 255), width=max(4, w // 25))
    d.rounded_rectangle((x + 4, y + 4, x + w - 4, y + h // 2), round(w * 0.09), fill=(255, 255, 255, 255))
    d.rectangle((x + 4, y + h // 2 - round(w * 0.09), x + w - 4, y + h // 2), fill=(255, 255, 255, 255))
    f = ImageFont.truetype(BOLD, round(w * 0.31))
    d.text((x + w / 2 - d.textlength("PL", font=f) / 2, y + h / 2 - round(w * 0.03)), "PL", font=f, fill=(255, 255, 255, 255))
    cien = Image.new("RGBA", img.size, (0, 0, 0, 0))
    cien.putalpha(warstwa.split()[3].filter(ImageFilter.GaussianBlur(10)).point(lambda v: v * 120 // 255))
    img.alpha_composite(cien, dest=(6, 8))
    img.alpha_composite(warstwa)


def main(out, pelne=False, zrodlo=ZRODLO):
    try:
        art = Image.open(zrodlo).convert("RGBA")
    except (OSError, ValueError) as e:
        sys.stderr.write(f"Brak obrazu ikony ({zrodlo}): {e}\n")
        return 2
    if pelne:  # the iPhone icon: the whole artwork, badge in the same place relative to the art
        body = art.resize((S, S), Image.LANCZOS)
        odznaka(body, 640, 70, 260)
        body.convert("RGB").save(out)
        return 0
    body = art.resize((S - 2 * M, S - 2 * M), Image.LANCZOS)
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(body, (M, M))
    odznaka(canvas, 640, 150, 200)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((M, M, S - M, S - M), R, fill=255)
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(canvas, (0, 0), mask)
    icon.save(out)
    return 0


if __name__ == "__main__":
    args, zr = [], ZRODLO
    it = iter(sys.argv[1:])
    for a in it:
        if a == "--zrodlo":
            zr = next(it, "")
        elif a != "--pelne":
            args.append(a)
    sys.exit(main(args[0] if args else "ikona.png", pelne="--pelne" in sys.argv, zrodlo=zr))
