#!/usr/bin/env python3
"""Generates the panel artwork via kie.ai (Nano Banana 2.1 by default, GPT Image as an option).

Optional: the art is already in panel/grafiki/. Needs your own kie.ai key in KIE_API_KEY (never printed).
Usage:
  grafiki.py --saldo                    # credit balance only
  grafiki.py [--silnik nb|gpt] [nazwa…] # render all (or the named) images into ../panel/grafiki/
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

STYL = ("Original artwork, no text, no letters, no logos, no brand names, no real products, no people. "
        "Synthwave dusk palette: deep night purple, magenta, hot pink and warm orange sunset glow, "
        "soft neon rim light, clean modern 3D illustration with gentle gradients, high detail. ")
OBIEKT = ("Single centered object on a plain smooth deep purple (#22104a) background with a faint magenta glow "
          "behind it, generous empty margin around the object, soft shadow under it. ")

OBRAZY = {
    "tlo": ("16:9", "2K",
            "Wide cinematic background for a game companion app: a tropical coastal city at dusk seen from a beach, "
            "silhouetted palm trees, low pink and orange sun on the ocean horizon, distant neon-lit skyline, "
            "calm water reflections, light haze. The left and center of the frame are darker and calm "
            "(room for interface cards), detail concentrated at the right and bottom edges. " + STYL),
    "profil_tv_mac": ("1:1", "1K",
            "A modern flat TV on a low stand showing an abstract colorful sunset game scene, a wireless game "
            "controller in front of it, and a silver laptop on the right with small glowing sound wave arcs "
            "coming from its speakers. " + OBIEKT + STYL),
    "profil_tv_glosnik": ("1:1", "1K",
            "A modern flat TV on a low stand showing an abstract colorful sunset game scene, a wireless game "
            "controller in front of it, and a small cylindrical portable wireless speaker on the right with "
            "glowing sound wave arcs around it. " + OBIEKT + STYL),
    "profil_sluchawki": ("1:1", "1K",
            "Over-ear wireless headphones resting next to a wireless game controller, glowing sound wave arcs "
            "around the ear cups, a tiny abstract sunset reflected on the headband. " + OBIEKT + STYL),
    "profil_iphone": ("1:1", "1K",
            "A modern smartphone standing upright on a small stand, its screen showing an abstract colorful sunset "
            "and a large glowing round play button, glowing sound wave arcs coming from its bottom speaker, "
            "a wireless game controller lying in front of it. " + OBIEKT + STYL),
}

MODEL = {"nb": "nano-banana-2-1", "gpt": "gpt-image-2-text-to-image"}


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


def saldo(k):
    d = http("GET", "/chat/credit", k)
    if d.get("code") != 200:
        raise RuntimeError(f"saldo: {d}")
    return d.get("data")


def renderuj(nazwa, k, silnik, wyniki):
    aspekt, rozdz, prompt = OBRAZY[nazwa]
    try:
        wej = {"prompt": prompt, "aspect_ratio": aspekt, "resolution": rozdz, "output_format": "png"}
        if silnik == "gpt":
            wej.pop("output_format")
        d = http("POST", "/jobs/createTask", k, {"model": MODEL[silnik], "input": wej})
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
                os.makedirs(CEL, exist_ok=True)
                plik = os.path.join(CEL, f"{nazwa}.png")
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
    a = ap.parse_args()
    k = klucz()
    przed = saldo(k)
    print("saldo kie.ai przed:", przed)
    if a.saldo:
        return
    nazwy = a.nazwy or list(OBRAZY)
    wyniki, watki = {}, []
    for n in nazwy:
        t = threading.Thread(target=renderuj, args=(n, k, a.silnik, wyniki))
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
