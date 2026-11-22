#!/usr/bin/env python3
"""Draws the 'GTA Lektor' Dock icon (original art: retro sunset + microphone + PL badge). Usage: ikona.py out.png"""
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

S = 1024
M, R = 100, 185  # macOS-style margin and corner radius
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def gradient(w, h, stops):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / (h - 1)
        for i in range(len(stops) - 1):
            (t0, c0), (t1, c1) = stops[i], stops[i + 1]
            if t0 <= t <= t1:
                k = (t - t0) / (t1 - t0)
                c = tuple(int(c0[j] + (c1[j] - c0[j]) * k) for j in range(3))
                break
        for x in range(w):
            px[x, y] = c
    return img


def main(out, pelne=False):
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((M, M, S - M, S - M), R, fill=255)

    # sky: night purple -> magenta -> orange at the horizon
    art = gradient(S, S, [(0.0, (34, 12, 74)), (0.42, (176, 38, 120)), (0.66, (255, 122, 69)), (1.0, (255, 170, 80))])
    d = ImageDraw.Draw(art)
    # striped retro sun sitting on the horizon
    sun = Image.new("L", (S, S), 0)
    sd = ImageDraw.Draw(sun)
    cx, cy, r = S // 2, 640, 250
    sd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    for i, y in enumerate(range(cy - 40, cy + r, 38)):
        sd.rectangle((0, y, S, y + 8 + i * 3), fill=0)
    sun_col = gradient(S, S, [(0.0, (255, 236, 120)), (0.6, (255, 200, 90)), (1.0, (255, 120, 80))])
    art.paste(sun_col, (0, 0), sun)
    # sea below the horizon
    d.rectangle((0, 700, S, S), fill=(52, 20, 92))
    for i, y in enumerate(range(712, S, 26)):
        d.line((M + 40 + i * 10, y, S - M - 40 - i * 10, y), fill=(255, 140, 110), width=3)

    # microphone (white with soft shadow)
    mic = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    md = ImageDraw.Draw(mic)
    white, dark = (255, 255, 255, 255), (40, 16, 70, 255)
    md.rounded_rectangle((432, 230, 592, 520), 80, fill=white, outline=dark, width=10)
    for y in range(290, 480, 34):
        md.line((462, y, 562, y), fill=(200, 190, 215, 255), width=8)
    md.arc((380, 330, 644, 600), 0, 180, fill=white, width=26)
    md.rectangle((499, 598, 525, 680), fill=white)
    md.rounded_rectangle((420, 668, 604, 698), 14, fill=white)
    # Shadows are COMPOSITED as layers: paste()/draw with a translucent colour would punch semi-transparent
    # holes into the icon body, and macOS then puts the icon in a grey frame ("legacy icon").
    blur = mic.split()[3].filter(ImageFilter.GaussianBlur(14))
    shadow = Image.new("RGBA", (S, S), (20, 0, 40, 0))
    shadow.putalpha(blur.point(lambda v: v * 150 // 255))
    art_rgba = art.convert("RGBA")
    art_rgba.alpha_composite(shadow, dest=(10, 14))
    art_rgba.alpha_composite(mic)

    # "LEKTOR" label (drawn on its own layer, then composited)
    label = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(label)
    font = ImageFont.truetype(BOLD, 128)
    text = "LEKTOR"
    w = d2.textlength(text, font=font)
    d2.text(((S - w) / 2 + 5, 742 + 6), text, font=font, fill=(30, 8, 60, 200))
    d2.text(((S - w) / 2, 742), text, font=font, fill=(255, 255, 255, 255))
    art_rgba.alpha_composite(label)

    # Polish flag badge
    bx, by, bw, bh = 640, 150, 200, 124
    badge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)
    bd.rounded_rectangle((bx, by, bx + bw, by + bh), 22, fill=(220, 20, 60, 255), outline=(255, 255, 255, 255), width=8)
    bd.rounded_rectangle((bx + 4, by + 4, bx + bw - 4, by + bh // 2), 18, fill=(255, 255, 255, 255))
    bd.rectangle((bx + 4, by + bh // 2 - 18, bx + bw - 4, by + bh // 2), fill=(255, 255, 255, 255))
    fpl = ImageFont.truetype(BOLD, 62)
    bd.text((bx + bw / 2 - bd.textlength("PL", font=fpl) / 2, by + bh / 2 - 6), "PL", font=fpl, fill=(255, 255, 255, 255))
    art_rgba.alpha_composite(badge)

    if pelne:  # full-bleed square of the icon body — ikona.swift (ANGIELSKI) clips it into the macOS shape
        art_rgba.crop((M, M, S - M, S - M)).resize((S, S), Image.LANCZOS).convert("RGB").save(out)  # opaque: iOS paints alpha black
        return
    icon.paste(art_rgba, (0, 0), mask)
    icon.save(out)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--pelne"]
    main(args[0] if args else "ikona.png", pelne="--pelne" in sys.argv)
