#!/usr/bin/env python3
"""Generates the panel artwork (the four "Jak słuchasz?" pictures) via kie.ai (Nano Banana 2.1 by default, GPT Image
as an option).

Optional: the art is already in panel/grafiki/. Needs your own kie.ai key in KIE_API_KEY (never printed).
Usage:
  grafiki.py --saldo                                        # credit balance only
  grafiki.py [--silnik nb|gpt] [--styl S] [--cel DIR] [--wzor sketch.png] [nazwa…]  # render all (or the named) images as PNG
Default target: ../panel/grafiki/ (the panel uses JPEG copies named <nazwa>.jpg).
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.request

BAZA = "https://api.kie.ai/api/v1"
CEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel", "grafiki")

# Neutral "game catalogue" look (E1d, 2026-11-25): calm and generic, not tied to any one game — the older synthwave
# sunset art looked like one specific game. Three candidate styles; the chosen one is the default.
STYLE = {
    "plaski": ("Original artwork, no text, no letters, no logos, no brand names, no real products, no people. "
               "Clean flat vector illustration, simple geometric shapes, subtle soft gradients, muted graphite and "
               "slate greys with a single calm teal accent, gentle long soft shadow, modern app-icon quality. "),
    "glina": ("Original artwork, no text, no letters, no logos, no brand names, no real products, no people. "
              "Soft matte 3D clay render, rounded friendly shapes, muted warm grey and slate colours with a single "
              "calm teal accent, soft studio lighting, subtle ambient occlusion, minimal and tidy. "),
    "kontur": ("Original artwork, no text, no letters, no logos, no brand names, no real products, no people. "
               "Minimal isometric line illustration, even off-white strokes, a few flat fills in slate grey and a "
               "single calm teal accent, precise and tidy, like a premium app onboarding icon. "),
}
# Icons only (operator 2026-11-25: the muted icons were "szarobure", today's vivid sunset icon is liked): today's palette.
STYLE["zywy"] = ("Original artwork, no text, no letters, no logos, no brand names, no real products, no people. Bold flat "
                 "vector illustration with vivid saturated colours: a deep purple to hot magenta to warm orange vertical "
                 "gradient, crisp glossy white and golden-yellow subject with a soft dark purple shadow, cheerful premium "
                 "app-icon quality, no neon glow lines. ")
# Icons, round 3 (operator 2026-11-25: the app icon in the FAMILY of the "Jak słuchasz?" pictures, but stronger than the
# first, too grey icons): the same flat device-icon look, lighter objects, two accents, a richer background.
STYLE["rodzina"] = ("Original artwork, no text, no letters, no logos, no brand names, no real products, no people. Clean flat "
                    "vector illustration in the style of soft modern device icons: rounded geometric shapes, light grey and "
                    "white objects with gentle gradient shading, two clear accents — warm orange (#f28c28) and teal (#2bb3a3) — "
                    "and a soft long shadow; background a rich deep blue (#1b2a4a) to slate radial gradient with a gentle light "
                    "glow behind the subject; high contrast, bold and readable at 32 pixels. ")
STYL_DOMYSLNY = "plaski"
OBIEKT = ("Single centered object group on a plain smooth dark slate (#1d2028) background, generous empty margin "
          "around it, soft shadow under it, no glow, no neon. ")

OBRAZY = {
    "profil_tv_mac": ("1:1", "1K",
            "A modern flat TV on a low stand showing a simple abstract landscape, a wireless game controller in "
            "front of it, and a silver laptop on the right with small sound wave arcs coming from its speakers. "),
    "profil_tv_glosnik": ("1:1", "1K",
            "A modern flat TV on a low stand showing a simple abstract landscape, a wireless game controller in "
            "front of it, and a small cylindrical portable wireless speaker on the right with sound wave arcs "
            "around it. "),
    "profil_sluchawki": ("1:1", "1K",
            "Over-ear wireless headphones resting next to a wireless game controller, small sound wave arcs "
            "around the ear cups. "),
    "profil_iphone": ("1:1", "1K",
            "A modern smartphone standing upright on a small stand, its screen showing a large round play button, "
            "sound wave arcs coming from its bottom speaker, a wireless game controller lying in front of it. "),
}

# Dock icon / iPhone icon art (E1e, 2026-11-25): the old icon (retro sunset) looked like one specific game. The art fills
# the whole square; aplikacja/ikona.py cuts it into the macOS shape and adds the PL badge (letters are never drawn by AI).
TLO_IKONY = ("App icon artwork that fills the ENTIRE square edge to edge: a smooth gradient background in the style's "
             "colours with no border, no frame, no rounded corners, no vignette edge; the "
             "subject centered and occupying about 60 percent of the square, bold and readable even at 32 pixels. ")
IKONY = {
    "ikona_mikrofon": "A classic studio microphone standing in front of a wireless game controller. ",
    "ikona_dymek": "A wireless game controller with a large rounded speech bubble above it, the bubble holding three sound wave arcs. ",
    "ikona_napisy": ("A wireless game controller at the bottom; above it a wide rounded subtitle bar holding two rows of abstract "
                     "text lines (no real letters), and the right end of the bar breaks apart into flowing sound wave arcs — "
                     "subtitles turning into a voice. No microphone. "),
    "ikona_dymek_pad": ("A large rounded speech bubble with its tail at the bottom left; a wireless game controller sits inside the "
                        "bubble, and three sound wave arcs come out of the bubble's right side — the game speaks. No microphone. "),
    "ikona_glos": "A wireless game controller lying at the bottom with a large classic microphone rising behind it and three sound wave arcs on both sides of the microphone. ",
}

MODEL = {"nb": "nano-banana-2-1", "gpt": "gpt-image-2-text-to-image"}
MODEL_WZOR = {"nb": "nano-banana-2-1", "gpt": "gpt-image-2-image-to-image"}  # with a reference image (--wzor)
POLE_WZORU = {"nb": "image_input", "gpt": "input_urls"}
UPLOAD = "https://kieai.redpandaai.co/api/file-base64-upload"
O_WZORZE = ("Use the attached reference image ONLY as a layout sketch: keep its composition, placement and proportions, "
            "and redraw everything as polished final artwork in the style described. ")


def klucz():
    k = os.environ.get("KIE_API_KEY", "").strip()
    if not k:
        sys.exit("Brak klucza kie.ai — ustaw zmienną KIE_API_KEY")
    return k


def http(metoda, sciezka, k, dane=None):
    req = urllib.request.Request(BAZA + sciezka, method=metoda,
                                 data=json.dumps(dane).encode() if dane is not None else None,
                                 headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def wyslij_wzor(k, sciezka):
    """Uploads a reference image (our own sketch) and returns its temporary URL on kie.ai."""
    import base64
    with open(sciezka, "rb") as f:
        dane = base64.b64encode(f.read()).decode()
    typ = "png" if sciezka.lower().endswith(".png") else "jpeg"
    req = urllib.request.Request(UPLOAD, method="POST", headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
                                 data=json.dumps({"base64Data": f"data:image/{typ};base64,{dane}", "fileName": os.path.basename(sciezka),
                                                  "uploadPath": "uploads"}).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    url = (d.get("data") or {}).get("fileUrl") or (d.get("data") or {}).get("downloadUrl")
    if d.get("code") != 200 or not url:
        raise RuntimeError(f"wzór: {d}")
    return url


def saldo(k):
    d = http("GET", "/chat/credit", k)
    if d.get("code") != 200:
        raise RuntimeError(f"saldo: {d}")
    return d.get("data")


def renderuj(nazwa, k, silnik, wyniki, styl=STYL_DOMYSLNY, cel=CEL, wzor=None):
    if nazwa in IKONY:
        aspekt, rozdz, prompt = "1:1", "1K", IKONY[nazwa] + TLO_IKONY + STYLE[styl]
    else:
        aspekt, rozdz, prompt = OBRAZY[nazwa]
        prompt = prompt + OBIEKT + STYLE[styl]
    try:
        wej = {"prompt": (O_WZORZE if wzor else "") + prompt, "aspect_ratio": aspekt, "resolution": rozdz, "output_format": "png"}
        if silnik == "gpt":
            wej.pop("output_format")
        if wzor:
            wej[POLE_WZORU[silnik]] = [wzor]
        d = http("POST", "/jobs/createTask", k, {"model": (MODEL_WZOR if wzor else MODEL)[silnik], "input": wej})
        if d.get("code") != 200 or not (d.get("data") or {}).get("taskId"):
            raise RuntimeError(f"createTask: {d}")
        tid, t0 = d["data"]["taskId"], time.time()
        time.sleep(15)
        while time.time() - t0 < 300:
            info = http("GET", f"/jobs/recordInfo?taskId={tid}", k).get("data") or {}
            stan = info.get("state")
            if stan == "success":
                urls = json.loads(info.get("resultJson") or "{}").get("resultUrls") or []
                if not urls:
                    raise RuntimeError("success bez resultUrls")
                os.makedirs(cel, exist_ok=True)
                plik = os.path.join(cel, f"{nazwa}.png")
                with urllib.request.urlopen(urls[0], timeout=120) as r, open(plik, "wb") as f:
                    f.write(r.read())
                wyniki[nazwa] = f"OK {time.time() - t0:.0f} s → {os.path.normpath(plik)}"
                return
            if stan in ("fail", "failed"):
                raise RuntimeError(f"fail: {info.get('failMsg') or info}")
            time.sleep(6)
        raise RuntimeError("limit czasu 300 s")
    except Exception as e:  # report per image, others keep going
        wyniki[nazwa] = f"BŁĄD: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("nazwy", nargs="*")
    ap.add_argument("--silnik", choices=["nb", "gpt"], default="nb")
    ap.add_argument("--saldo", action="store_true")
    ap.add_argument("--styl", choices=sorted(STYLE), default=STYL_DOMYSLNY)
    ap.add_argument("--cel", default=CEL, help="output folder (default: ../panel/grafiki)")
    ap.add_argument("--wzor", help="reference image (our own layout sketch) sent with every named image")
    a = ap.parse_args()
    k = klucz()
    przed = saldo(k)
    print("saldo kie.ai przed:", przed)
    if a.saldo:
        return
    nazwy = a.nazwy or list(OBRAZY)  # icons only by name (one is chosen; the chosen art lives in aplikacja/ikona-zrodlo.jpg)
    wyniki, watki = {}, []
    wzor = wyslij_wzor(k, a.wzor) if a.wzor else None
    for n in nazwy:
        if n not in OBRAZY and n not in IKONY:
            sys.exit(f"Nie znam obrazu „{n}” — znane: {', '.join(list(OBRAZY) + list(IKONY))}")
        t = threading.Thread(target=renderuj, args=(n, k, a.silnik, wyniki, a.styl, a.cel, wzor))
        t.start()
        watki.append(t)
    for t in watki:
        t.join()
    for n in nazwy:
        print(f"{n:20s} {wyniki.get(n)}")
    po = saldo(k)
    print("saldo kie.ai po:", po, "| zużyto:", round(float(przed) - float(po), 2) if przed is not None else "?")


if __name__ == "__main__":
    main()
