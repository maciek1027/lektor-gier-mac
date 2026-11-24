#!/usr/bin/env python3
"""GTA Lektor — panel server (port 8797): the Mac panel on 127.0.0.1 and an iPhone remote over home Wi-Fi.

Serves panel/index.html and a small JSON API that drives lektor.py: the game catalogue (voice packs in the packs
folder), start/stop the reader, a short sample of a game, live volume, output device, Chrome with Xbox Remote Play,
demo. Requests from this Mac (127.0.0.1) work as before; every request from the network needs the secret key `k`
(kept in ustawienia.json, shown on the Mac panel as a QR code).
Profile "iphone": matched lines are not played on the Mac but sent to the phone (long-poll), which plays them.
Game change: one lock covers change, start and stop; the reader is confirmed stopped before the pack is swapped,
and every phone event carries the game-session number `g` (line numbers are per pack — a line of the old game
must never be played from the new one).
Started by "GTA Lektor.app"; log in ~/Library/Logs/gta-lektor.log.
Test instance: LEKTOR_PORT=8798 LEKTOR_USTAWIENIA=/tmp/x.json .venv/bin/python serwer_panelu.py
"""
import base64
import collections
import colorsys
import hashlib
import hmac
import io
import json
import os
import random
import re
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import Quartz

import lektor as L

# Started from the Dock icon the PATH is minimal (/usr/bin:/bin…): add Homebrew so ffplay/ffmpeg are found.
os.environ["PATH"] = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin") + ":/opt/homebrew/bin:/usr/local/bin"

PORT = int(os.environ.get("LEKTOR_PORT") or 8797)
FOLDER = os.path.dirname(os.path.abspath(__file__))
PANEL = os.path.join(FOLDER, "panel")
USTAWIENIA = os.environ.get("LEKTOR_USTAWIENIA") or os.path.join(FOLDER, "ustawienia.json")
# catalogue cache (pack manifest + cover + colours), outside the program folder: covers belong to the pack authors
PAMIEC = os.environ.get("LEKTOR_PAMIEC") or os.path.expanduser("~/Library/Caches/GTA Lektor/katalog")
# our own record of which pack versions were tested and how (live on the Xbox / on a game recording)
SPRAWDZONE = os.path.join(FOLDER, "sprawdzone.json")
XBOX = "https://www.xbox.com/pl-PL/play/consoles"
USTAWIENIA_EKRANU = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
# glosnosc = the lektor on the Mac; glosnosc_iphone = extra boost on the phone, on top of its side buttons (which a web
# page can neither read nor set) — kept apart so a 200 % phone boost never blasts the Mac speakers.
# gra = catalogue id of the chosen game (pack file name); wyglad = panel background (game theme or a fixed one)
DOMYSLNE = {"profil": "tv_mac", "urzadzenie": "", "glosnosc": 1.0, "glosnosc_iphone": 1.0, "zrodlo": "ekran", "kamera": 0,
            "gra": None, "wyglad": "motyw"}
PROFILE = ("tv_mac", "tv_glosnik", "sluchawki", "iphone")
WYGLADY = ("motyw", "klasyczny", "grafit", "granat")
ID_GRY = re.compile(r"[A-Za-z0-9_.-]{1,80}")
GRA_DOMYSLNA = "Grand Theft Auto VI"  # the only game before the catalogue: an old settings file means this one
LOKALNE = ("127.0.0.1", "::1", "::ffff:127.0.0.1")
# A client counts as present until this long without a poll (seconds). Mac panel behind the game is throttled
# to ~1 poll/min; a phone freezes completely when locked, so it gets a long grace (the remote must not kill
# the voice mid-game just because the phone lies on the table).
LIMIT = {("mac", False): 150, ("mac", True): 900, ("tel", False): 1800, ("tel", True): 3 * 3600}
PUBLICZNE = re.compile(r"/(grafiki/[a-z0-9_]+\.(jpg|png)|ikona-(180|192|512)\.png|czuwanie\.mp4"
                       r"|apple-touch-icon(-precomposed)?\.png|favicon\.ico)")
# names iOS and browsers ask for on their own (measured on the operator's iPhone 2026-11-22: each was a 403)
ALIASY = {"/apple-touch-icon.png": "ikona-180.png", "/apple-touch-icon-precomposed.png": "ikona-180.png",
          "/favicon.ico": "ikona-192.png"}
TYPY = {"jpg": "image/jpeg", "png": "image/png", "mp4": "video/mp4"}
STARY_S = 6  # a phone line older than this is not worth waiting for
ZAJETY = "Lektor właśnie się przełącza (zmiana gry, start albo stop) — spróbuj za chwilę."


def teraz():
    return time.strftime("%H:%M:%S")


def adres_ip():
    """This Mac's address in the home network (UDP connect sends no packet, it only picks the interface)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 9))
        return s.getsockname()[0]
    except OSError:
        for iface in ("en0", "en1"):
            r = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True)
            if r.stdout.strip():
                return r.stdout.strip()
        return None
    finally:
        s.close()


def nazwa_bonjour():
    r = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True)
    return (r.stdout.strip() + ".local") if r.returncode == 0 and r.stdout.strip() else None


def kod_qr(tekst):
    """PNG of a QR code, one pixel per module (the panel scales it up with image-rendering: pixelated)."""
    import AppKit
    from Foundation import NSData
    dane = tekst.encode()
    f = Quartz.CIFilter.filterWithName_("CIQRCodeGenerator")
    f.setValue_forKey_(NSData.dataWithBytes_length_(dane, len(dane)), "inputMessage")
    f.setValue_forKey_("M", "inputCorrectionLevel")
    rep = AppKit.NSBitmapImageRep.alloc().initWithCIImage_(f.outputImage())
    return bytes(rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {}))


_wersja = {"klucz": None, "wartosc": None, "lock": threading.Lock()}


def wersja_strony():
    """Version of panel/index.html — injected into the page and returned by /api/stan: an open page (phone) with
    old JavaScript sees a different value and reloads itself. One source: the file itself (cached by mtime)."""
    plik = os.path.join(PANEL, "index.html")
    with _wersja["lock"]:
        st = os.stat(plik)
        if _wersja["klucz"] != (st.st_mtime_ns, st.st_size):
            with open(plik, "rb") as f:
                _wersja["wartosc"] = hashlib.sha1(f.read()).hexdigest()[:12]
            _wersja["klucz"] = (st.st_mtime_ns, st.st_size)
        return _wersja["wartosc"]


# ---------------------------------------------------------------- settings
def popraw_ustawienie(k, v):
    """Validated value of one settings field, or raises ValueError. An old or hand-edited settings file must never
    stop the server: a bad field falls back to its default (see czytaj_ustawienia)."""
    if k == "profil" and v in PROFILE:
        return v
    if k == "urzadzenie" and isinstance(v, str) and len(v) <= 200:
        return v
    if k in ("glosnosc", "glosnosc_iphone") and isinstance(v, (int, float)) and not isinstance(v, bool):
        return max(0.0, min(2.0, float(v)))
    if k == "zrodlo" and v in ("ekran", "kamera"):
        return v
    if k == "kamera" and isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 9:
        return v
    if k == "gra" and (v is None or (isinstance(v, str) and ID_GRY.fullmatch(v))):
        return v
    if k == "wyglad" and v in WYGLADY:
        return v
    raise ValueError(k)


def czytaj_ustawienia(plik):
    """(settings, key). Every field read with its default; the pilot key is kept only if it has the right shape."""
    ust, klucz = dict(DOMYSLNE), None
    try:
        with open(plik) as f:
            zapis = json.load(f)
    except (OSError, ValueError):
        zapis = {}
    if not isinstance(zapis, dict):
        zapis = {}
    for k, v in zapis.items():
        if k in DOMYSLNE:
            try:
                ust[k] = popraw_ustawienie(k, v)
            except ValueError:
                pass
    if re.fullmatch(r"[A-Za-z0-9_-]{12,64}", str(zapis.get("klucz", ""))):
        klucz = zapis["klucz"]
    return ust, klucz


# ---------------------------------------------------------------- game theme from the cover
def _lin(c):
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def kontrast(a, b):
    """WCAG contrast ratio of two (r, g, b) colours."""
    la, lb = (0.2126 * _lin(x[0]) + 0.7152 * _lin(x[1]) + 0.0722 * _lin(x[2]) for x in (a, b))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


BIALY = (255, 255, 255)


def przyciemnij(rgb, cel=4.5):
    """Lower the lightness (same hue and saturation, so the colour stays vivid) until white text on it has at
    least `cel`:1 contrast. Black always passes, so the loop ends."""
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    wynik = tuple(int(round(c)) for c in rgb)
    while kontrast(BIALY, wynik) < cel and l > 0:
        l = max(0.0, l - 0.01)
        wynik = tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(h, l, s))
    return wynik


def hex_(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def motyw_z_okladki(dane):
    """Accent colours and a dark background from a cover image. Picks the strongest colourful tone (area x
    saturation squared, mid lightness preferred — a cover is mostly dark scenery, the brand colour is small and
    vivid), then darkens it until white text has >= 4.5:1 — the play button's gradient runs between akcent and
    akcent2, so both ends are checked."""
    from PIL import Image
    im = Image.open(io.BytesIO(dane)).convert("RGB")
    im.thumbnail((96, 96))
    q = im.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    kandydaci = []
    for n, i in q.getcolors():
        rgb = tuple(pal[3 * i:3 * i + 3])
        h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
        waga = n * (0.05 + s) ** 2 * max(0.05, 1 - 1.6 * abs(l - 0.5))
        kandydaci.append((waga, h, rgb))
    kandydaci.sort(reverse=True)
    _, h1, rgb1 = kandydaci[0]
    drugi = next((rgb for _, h, rgb in kandydaci[1:] if min(abs(h - h1), 1 - abs(h - h1)) > 0.06), None)
    if drugi is None:  # one-colour cover: a lighter twin of the first tone
        drugi = tuple(min(255, int(c * 1.25 + 20)) for c in rgb1)
    akcent, akcent2 = przyciemnij(rgb1), przyciemnij(drugi)
    tlo = tuple(int(c * 0.16) + 8 for c in rgb1)
    return {"akcent": hex_(akcent), "akcent2": hex_(akcent2), "tlo": hex_(tlo),
            "kontrast": round(min(kontrast(BIALY, akcent), kontrast(BIALY, akcent2)), 2),
            "kontrast_tla": round(kontrast(BIALY, tlo), 2)}


# ---------------------------------------------------------------- catalogue (packs on disk)
STANY = {  # tile state -> short label (the panel shows the longer explanation from stan_opis)
    "sprawdzona": "Sprawdzona na Xboxie",
    "nagranie": "Próba na nagraniu gry",  # the explanation (not yet on the Xbox) is in stan_opis
    "niesprawdzona": "Niesprawdzona",
    "ryzyko": "Może gubić kwestie",
    "nie_na_macu": "Na razie nie na Macu",
    "wczytuje": "Wczytuję…",
    "blad": "Błąd paczki",
    "nie_na_dysku": "W bibliotece — nie na dysku",  # E1c (download) fills this one; the panel already draws it
}


def id_z_pliku(nazwa):
    return re.sub(r"[^A-Za-z0-9_.-]", "-", os.path.splitext(nazwa)[0])[:80] or "paczka"


class Katalog:
    """Voice packs in the packs folder: per pack only manifest.json, preset/settings and the cover are read (never
    subtitles.json — GTA VI has 373 thousand lines), in a background thread, cached outside the program folder and
    invalidated by file size and date. Folder states are told apart: no disk, no folder, empty folder, read error."""

    def __init__(self, folder, pamiec, sprawdzone=SPRAWDZONE):
        self.folder, self.pamiec, self.plik_dowodow = folder, pamiec, sprawdzone
        self.lock = threading.Lock()
        self.skanuje = threading.Lock()
        self.stan, self.komunikat = "skanuje", "Sprawdzam folder paczek…"
        self.gry = {}  # id -> entry (with "plik" and "okladka_plik", which never leave the server)
        self.nr = 0  # bumped when anything visible changes: the panel refetches the catalogue
        self.odcisk = None

    def _ustaw(self, stan, komunikat, gry=None):
        with self.lock:
            nowe = (stan, komunikat, gry if gry is not None else self.gry)
            if nowe != (self.stan, self.komunikat, self.gry):
                self.stan, self.komunikat = stan, komunikat
                if gry is not None:
                    self.gry = gry
                self.nr += 1

    def _brak_folderu(self, blad):
        czesci = os.path.abspath(self.folder).split(os.sep)
        if len(czesci) > 2 and czesci[1] == "Volumes" and not os.path.ismount(os.path.join("/Volumes", czesci[2])):
            return "brak_dysku", (f"Nie widzę dysku „{czesci[2]}” z paczkami lektora. Podłącz dysk — katalog "
                                  f"pokaże gry sam (albo kliknij „Sprawdź ponownie”).")
        if not os.path.exists(self.folder):
            return "brak_folderu", (f"Nie ma folderu paczek ({self.folder}). Utwórz go i wrzuć do niego paczkę .zip "
                                    f"z gamereader.pl albo wskaż inny folder (LEKTOR_PACZKI w lokalne.env).")
        return "blad", (f"Nie mogę odczytać folderu paczek ({blad}). Sprawdź, czy dysk działa i czy program ma "
                        f"dostęp do tego folderu, potem kliknij „Sprawdź ponownie”.")

    def dowody(self):
        try:
            with open(self.plik_dowodow, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def skanuj(self, czekaj=False):
        """One pass over the folder (call in a background thread). Cheap when nothing changed: listdir + stat.
        czekaj=True waits for a pass already running (the game loader needs a finished catalogue)."""
        if not self.skanuje.acquire(blocking=czekaj):
            return
        try:
            try:
                nazwy = L.pliki_w_folderze(self.folder, ".zip")
                staty = {n: os.stat(os.path.join(self.folder, n)) for n in nazwy}
            except OSError as e:
                self.odcisk = None
                return self._ustaw(*self._brak_folderu(e), gry={})
            if not nazwy:
                self.odcisk = ()
                return self._ustaw("pusty", ("W folderze paczek nie ma jeszcze żadnej gry. Pobierz paczkę lektora "
                                             "z gamereader.pl i wrzuć plik .zip do folderu paczek."), gry={})
            mp4 = self._filmy()
            try:
                dowody_t = os.stat(self.plik_dowodow).st_mtime_ns
            except OSError:
                dowody_t = None
            # demo videos and the test-record file change the tiles too (demo button, state)
            odcisk = (tuple((n, staty[n].st_size, staty[n].st_mtime_ns) for n in nazwy), tuple(mp4), dowody_t)
            if odcisk == self.odcisk:
                return
            dowody = self.dowody()
            ids, gry = {}, {}
            for n in nazwy:  # placeholders first: the tiles show up at once, "wczytuję…"
                i, nr = id_z_pliku(n), 2
                while i in gry:  # two names that clean up the same: suffix, still within 80 characters
                    i, nr = id_z_pliku(n)[:76] + f"-{nr}", nr + 1
                ids[n] = i
                stary = self.gry.get(i)
                gry[i] = stary if stary and stary.get("plik") == os.path.join(self.folder, n) else {
                    "id": i, "plik": os.path.join(self.folder, n), "nazwa": os.path.splitext(n)[0], "stan": "wczytuje",
                    "stan_opis": "Czytam opis i okładkę paczki…"}
            self._ustaw("ok", None, gry=dict(gry))
            for n in nazwy:
                wpis = self._wpis(os.path.join(self.folder, n), staty[n], ids[n])
                self._stan_kafelka(wpis, dowody)
                wpis["demo_plik"] = self._demo(n, wpis, mp4)
                gry[ids[n]] = wpis
                self._ustaw("ok", None, gry=dict(gry))
            self.odcisk = odcisk
        finally:
            self.skanuje.release()

    def _filmy(self):
        try:
            return L.pliki_w_folderze(self.folder, ".mp4")
        except OSError:
            return []

    def _demo(self, nazwa, wpis, mp4):
        """Demo video of this game: `<pack name>.mp4` next to the pack; the old single demo (LEKTOR_DEMO, else the
        first other .mp4 in the folder) belongs to GTA VI only — RE2 must not offer the GTA VI scene."""
        wlasny = os.path.splitext(nazwa)[0] + ".mp4"
        if wlasny in mp4:
            return os.path.join(self.folder, wlasny)
        if not self.jest_domyslna(wpis):
            return None
        demo = os.environ.get("LEKTOR_DEMO")
        if not demo:  # the first .mp4 that is not another game's own demo
            try:
                paczki = {os.path.splitext(n)[0] for n in L.pliki_w_folderze(self.folder, ".zip")}
            except OSError:
                paczki = set()
            inne = [m for m in mp4 if os.path.splitext(m)[0] not in paczki]
            demo = os.path.join(self.folder, inne[0]) if inne else None
        return demo if demo and os.path.exists(demo) else None

    def jest_domyslna(self, wpis):
        """The game an old installation meant: the pack named by LEKTOR_PACZKA, else the GTA VI pack."""
        wskazana = os.environ.get("LEKTOR_PACZKA")
        if wskazana:
            try:
                return os.path.samefile(wskazana, wpis["plik"])
            except OSError:
                return False
        return wpis.get("nazwa") == GRA_DOMYSLNA

    def domyslna(self):
        with self.lock:
            return next((i for i, w in self.gry.items() if w.get("stan") != "wczytuje" and self.jest_domyslna(w)), None)

    def _wpis(self, plik, st, i):
        katalog = os.path.join(self.pamiec, i)
        meta_plik = os.path.join(katalog, "meta.json")
        try:
            with open(meta_plik, encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("rozmiar") == st.st_size and meta.get("mtime_ns") == st.st_mtime_ns and meta.get("plik") == plik:
                return meta["wpis"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        wpis = {"id": i, "plik": plik, "rozmiar": st.st_size}
        okladka = None
        try:
            with zipfile.ZipFile(plik) as z:
                nazwy = set(z.namelist())
                if "manifest.json" not in nazwy or "subtitles.json" not in nazwy:
                    raise ValueError("brak manifest.json albo subtitles.json — to nie paczka GameReadera 2.0")
                man = json.loads(z.read("manifest.json"))
                preset = json.loads(z.read("preset.json")) if "preset.json" in nazwy else {}
                ustaw = json.loads(z.read("settings.json")) if "settings.json" in nazwy else {}
                if "images/cover.jpg" in nazwy:
                    okladka = z.read("images/cover.jpg")
            if not isinstance(man, dict) or not isinstance(preset, dict) or not isinstance(ustaw, dict):
                raise ValueError("pliki opisu paczki mają zły kształt")
            rois = preset.get("rois") or []
            wymagania = [str(r.get("description", "")).strip() for r in (man.get("requirements") or [])
                         if isinstance(r, dict) and str(r.get("description", "")).strip()]
            wpis.update(nazwa=str(man.get("name") or os.path.splitext(os.path.basename(plik))[0]),
                        autor=str(man.get("author") or ""), wersja=str(man.get("version") or ""),
                        opis=str(man.get("description") or ""), id_paczki=str(man.get("id") or ""),
                        wymagania=wymagania, obszar=bool(rois and isinstance(rois[0], dict) and rois[0].get("rect")),
                        uwagi=L.nieobslugiwane(preset, ustaw), blad=None)
        except Exception as e:  # zipfile.BadZipFile, JSON, missing files: the tile says what is wrong
            wpis.update(nazwa=os.path.splitext(os.path.basename(plik))[0],
                        blad=f"Paczka uszkodzona albo nie z GameReadera 2.0 ({e}). Pobierz ją jeszcze raz.")
        wpis["okladka_plik"] = None
        try:
            os.makedirs(katalog, exist_ok=True)
            if okladka:
                wpis["okladka_plik"] = os.path.join(katalog, "okladka.jpg")
                with open(wpis["okladka_plik"], "wb") as f:
                    f.write(okladka)
                try:
                    wpis["motyw"] = motyw_z_okladki(okladka)
                except Exception:
                    wpis["motyw"] = None
            with open(meta_plik, "w", encoding="utf-8") as f:
                json.dump({"plik": plik, "rozmiar": st.st_size, "mtime_ns": st.st_mtime_ns, "wpis": wpis}, f,
                          ensure_ascii=False)
        except OSError:  # no cache: the catalogue still works, it just reads the pack again next time
            if okladka and not (wpis["okladka_plik"] and os.path.exists(wpis["okladka_plik"])):
                wpis["okladka_plik"] = None
        return wpis

    @staticmethod
    def _stan_kafelka(w, dowody):
        """Tile state. Decides: a test record of this pack version (live on the Xbox / on a game recording), then the
        pack fields the reader does not apply that can lose lines (czytanie). Not the bare list of unhandled fields:
        GTA VI has two and reads the demo 14/14."""
        if w.get("blad"):
            w.update(stan="blad", stan_opis=w["blad"])
            return
        if not w.get("obszar"):
            w.update(stan="nie_na_macu", stan_opis="Paczka nie ma obszaru napisów (presetu) — ten czytnik nie wie, "
                                                   "gdzie na ekranie szukać tekstu.")
            return
        d = dowody.get(w.get("id_paczki") or "")
        if isinstance(d, dict) and d.get("wersja") == w.get("wersja"):
            if d.get("rodzaj") == "na_zywo":
                w.update(stan="sprawdzona", stan_opis=f"Sprawdzona na żywo: {d.get('jak', '')} ({d.get('data', '')}).")
                return
            if d.get("rodzaj") == "nagranie":
                w.update(stan="nagranie", stan_opis=f"Czyta napisy z nagrania tej gry: {d.get('jak', '')} "
                                                    f"({d.get('data', '')}). Na Xboxie jeszcze nie sprawdzona.")
                return
        ryzyko = [u for u in (w.get("uwagi") or []) if u.get("czytanie")]
        if ryzyko:
            w.update(stan="ryzyko", stan_opis="Paczka używa ustawień, których ten czytnik nie stosuje: "
                     + "; ".join(f"{u['pole']} ({u['opis']})" for u in ryzyko) + ". Może gubić kwestie.")
        else:
            w.update(stan="niesprawdzona", stan_opis="Czytnik obsługuje ustawienia tej paczki, ale nikt jej jeszcze "
                                                     "nie sprawdził na grze.")

    def wpis(self, i):
        with self.lock:
            w = self.gry.get(i)
            return dict(w) if w else None

    def okladka(self, i):
        """Cover bytes by catalogue id only (dictionary lookup — no path is ever built from the request)."""
        w = self.wpis(i)
        if not w or not w.get("okladka_plik"):
            return None
        try:
            with open(w["okladka_plik"], "rb") as f:
                return f.read()
        except OSError:
            return None

    def publiczne(self):
        with self.lock:
            gry = [{k: v for k, v in w.items() if k not in ("plik", "okladka_plik", "demo_plik")}
                   | {"okladka": bool(w.get("okladka_plik")), "demo": bool(w.get("demo_plik")),
                      "stan_nazwa": STANY.get(w.get("stan"), "")}
                   for w in self.gry.values()]
            return {"stan": self.stan, "komunikat": self.komunikat, "nr": self.nr,
                    "gry": sorted(gry, key=lambda g: g.get("nazwa", "").lower())}


# ---------------------------------------------------------------- phone as the speaker
class GlosTelefon:
    """Stand-in for lektor.Glos when the phone is the speaker: same methods, but a matched line becomes an
    event for the phone(s) listening on /api/telefon/kwestie; the phone fetches the clip and plays it."""

    def __init__(self, stan, glosnosc):
        self.stan, self.glosnosc, self.na_info = stan, glosnosc, print  # glosnosc is read by the phones
        self.gra_do = 0.0  # server time when the phone says the current line ends

    def start(self):
        pass

    def dodaj(self, idx):
        self.stan.nadaj({"typ": "kwestia", "idx": idx})

    def zajety(self):
        return time.time() < self.gra_do or self.stan.czeka_na_telefon()

    def zamknij(self):
        self.stan.nadaj({"typ": "stop"})


class Stan:
    def __init__(self, ustawienia=None, folder=None, pamiec=None, sprawdzone=SPRAWDZONE):
        self.plik_ust = ustawienia or USTAWIENIA
        self.katalog = Katalog(folder or L.PACZKI, pamiec or PAMIEC, sprawdzone)
        self.lock = threading.RLock()
        # THE one lock for game change, start, stop, sample and pack reload: taken without waiting — busy = a refusal
        # with a message, never a queue (two screens clicking at once must not leave two voices or a half-swapped pack)
        self.sterowanie = threading.Lock()
        self.p = self.m = None
        self.gra = None  # catalogue id of the loaded pack
        self.sesja_gry = 0  # bumped on every pack swap; carried by every phone event and clip request
        self.paczka_blad = "wczytuję paczkę…"
        self.alarm = None  # stays on screen after the reader stopped by itself (e.g. the disk went away)
        self.sesja = None  # dict: watek, stop, glos, zrodlo, profil, start, okno
        self.kwestie = collections.deque(maxlen=6)
        self.komunikaty = collections.deque(maxlen=30)
        self.licz = collections.Counter()
        self.start_t = time.time()
        self.klienci = {}  # id -> {"t": last poll, "tel": from the network, "ip"}
        self.zamknij_o = None
        # events for phones (profile "iphone"): cursor-based so a reconnecting phone misses nothing
        self.cv = threading.Condition()
        self.zdarzenia = collections.deque(maxlen=50)
        self.nr = 0
        self.zgloszone = 0  # last event number a phone reported as played or skipped
        self.sluchacze = {}  # phone id -> listening until (server time)
        self.opoznienia = collections.deque(maxlen=20)  # ms from match to the phone's "playing" report
        self.test_tel = None  # last self-test result from a phone
        self.bonjour = nazwa_bonjour()
        self.qr_cache = {}
        self.ust, self.klucz = czytaj_ustawienia(self.plik_ust)
        if self.klucz is None:
            self.nowy_klucz()

    def uruchom(self):
        """Background start: scan the catalogue, then load the chosen game (holding the one lock)."""
        self.sterowanie.acquire()
        threading.Thread(target=self._odswiez, daemon=True).start()

    # ---------------------------------------------------------- pack / game
    def wybrana(self):
        """Which game to load: the saved choice; else the game an old installation meant (LEKTOR_PACZKA, else GTA VI);
        else none (the panel opens the catalogue). A saved choice whose pack is missing stays chosen."""
        g = self.ust.get("gra")
        if g:
            return g
        return self.katalog.domyslna()

    def _odswiez(self):
        """Rescan + (re)load the chosen game. Runs with self.sterowanie held by the caller; releases it."""
        try:
            self.katalog.skanuj(czekaj=True)
            g = self.wybrana()
            if g is None:
                kat = self.katalog.publiczne()
                with self.lock:
                    self.paczka_blad = (kat["komunikat"] if kat["stan"] != "ok" else
                                        "Wybierz grę w katalogu „Moje gry”.")
                return
            ok, blad = self._wczytaj(g)
            with self.lock:
                if ok and not self.ust.get("gra"):  # remembered from now on (an old settings file had no game field)
                    self.ust["gra"] = g
                if not ok and self.p is not None:  # the old pack stays loaded — but the failed reload is said
                    self.alarm = f"Nie wczytałem gry od nowa: {blad}"
                elif ok:
                    self.alarm = None
        finally:
            self.sterowanie.release()

    def _wczytaj(self, g):
        """Load pack `g` and swap it in. Caller holds self.sterowanie and has stopped the reader. On failure the old
        pack stays (and paczka_blad is set only when there is no pack at all). Returns (ok, error text)."""
        w = self.katalog.wpis(g)
        if w is None:
            kat = self.katalog.publiczne()
            blad = kat["komunikat"] if kat["stan"] != "ok" else (
                "Nie widzę paczki wybranej gry w folderze paczek — podłącz dysk albo wybierz grę w katalogu.")
        elif w.get("stan") == "nie_na_macu":
            blad = f"{w.get('nazwa')}: {w.get('stan_opis')}"
        else:
            blad = None
        if blad is None:
            try:
                p = L.Paczka(w["plik"])
                m = L.Dopasowanie(p)
            except BaseException as e:  # Paczka uses sys.exit on a broken file
                blad = f"Paczka uszkodzona albo nie z GameReadera 2.0: {e}"
        if blad:
            with self.lock:
                if self.p is None:
                    self.paczka_blad = blad
            return False, blad
        with self.lock:
            stary = self.p
            self.p, self.m, self.gra, self.paczka_blad = p, m, g, None
            with self.cv:  # the swap: new session number, old lines gone, waiting long-polls answered at once
                self.sesja_gry += 1
                self.zdarzenia = collections.deque((e for e in self.zdarzenia if e["typ"] != "kwestia"), maxlen=50)
                self._nadaj({"typ": "zmiana_gry"})
            self.kwestie.clear()
        if stary is not None:
            stary.zamknij()
        self.info(f"Paczka gotowa: {p.manifest.get('name', '?')} v{p.manifest.get('version', '?')}, {len(p.ids)} kwestii")
        return True, None

    def _zmien_gre(self, g):
        """Caller holds self.sterowanie. The reader must be CONFIRMED stopped before the pack is swapped."""
        if not ID_GRY.fullmatch(str(g)) or self.katalog.wpis(g) is None:
            return {"ok": False, "blad": "Nie widzę tej gry w folderze paczek — odśwież katalog."}
        if not self._stop():
            return {"ok": False, "blad": "Lektor nie zatrzymał się — gry nie zmieniam. Spróbuj jeszcze raz."}
        ok, blad = self._wczytaj(g)
        if not ok:
            return {"ok": False, "blad": blad}
        with self.lock:
            self.ust["gra"] = g
            self.zapisz_ustawienia()
        return {"ok": True}

    def odswiez(self):
        """„Sprawdź ponownie”: rescan the folder and reload the chosen game (in the background)."""
        if not self.sterowanie.acquire(blocking=False):
            return {"ok": False, "blad": ZAJETY}
        if self.sesja is not None:
            self.sterowanie.release()
            return {"ok": False, "blad": "Zatrzymaj lektora, zanim wczytam paczkę od nowa."}
        threading.Thread(target=self._odswiez, daemon=True).start()
        return {"ok": True}

    def pilnuj(self):
        """Called by the watchdog every few seconds: catalogue changes (a pack added or removed), and the pack of a
        running session vanishing (disk unplugged) = stop with a message, never silence."""
        threading.Thread(target=self.katalog.skanuj, daemon=True).start()
        with self.lock:
            s, p = self.sesja, self.p
        if s is not None and p is not None and not os.path.exists(p.zip.filename):
            ok = self.stop(czekaj=True)["ok"]
            with self.lock:  # said in both cases — never silence
                self.alarm = ("Paczka gry zniknęła z dysku (odłączony dysk?) — " + (
                    "lektor zatrzymany. Podłącz dysk i kliknij „Graj”." if ok else
                    "nie mogę zatrzymać lektora, kliknij „Zatrzymaj”. Podłącz dysk."))
            self.info("! " + self.alarm)

    def zapisz_ustawienia(self):
        try:  # the key lives next to the settings but never in self.ust (which /api/stan returns)
            with open(self.plik_ust, "w") as f:
                json.dump(dict(self.ust, klucz=self.klucz), f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def nowy_klucz(self):
        with self.lock:
            self.klucz = secrets.token_urlsafe(12)
            self.qr_cache.clear()
            self.zapisz_ustawienia()

    def info(self, tekst):
        with self.lock:
            self.komunikaty.append((teraz(), str(tekst)))
        print(teraz(), tekst, flush=True)

    # ---------------------------------------------------------- clients (Mac panel windows, phones)
    def zglos(self, cid, tel, ip):
        with self.lock:
            if tel and not any(k["tel"] for k in self.klienci.values()):
                self.info(f"📱 Pilot połączony ({ip}).")
            self.klienci[cid] = {"t": time.time(), "tel": tel, "ip": ip}
            self.zamknij_o = None

    def obecni(self):
        t, czyta = time.time(), self.sesja is not None
        with self.lock:
            for cid, k in list(self.klienci.items()):
                if t - k["t"] > LIMIT[("tel" if k["tel"] else "mac", czyta)]:
                    del self.klienci[cid]
            return dict(self.klienci)

    def koniec(self, cid):
        """A Mac panel window closed. Shut down only when no other client (another window, a phone) is left."""
        with self.lock:
            self.klienci.pop(cid, None)
            if not self.obecni():
                self.zamknij_o = time.time() + 5  # a reload polls within 5 s and cancels this
                self.info("Panel zamyka okno…")

    # ---------------------------------------------------------- phone as the speaker
    def _nadaj(self, ev):  # caller holds self.cv
        self.nr += 1
        ev.update(n=self.nr, t=time.time(), g=self.sesja_gry)
        self.zdarzenia.append(ev)
        self.cv.notify_all()

    def nadaj(self, ev):
        with self.cv:
            self._nadaj(ev)

    def czekaj_na_zdarzenia(self, od, ile_s):
        """Long-poll. Returns (cursor, game session, events of the CURRENT game session only): a poll waiting
        when the game changes is woken by the change event and gets no line of the old game."""
        with self.cv:
            if od > self.nr:  # the phone remembers a server from before a restart: start counting afresh
                od = -1
            if od >= 0:
                self.cv.wait_for(lambda: self.nr > od, ile_s)
            t, g = time.time(), self.sesja_gry
            return self.nr, g, [{**{k: v for k, v in e.items() if k != "t"}, "wiek_ms": int(1000 * (t - e["t"]))}
                                for e in self.zdarzenia if od >= 0 and e["n"] > od and e["g"] == g]

    def czeka_na_telefon(self):
        with self.cv:
            return any(e["typ"] == "kwestia" and e["n"] > self.zgloszone and time.time() - e["t"] < STARY_S
                       for e in self.zdarzenia)

    def telefon_slucha(self):
        t = time.time()
        with self.lock:
            return any(v > t for v in self.sluchacze.values())

    def zagrane(self, d, ip):
        """A phone reports a line it started playing (or skipped): latency measured on the server clock only."""
        try:
            n = int(d.get("n", 0))
        except (TypeError, ValueError):
            return
        with self.cv:
            ev = next((e for e in self.zdarzenia if e["n"] == n), None)
            self.zgloszone = max(self.zgloszone, n)
        if ev is None:
            return
        ms = int(1000 * (time.time() - ev["t"]))
        typ = str(d.get("typ", ""))[:12]
        if typ == "gra":
            self.opoznienia.append(ms)
            with self.lock:
                s = self.sesja
            try:
                dlugosc = max(0.0, min(60.0, float(d.get("dlugosc_ms") or 0) / 1000))
            except (TypeError, ValueError):
                dlugosc = 0.0
            if s and isinstance(s["glos"], GlosTelefon):
                s["glos"].gra_do = time.time() + dlugosc
            szcz = ", ".join(f"{k} {int(d[k])} ms" for k in ("pobranie_ms", "dekodowanie_ms", "czekanie_ms")
                             if isinstance(d.get(k), (int, float)))
            tel = f"telefon liczy {int(d['wiek_ms'])} ms; " if isinstance(d.get("wiek_ms"), (int, float)) else ""
            self.info(f"📱 kwestia #{n} gra w telefonie {ms} ms po dopasowaniu wg Maca ({tel}{d.get('format', '?')}"
                      f"{', w paczce odpowiedzi' if d.get('w_odpowiedzi') else ''}; {szcz})")
        else:
            self.info(f"📱 kwestia #{n} pominięta w telefonie ({typ}, {ms} ms po dopasowaniu)")

    def dzwiek(self, idx, fmt, g=None):
        """Clip for the phone: Ogg Opus straight from the pack, or WAV (Core Audio afconvert, ~20-30 ms).
        `g` = the game session the phone asks for: a different one (the game changed meanwhile) or none (an old page
        from before the update) gets nothing — the same line number in the new pack is a different line."""
        with self.lock:
            p, biezaca = self.p, self.sesja_gry
        if p is None or g != biezaca or not (0 <= idx < len(p.ids)) or not p.audio[idx]:  # no number = no clip
            return None, None
        try:
            dane = p.dzwiek(idx)
        except (ValueError, OSError, KeyError):  # pack closed by a swap a moment ago, or the disk went away
            return None, None
        if fmt != "wav":
            return dane, "audio/ogg"
        with tempfile.TemporaryDirectory(prefix="lektor-tel-") as tmp:
            src, dst = os.path.join(tmp, "k.opus"), os.path.join(tmp, "k.wav")
            with open(src, "wb") as f:
                f.write(dane)
            r = subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@48000", "-c", "1", src, dst],
                               capture_output=True)
            if r.returncode != 0:
                return None, None
            with open(dst, "rb") as f:
                return f.read(), "audio/wav"

    # ---------------------------------------------------------- devices
    def urzadzenia(self, odswiez=False):
        import sounddevice as sd
        with self.lock:
            wolno = self.sesja is None and not self.sterowanie.locked()
        if odswiez and wolno:
            try:  # PortAudio caches the device list: re-scan so newly connected Bluetooth devices show up
                sd._terminate()
                sd._initialize()
            except Exception:
                pass
        try:
            dom = sd.default.device[1]
            return [{"nazwa": d["name"], "domyslne": i == dom}
                    for i, d in enumerate(sd.query_devices()) if d["max_output_channels"] > 0]
        except Exception as e:
            self.info(f"! Nie mogę wypisać urządzeń dźwięku: {e}")
            return []

    def indeks_urzadzenia(self, nazwa):
        import sounddevice as sd
        if not nazwa:
            return None
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0 and d["name"] == nazwa:
                return i
        self.info(f"! Nie ma teraz urządzenia „{nazwa}” — gram na domyślnym wyjściu Maca.")
        return None

    # ---------------------------------------------------------- session
    def start(self, zrodlo, profil, urzadzenie, glosnosc, kamera, otworz_xbox=True, gra=None):
        if not self.sterowanie.acquire(blocking=False):  # one at a time: never two voices, never a half swap
            return {"ok": False, "blad": ZAJETY}
        try:
            if gra and gra != self.gra:
                zmiana = self._zmien_gre(gra)
                if not zmiana["ok"]:
                    return zmiana
            elif not self._stop():  # a reader that did not end must not get a second one next to it
                return {"ok": False, "blad": "Poprzedni lektor nie zatrzymał się (wyciszyłem go) — spróbuj za chwilę."}
            return self._start(zrodlo, profil, urzadzenie, glosnosc, kamera, otworz_xbox)
        finally:
            self.sterowanie.release()

    def _ustaw_glos(self, profil, urzadzenie, glosnosc, kamera=None):
        """Settings of a start (remembered for next time). Returns the volume key."""
        klucz_gl = "glosnosc_iphone" if profil == "iphone" else "glosnosc"
        if glosnosc is None:
            glosnosc = self.ust[klucz_gl]
        self.ust.update(profil=profil, urzadzenie=str(urzadzenie or "")[:200])
        if kamera is not None:
            try:
                self.ust["kamera"] = popraw_ustawienie("kamera", int(kamera))
            except (TypeError, ValueError):
                pass
        try:
            self.ust[klucz_gl] = max(0.0, min(2.0, float(glosnosc)))
        except (TypeError, ValueError):
            pass
        return klucz_gl

    def _glos(self, p, profil, urzadzenie):
        if profil == "iphone":
            return GlosTelefon(self, self.ust["glosnosc_iphone"])
        return L.Glos(p, self.ust["glosnosc"], 2, False, urzadzenie=self.indeks_urzadzenia(urzadzenie))

    def _start(self, zrodlo, profil, urzadzenie, glosnosc, kamera, otworz_xbox=True):
        if profil not in PROFILE:
            profil = "tv_mac"
        if zrodlo not in ("ekran", "kamera", "demo"):
            return {"ok": False, "blad": f"Nieznane źródło obrazu: {zrodlo}"}
        with self.lock:
            if self.p is None:
                return {"ok": False, "blad": self.paczka_blad or "Paczka jeszcze się wczytuje."}
            self._ustaw_glos(profil, urzadzenie, glosnosc, kamera)
            if zrodlo != "demo":  # the demo must not change how the user normally plays
                self.ust["zrodlo"] = zrodlo
            self.zapisz_ustawienia()
            p, m, g = self.p, self.m, self.gra
        frac = p.roi()[0]
        if zrodlo == "ekran":
            if not Quartz.CGPreflightScreenCaptureAccess():
                Quartz.CGRequestScreenCaptureAccess()
                return {"ok": False, "zgoda": True,
                        "blad": "macOS potrzebuje zgody na nagrywanie ekranu dla „GTA Lektor”. Zezwól w okienku "
                                "systemu albo w Ustawieniach, potem uruchom panel ponownie."}
            src = L.ZrodloEkran(frac)
            if otworz_xbox:
                subprocess.Popen(["open", "-a", "Google Chrome", XBOX])
        elif zrodlo == "kamera":
            try:
                src = L.ZrodloWideo(int(kamera), frac)
            except SystemExit as e:
                return {"ok": False, "blad": str(e)}
        else:  # demo — only the chosen game's own recording
            w = self.katalog.wpis(g) or {}
            demo = w.get("demo_plik")
            if not demo or not os.path.exists(demo):
                return {"ok": False, "blad": "Ta gra nie ma nagrania demo — wrzuć krótki film z napisami tej gry "
                                             "(.mp4, nazwany jak paczka) do folderu paczek."}
            src = L.ZrodloWideo(demo, frac)
        try:
            glos = self._glos(p, profil, urzadzenie)
        except Exception as e:
            src.zamknij()  # its temporary folder too
            return {"ok": False, "blad": f"Nie mogę otworzyć dźwięku: {e}"}
        glos.na_info = self.info
        glos.start()
        okno = None
        if zrodlo == "demo":
            try:
                okno = subprocess.Popen(["ffplay", "-loglevel", "quiet", "-an", "-autoexit", "-x", "960", "-y", "540",
                                         "-window_title", f"{p.manifest.get('name', 'Gra')} - demo lektora", demo])
                time.sleep(0.4)
            except FileNotFoundError:
                self.info("(brak ffplay — demo bez obrazu, tylko lektor)")
        stop = threading.Event()
        sesja = {"stop": stop, "glos": glos, "zrodlo": zrodlo, "profil": profil, "start": time.time(), "okno": okno}

        def na_kwestie(tekst, idx, score):
            with self.lock:
                self.kwestie.appendleft({"czas": teraz(), "tekst": tekst})

        def praca():
            licz = collections.Counter()
            try:
                licz = L.czytaj(p, m, glos, src, stop=stop, na_kwestie=na_kwestie, na_info=self.info)
                if zrodlo == "demo" and not stop.is_set():
                    koniec = time.time() + 15
                    while glos.zajety() and time.time() < koniec:
                        time.sleep(0.1)
            except Exception as e:  # never leave the panel showing "reading" with a dead reader
                self.info(f"! Lektor przerwał pracę: {e}")
            finally:
                glos.zamknij()
                src.zamknij()
                with self.lock:
                    self.licz = licz
                    if self.sesja is sesja:
                        self.sesja = None
            self.info(f"Lektor zatrzymany. Przeczytane kwestie: {licz['glos']}.")

        sesja["watek"] = threading.Thread(target=praca, daemon=True)
        with self.lock:
            self.sesja, self.alarm = sesja, None
            self.kwestie.clear()
        sesja["watek"].start()
        self.info({"ekran": "Lektor czyta napisy z ekranu (gra zdalna).", "kamera": "Lektor czyta obraz z karty.",
                   "demo": "Demo: scenka z gry."}[zrodlo] + (" Głos: iPhone." if profil == "iphone" else ""))
        return {"ok": True}

    def probka(self, gra=None, profil=None, urzadzenie=None, glosnosc=None, ile=3):
        """„Posłuchaj próbki”: a few random lines of the game that have a recording, through the current output (Mac
        or iPhone). Runs as a session, so Graj, Stop and a game change stop it like the reader."""
        if not self.sterowanie.acquire(blocking=False):
            return {"ok": False, "blad": ZAJETY}
        try:
            profil = profil if profil in PROFILE else self.ust["profil"]
            if profil == "iphone" and not self.telefon_slucha():  # checked BEFORE anything is stopped or swapped
                return {"ok": False, "blad": "Próbka ma zagrać z iPhone'a, ale żaden telefon nie ma włączonego "
                                             "dźwięku — otwórz pilota w iPhonie i stuknij „Włącz dźwięk w tym iPhonie”."}
            if gra and gra != self.gra:
                zmiana = self._zmien_gre(gra)
                if not zmiana["ok"]:
                    return zmiana
            elif not self._stop():
                return {"ok": False, "blad": "Lektor nie zatrzymał się — spróbuj jeszcze raz."}
            with self.lock:
                p = self.p
                if p is None:
                    return {"ok": False, "blad": self.paczka_blad or "Paczka jeszcze się wczytuje."}
                urzadzenie = self.ust["urzadzenie"] if urzadzenie is None else urzadzenie
                self._ustaw_glos(profil, urzadzenie, glosnosc)
                self.zapisz_ustawienia()
            dobre = [i for i in range(len(p.ids)) if p.audio[i] and 12 <= len(p.texts[i]) <= 140]
            if not dobre:
                return {"ok": False, "blad": "Ta paczka nie ma nagrań do próbki."}
            wybrane = random.sample(dobre, min(ile, len(dobre)))
            try:
                glos = self._glos(p, profil, urzadzenie)
            except Exception as e:
                return {"ok": False, "blad": f"Nie mogę otworzyć dźwięku: {e}"}
            glos.na_info = self.info
            glos.start()
            stop = threading.Event()
            sesja = {"stop": stop, "glos": glos, "zrodlo": "probka", "profil": profil, "start": time.time(),
                     "okno": None, "wybrane": wybrane}

            def praca():
                try:
                    for idx in wybrane:
                        if stop.is_set():
                            break
                        with self.lock:
                            self.kwestie.appendleft({"czas": teraz(), "tekst": p.texts[idx]})
                        glos.dodaj(idx)
                        koniec = time.time() + 20
                        stop.wait(0.3)
                        while glos.zajety() and time.time() < koniec and not stop.is_set():
                            stop.wait(0.1)
                finally:
                    glos.zamknij()
                    with self.lock:
                        if self.sesja is sesja:
                            self.sesja = None

            sesja["watek"] = threading.Thread(target=praca, daemon=True)
            with self.lock:
                self.sesja, self.alarm = sesja, None
                self.kwestie.clear()
            sesja["watek"].start()
            self.info(f"Próbka: {len(wybrane)} kwestie z {p.manifest.get('name', 'gry')}"
                      + (" (iPhone)." if profil == "iphone" else "."))
            return {"ok": True, "kwestie": len(wybrane)}
        finally:
            self.sterowanie.release()

    def _stop(self):
        """Caller holds self.sterowanie. True = no session left (the reader thread really ended)."""
        with self.lock:
            s = self.sesja
        if s is None:
            return True
        s["stop"].set()
        if s.get("okno") and s["okno"].poll() is None:
            s["okno"].terminate()  # close the demo video window too
            try:
                s["okno"].wait(timeout=2)
            except subprocess.TimeoutExpired:
                s["okno"].kill()
        s["watek"].join(timeout=5)
        if s["watek"].is_alive():  # reader stuck: silence it anyway so two voices can never overlap
            s["glos"].zamknij()
            return False
        with self.lock:
            if self.sesja is s:
                self.sesja = None
        return True

    def stop(self, czekaj=False):
        """Stop button: busy lock = a refusal (czekaj=True only for the shutdown watchdog)."""
        if not self.sterowanie.acquire(timeout=15 if czekaj else -1, blocking=czekaj):
            return {"ok": False, "blad": ZAJETY}
        try:
            return {"ok": True} if self._stop() else {"ok": False, "blad": "Lektor nie zatrzymał się w 5 s — wyciszyłem go."}
        finally:
            self.sterowanie.release()

    def glosnosc(self, v, iphone=False):
        klucz = "glosnosc_iphone" if iphone else "glosnosc"
        try:
            v = max(0.0, min(2.0, float(v)))
        except (TypeError, ValueError):
            return
        with self.lock:
            self.ust[klucz] = v
            if self.sesja and (self.sesja["profil"] == "iphone") == iphone:
                self.sesja["glos"].glosnosc = v
            self.zapisz_ustawienia()

    def adresy_pilota(self):
        ip = adres_ip()
        q = f"/telefon?k={self.klucz}"
        return {"local": f"http://{self.bonjour}:{PORT}{q}" if self.bonjour else None,
                "ip": f"http://{ip}:{PORT}{q}" if ip else None}

    def stan(self, lokalny):
        with self.lock:
            s = self.sesja
            ostrz = next((t for _, t in reversed(self.komunikaty) if t.startswith("!")), None)
            slucha = self.telefon_slucha()
            if s and s["profil"] == "iphone" and not slucha:
                ostrz = ("! Lektor ma grać z iPhone'a, ale żaden telefon nie ma włączonego dźwięku — otwórz pilota "
                         "w iPhonie i stuknij „Włącz dźwięk w tym iPhonie”.")
            op = list(self.opoznienia)
            w = self.katalog.wpis(self.gra) if self.gra else None
            wynik = {
                "wersja": wersja_strony(),
                "gotowy": self.p is not None, "paczka_blad": self.paczka_blad,
                "paczka": (self.p.manifest.get("name") if self.p else None),
                "gra": {"id": self.gra, "nazwa": w.get("nazwa") if w else None} if self.gra else None,
                "sesja_gry": self.sesja_gry, "katalog_nr": self.katalog.nr, "alarm": self.alarm,
                "dziala": s is not None, "zrodlo": s["zrodlo"] if s else None, "profil": s["profil"] if s else None,
                "od": int(time.time() - s["start"]) if s else 0,
                "kwestie": list(self.kwestie), "ostrzezenie": ostrz if s else None,
                "komunikaty": [f"{c} {t}" for c, t in list(self.komunikaty)[-8:]],
                "ustawienia": self.ust, "zgoda_ekranu": bool(Quartz.CGPreflightScreenCaptureAccess()),
                # a demo path set for a disk that is not plugged in = no demo
                "demo": bool(w and w.get("demo_plik") and os.path.exists(w["demo_plik"])),
                "telefon": {"slucha": slucha, "ostatnie_ms": op[-1] if op else None,
                            "mediana_ms": int(statistics.median(op)) if op else None},
            }
            if lokalny:
                t = time.time()
                wynik["pilot"] = {"telefony": [{"ip": k["ip"], "temu": int(t - k["t"])}
                                               for k in self.klienci.values() if k["tel"]],
                                  "test": self.test_tel}
        if lokalny:
            wynik["pilot"].update(self.adresy_pilota())
        return wynik


STAN = None  # created in main() (tests build their own Stan on temporary folders)


class Obsluga(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _wyslij(self, kod, tresc, typ="application/json; charset=utf-8", naglowki=()):
        dane = tresc if isinstance(tresc, bytes) else json.dumps(tresc, ensure_ascii=False).encode()
        self.send_response(kod)
        self.send_header("Content-Type", typ)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(dane)))
        for k, v in naglowki:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(dane)

    def _plik(self, plik, typ):
        """Static file with Range support (Safari on iOS will not play a video without it)."""
        with open(plik, "rb") as f:
            dane = f.read()
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if not m or not (m.group(1) or m.group(2)):
            return self._wyslij(200, dane, typ, [("Accept-Ranges", "bytes")])
        if m.group(1):
            a, b = int(m.group(1)), min(int(m.group(2) or len(dane) - 1), len(dane) - 1)
        else:  # suffix range: last N bytes
            a, b = max(0, len(dane) - int(m.group(2))), len(dane) - 1
        if a > b or a >= len(dane):
            return self._wyslij(416, b"", typ, [("Content-Range", f"bytes */{len(dane)}")])
        self._wyslij(206, dane[a:b + 1], typ, [("Accept-Ranges", "bytes"), ("Content-Range", f"bytes {a}-{b}/{len(dane)}")])

    def _wejscie(self):
        """Who is asking and may they? Returns (allowed, from_network, path, query).
        From this Mac: the Host must be the loopback name (stops DNS-rebinding pages). From the network:
        the secret key in ?k= or the X-Lektor-Klucz header, except for harmless static pictures."""
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if self.client_address[0] in LOKALNE and host in ("127.0.0.1", "localhost", "::1"):
            return True, False, u.path, q
        # the network — or this Mac under its own Wi-Fi name (MacBook….local resolves to itself): key required
        if PUBLICZNE.fullmatch(u.path):
            return True, True, u.path, q
        podany = (q.get("k") or [""])[0] or self.headers.get("X-Lektor-Klucz") or ""
        return hmac.compare_digest(podany.encode(), STAN.klucz.encode()), True, u.path, q

    def _slad(self, ok, siec, sciezka):
        """One log line per network request for the page, manifest, icons and every refusal (not the 1 s polls):
        after the first scan the log shows what the phone fetched, under which name, and what was refused."""
        if siec and (not ok or sciezka in ("/", "/index.html", "/telefon", "/manifest.json") or sciezka.startswith("/ikona-")):
            STAN.info(f"📱 sieć {self.ip()} {self.command} {sciezka} → {'wpuszczony' if ok else 'ODMOWA 403'} "
                      f"(adres: {self.headers.get('Host', '?')})")

    def ip(self):
        a = self.client_address[0]
        return a[7:] if a.startswith("::ffff:") else a

    def _klient(self, q, siec):
        """Only a page that names itself (?c=) counts as a client — a bare curl check must not keep the server up."""
        cid = (q.get("c") or [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", cid):
            return "ip-" + self.ip()
        STAN.zglos(cid, siec, self.ip())
        return cid

    @staticmethod
    def _g(q):
        try:
            return int((q.get("g") or [""])[0])
        except ValueError:
            return None

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        ok, siec, sciezka, q = self._wejscie()
        self._slad(ok, siec, sciezka)
        if not ok:
            return self._wyslij(403, {"blad": "zabronione — zeskanuj kod QR z panelu na Macu"})
        if sciezka == "/api/stan":  # only a panel's own poll proves it is alive (not images)
            self._klient(q, siec)
            return self._wyslij(200, STAN.stan(lokalny=not siec))
        if sciezka in ("/", "/index.html", "/telefon"):
            with open(os.path.join(PANEL, "index.html"), "rb") as f:
                html = f.read()
            html = html.replace(b"__WERSJA__", wersja_strony().encode())
            if siec:  # iOS fetches the manifest itself (no header, maybe no cookie): the key goes into its URL
                html = html.replace(b'href="/manifest.json"', f'href="/manifest.json?k={STAN.klucz}"'.encode())
            return self._wyslij(200, html, "text/html; charset=utf-8")
        if sciezka == "/manifest.json":
            return self._wyslij(200, {
                "name": "GTA Lektor", "short_name": "Lektor", "lang": "pl", "display": "standalone",
                "start_url": f"/telefon?k={STAN.klucz}", "scope": "/", "id": "/telefon",
                "background_color": "#140a2c", "theme_color": "#140a2c",
                "icons": [{"src": f"/ikona-{r}.png", "sizes": f"{r}x{r}", "type": "image/png"} for r in (192, 512)],
            }, "application/manifest+json; charset=utf-8")
        if PUBLICZNE.fullmatch(sciezka):
            plik = os.path.join(PANEL, ALIASY.get(sciezka) or os.path.join(*sciezka.lstrip("/").split("/")))
            if os.path.exists(plik):
                return self._plik(plik, TYPY[plik.rsplit(".", 1)[1]])
        if sciezka == "/api/katalog":
            kat = STAN.katalog.publiczne()
            with STAN.lock:
                kat.update(wybrana=STAN.gra, ustawiona=STAN.ust.get("gra"))
            return self._wyslij(200, kat)
        m = re.fullmatch(r"/api/okladka/([A-Za-z0-9_.-]{1,80})", sciezka)
        if m:  # the id is only a dictionary key into the catalogue — never part of a path
            dane = STAN.katalog.okladka(m.group(1))
            if dane is None:
                return self._wyslij(404, {"blad": "nie ma takiej okładki"})
            return self._wyslij(200, dane, "image/jpeg")
        if sciezka == "/api/urzadzenia":
            return self._wyslij(200, {"urzadzenia": STAN.urzadzenia(odswiez="odswiez" in q)})
        if sciezka == "/api/qr.png" and not siec:
            adr = STAN.adresy_pilota().get((q.get("co") or ["local"])[0])
            if not adr:
                return self._wyslij(404, {"blad": "brak adresu w sieci"})
            if adr not in STAN.qr_cache:
                STAN.qr_cache[adr] = kod_qr(adr)
            return self._wyslij(200, STAN.qr_cache[adr], "image/png")
        if sciezka == "/api/telefon/kwestie":  # long-poll: returns at once when a line is matched or the game changes
            cid = self._klient(q, siec)
            try:
                od = int((q.get("od") or ["-1"])[0])
            except ValueError:
                od = -1
            with STAN.lock:
                STAN.sluchacze[cid] = time.time() + 24  # the poll waits at most 20 s
            try:
                nr, g, zd = STAN.czekaj_na_zdarzenia(od, 20)
            finally:
                with STAN.lock:
                    STAN.sluchacze[cid] = time.time() + 4  # the phone reconnects at once
            if (q.get("f") or [""])[0] == "opus":  # the clip rides along: one Wi-Fi round trip instead of two
                for e in zd:
                    if e["typ"] == "kwestia":
                        dane, _ = STAN.dzwiek(e["idx"], "opus", e["g"])
                        if dane:
                            e["audio"] = base64.b64encode(dane).decode()
            with STAN.lock:
                gl = STAN.ust["glosnosc_iphone"]
            return self._wyslij(200, {"nr": nr, "g": g, "zdarzenia": zd, "glosnosc": gl})
        if sciezka == "/api/telefon/probka":  # a real pack clip for the phone's codec self-test
            with STAN.lock:
                p, g = STAN.p, STAN.sesja_gry
            if p is None:
                return self._wyslij(503, {"blad": "paczka się wczytuje"})
            return self._wyslij(200, {"idx": next(i for i, a in enumerate(p.audio) if a), "g": g})
        m = re.fullmatch(r"/api/telefon/dzwiek/(\d{1,7})", sciezka)
        if m:
            dane, typ = STAN.dzwiek(int(m.group(1)), (q.get("f") or ["opus"])[0], self._g(q))
            if dane is None:
                return self._wyslij(404, {"blad": "nie ma takiego nagrania (albo gra się zmieniła)"})
            return self._wyslij(200, dane, typ)
        self._wyslij(404, {"blad": "nie ma"})

    def do_POST(self):
        ok, siec, sciezka, q = self._wejscie()
        self._slad(ok, siec, sciezka)
        if not ok:
            return self._wyslij(403, {"blad": "zabronione"})
        if sciezka == "/api/koniec":  # sendBeacon on Mac window close cannot send headers; worst case: panel closes
            if not siec:  # a phone switching apps must never shut the server down
                STAN.koniec((q.get("c") or [""])[0])
            return self._wyslij(200, {"ok": True})
        if self.headers.get("X-Lektor") != "1":  # blocks cross-site requests from other web pages
            return self._wyslij(403, {"blad": "zabronione"})
        try:
            dane = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536)) or b"{}")
        except ValueError:
            dane = {}
        if not isinstance(dane, dict):
            dane = {}
        if sciezka == "/api/start":
            u = STAN.ust
            wynik = STAN.start(dane.get("zrodlo", u["zrodlo"]), dane.get("profil", u["profil"]),
                               dane.get("urzadzenie", u["urzadzenie"]), dane.get("glosnosc"),
                               dane.get("kamera", u["kamera"]), dane.get("otworz_xbox", True), dane.get("gra"))
            return self._wyslij(200, wynik)
        if sciezka == "/api/probka":
            return self._wyslij(200, STAN.probka(dane.get("gra"), dane.get("profil"), dane.get("urzadzenie"),
                                                 dane.get("glosnosc")))
        if sciezka == "/api/stop":
            return self._wyslij(200, STAN.stop())
        if sciezka == "/api/glosnosc":
            STAN.glosnosc(dane.get("v", 1.0), bool(dane.get("iphone")))
            return self._wyslij(200, {"ok": True})
        if sciezka == "/api/ustawienia":  # the game is chosen only by Graj / Próbka (under the one lock), not here
            with STAN.lock:
                for k in ("profil", "urzadzenie", "zrodlo", "kamera", "wyglad"):
                    if k in dane:
                        try:
                            STAN.ust[k] = popraw_ustawienie(k, dane[k])
                        except ValueError:
                            pass
                STAN.zapisz_ustawienia()
            return self._wyslij(200, {"ok": True})
        if sciezka == "/api/xbox":
            subprocess.Popen(["open", "-a", "Google Chrome", XBOX])
            return self._wyslij(200, {"ok": True})
        if sciezka == "/api/zgoda-ekranu":
            Quartz.CGRequestScreenCaptureAccess()
            subprocess.Popen(["open", USTAWIENIA_EKRANU])
            return self._wyslij(200, {"ok": True})
        if sciezka == "/api/paczka":
            return self._wyslij(200, STAN.odswiez())
        if sciezka == "/api/folder-paczek" and not siec:
            os.makedirs(STAN.katalog.folder, exist_ok=True)
            subprocess.Popen(["open", STAN.katalog.folder])
            return self._wyslij(200, {"ok": True})
        if sciezka == "/api/nowy-klucz" and not siec:
            STAN.nowy_klucz()
            STAN.info("Nowy kod pilota — stare ikonki na telefonach przestały działać.")
            return self._wyslij(200, {"ok": True})
        if sciezka == "/api/telefon/gram":
            STAN.zagrane(dane, self.ip())
            return self._wyslij(200, {"ok": True})
        if sciezka == "/api/telefon/diag":  # the phone's own measurements, logged so nobody has to retype them
            tekst = json.dumps(dane, ensure_ascii=False)[:900]
            if dane.get("typ") == "test":
                with STAN.lock:
                    STAN.test_tel = dane
            STAN.info(f"📱 telefon {self.ip()}: {tekst}")
            return self._wyslij(200, {"ok": True})
        self._wyslij(404, {"blad": "nie ma"})


def straznik(serwer):
    """Shuts the server down when no client is left: the last Mac panel window closed (beacon) with no phone
    around, or every client went silent past its limit (see LIMIT). The start gets the Mac idle limit as grace.
    Every 5 s it also looks at the packs folder (catalogue) and at the pack of a running session."""
    n = 0
    while True:
        time.sleep(1)
        n += 1
        if n % 5 == 0:
            STAN.pilnuj()
        with STAN.lock:
            zamknij_o = STAN.zamknij_o
        nikt = not STAN.obecni() and time.time() - STAN.start_t > LIMIT[("mac", False)]
        if (zamknij_o and time.time() > zamknij_o) or nikt:
            STAN.info("Panel zamknięty — kończę lektora.")
            STAN.stop(czekaj=True)
            serwer.shutdown()
            return


class Serwer(ThreadingHTTPServer):
    """IPv6 + IPv4 on one socket: the phone may reach MacBook….local over IPv6 first (Bonjour gives both)."""
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def main():
    global STAN
    try:
        serwer = Serwer(("::", PORT), Obsluga)
    except OSError:
        sys.exit(f"Port {PORT} zajęty — panel już działa? Otwórz http://127.0.0.1:{PORT}/")
    STAN = Stan()
    STAN.uruchom()
    threading.Thread(target=straznik, args=(serwer,), daemon=True).start()
    STAN.info(f"Panel GTA Lektor: http://127.0.0.1:{PORT}/ · pilot w sieci: {STAN.bonjour or adres_ip()}:{PORT}")
    serwer.serve_forever()


if __name__ == "__main__":
    main()
