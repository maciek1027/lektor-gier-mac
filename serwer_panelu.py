#!/usr/bin/env python3
"""Lektor gier (dawniej GTA Lektor) — panel server (port 8797): the Mac panel on 127.0.0.1 and an iPhone remote over home Wi-Fi.

Serves panel/index.html and a small JSON API that drives lektor.py: the game catalogue (voice packs in the packs
folder), start/stop the reader, a short sample of a game, live volume, output device, the remote play of the chosen console
(Xbox in Chrome or Sony's PS Remote Play app), demo, and the GameReader library (their public list of voice packs; "Pobierz" downloads, checks and drops a pack
into the packs folder). Requests from this Mac (127.0.0.1) work as before; every request from the network needs the secret key `k`
(kept in ustawienia.json, shown on the Mac panel as a QR code).
Profile "iphone": matched lines are not played on the Mac but sent to the phone (long-poll), which plays them.
Game change: one lock covers change, start and stop; the reader is confirmed stopped before the pack is swapped,
and every phone event carries the game-session number `g` (line numbers are per pack — a line of the old game
must never be played from the new one).
Started by "Lektor Gier na konsoli.app"; log in ~/Library/Logs/gta-lektor.log.
Test instance: LEKTOR_PORT=8798 LEKTOR_USTAWIENIA=/tmp/x.json .venv/bin/python serwer_panelu.py
"""
import base64
import collections
import colorsys
import errno
import hashlib
import hmac
import io
import json
import os
import random
import re
import secrets
import shutil
import socket
import ssl
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

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
# PlayStation 5 has no remote play in a browser: Sony's own Mac app does it. Looked for where macOS installs apps
# (a list, so the tests can point it at a temporary folder and never open anything on the user's Mac).
PS_REMOTE_PLAY = ["/Applications/PS Remote Play.app", os.path.expanduser("~/Applications/PS Remote Play.app")]
PS_BRAK = ("Nie widzę aplikacji PS Remote Play na tym Macu. Bez niej Mac nie widzi obrazu gry z PlayStation 5 "
           "(PS5 nie ma gry zdalnej w przeglądarce, jak Xbox). Pobierz ją ze strony Sony (szukaj „PS Remote Play Mac”), "
           "zainstaluj ją, połącz z konsolą i kliknij Graj jeszcze raz — albo w Ustawieniach → "
           "Więcej opcji wybierz konsolę Xbox albo kartę przechwytującą.")
USTAWIENIA_EKRANU = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
# glosnosc = the lektor on the Mac; glosnosc_iphone = extra boost on the phone, on top of its side buttons (which a web
# page can neither read nor set) — kept apart so a 200 % phone boost never blasts the Mac speakers.
# gra = catalogue id of the chosen game (pack file name); wyglad = panel background (game theme or a fixed one)
# konsola = which console the remote play (zrodlo "ekran") comes from; an old settings file means Xbox
DOMYSLNE = {"profil": "tv_mac", "urzadzenie": "", "glosnosc": 1.0, "glosnosc_iphone": 1.0, "zrodlo": "ekran", "kamera": 0,
            "gra": None, "wyglad": "motyw", "konsola": "xbox"}
PROFILE = ("tv_mac", "tv_glosnik", "sluchawki", "iphone")
WYGLADY = ("motyw", "klasyczny", "grafit", "granat")
KONSOLE = ("xbox", "playstation")
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
KOPIA_S = 60  # an unreadable pack changed less than this ago is still being copied, not broken
ZAJETY = "Lektor właśnie się przełącza (zmiana gry, start albo stop) — spróbuj za chwilę."
# GameReader library: the public list of voice packs (the same one their program reads). Read in the background, kept in
# the cache folder; nothing about the user is sent (a generic program name only). LEKTOR_SPIS overrides the address.
SPIS_URL = os.environ.get("LEKTOR_SPIS") or "https://voicepack-proxy.gamereader-pl.workers.dev/"
HOSTY_BIBLIOTEKI = ("voicepack.gamereader.pl",)  # download and cover addresses from the list must point here (https)
# "Pobierz" inside the program; LEKTOR_POBIERANIE=0 (lokalne.env) = the library shows the list with a link to gamereader.pl
POBIERANIE = os.environ.get("LEKTOR_POBIERANIE", "1") != "0"
UA = "LektorGierNaKonsoli/1.0"
ZAPAS_B = 200 * 1024 * 1024  # free space kept on the packs disk on top of the pack size
CISZA_S = 30  # a download with no data for this long stops with a message
STARA_CZESC_S = 600  # a .czesc file unchanged this long is a leftover (a younger one may be another server's download)


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
    if k == "konsola" and v in KONSOLE:
        return v
    raise ValueError(k)


def ps_remote_play():
    """Path of the PS Remote Play app on this Mac, or None."""
    return next((a for a in PS_REMOTE_PLAY if os.path.isdir(a)), None)


def otworz_gre_zdalna(konsola):
    """Opens the remote play of the chosen console: Xbox = its page in Chrome, PlayStation = Sony's Mac app.
    Returns None when opened, or the error text for the user (what, why, how to fix)."""
    if konsola == "playstation":
        aplikacja = ps_remote_play()
        if not aplikacja:
            return PS_BRAK
        subprocess.Popen(["open", "-a", aplikacja])
        return None
    subprocess.Popen(["open", "-a", "Google Chrome", XBOX])
    return None


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
    "kopiuje": "Kopiuję…",  # a fresh file that is not a whole zip yet (Finder copy / download under the final name)
    "nie_na_dysku": "W bibliotece — nie na dysku",  # E1c (download) fills this one; the panel already draws it
}


def id_z_pliku(nazwa):
    return re.sub(r"[^A-Za-z0-9_.-]", "-", os.path.splitext(nazwa)[0])[:80] or "paczka"


def dysk_odlaczony(folder):
    """Name of the external disk the folder lives on when that disk is not mounted (else None). An unplugged disk
    must never turn into writes on the Mac's own disk (/Volumes/<name> can be a plain leftover folder)."""
    czesci = os.path.abspath(folder).split(os.sep)
    if len(czesci) > 2 and czesci[1] == "Volumes" and not os.path.ismount(os.path.join("/Volumes", czesci[2])):
        return czesci[2]
    return None


class Katalog:
    """Voice packs in the packs folder: per pack only manifest.json, preset/settings and the cover are read (never
    subtitles.json — GTA VI has 373 thousand lines), in a background thread, cached outside the program folder and
    invalidated by file size and date. Folder states are told apart: no disk, no folder, empty folder, read error."""

    def __init__(self, folder, pamiec, sprawdzone=SPRAWDZONE):
        self.folder, self.pamiec, self.plik_dowodow = folder, pamiec, sprawdzone
        self.lock = threading.Lock()
        self.skanuje = threading.Lock()
        self.stan, self.komunikat = "skanuje", "Sprawdzam folder gier…"
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
        dysk = dysk_odlaczony(self.folder)
        if dysk:
            return "brak_dysku", (f"Nie widzę dysku „{dysk}” z paczkami lektora. Podłącz dysk — katalog "
                                  f"pokaże gry sam (albo kliknij „Sprawdź ponownie”).")
        if not os.path.exists(self.folder):
            return "brak_folderu", (f"Nie ma folderu gier ({self.folder}). Utwórz go i wrzuć do niego paczkę .zip "
                                    f"z gamereader.pl albo wskaż inny folder (LEKTOR_PACZKI w lokalne.env).")
        return "blad", (f"Nie mogę odczytać folderu gier ({blad}). Sprawdź, czy dysk działa i czy program ma "
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
                return self._ustaw("pusty", ("W folderze gier nie ma jeszcze żadnej gry. Pobierz paczkę lektora "
                                             "z gamereader.pl i wrzuć plik .zip do folderu gier."), gry={})
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
                self._stan_kafelka(wpis, dowody, staty[n].st_mtime)
                wpis["demo_plik"] = self._demo(n, wpis, mp4)
                gry[ids[n]] = wpis
                self._ustaw("ok", None, gry=dict(gry))
            # a file still being copied: look again on the next pass even if it stops growing (then it is broken)
            self.odcisk = None if any(w.get("stan") == "kopiuje" for w in gry.values()) else odcisk
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
    def _stan_kafelka(w, dowody, mtime=0):
        """Tile state. Decides: a test record of this pack version (live on the Xbox / on a game recording), then the
        pack fields the reader does not apply that can lose lines (czytanie). Not the bare list of unhandled fields:
        GTA VI has two and reads the demo 14/14."""
        if w.get("blad"):
            if 0 <= time.time() - mtime < KOPIA_S:  # dragging a 3 GB pack in Finder writes it under its final name
                # (a date in the future is not "being copied": it would wait forever and rescan every pass)
                w.update(stan="kopiuje", stan_opis="Plik paczki jeszcze się kopiuje — gra będzie gotowa, gdy kopiowanie "
                                                   "się skończy (sprawdzam co 5 s).")
            else:
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

    def id_paczek(self):
        """Pack ids (manifest `id`) of the packs on disk — the library hides these (matched by id, not by file name:
        the GTA VI file on disk is named v4 and holds 1.0.2 while the list offers 1.0.4)."""
        with self.lock:
            return {w["id_paczki"] for w in self.gry.values() if w.get("id_paczki")}

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


# ---------------------------------------------------------------- GameReader library (list + download)
def typ_obrazka(dane):
    if dane[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if dane[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if dane[:4] == b"RIFF" and dane[8:12] == b"WEBP":
        return "image/webp"
    return None


def mb(b):
    return f"{b / 1e9:.1f} GB".replace(".", ",") if b >= 1e9 else f"{max(1, round(b / 1e6))} MB"


def sprawdz_paczke(plik, rozmiar, id_spisu):
    """A downloaded pack before it gets its .zip name: size as in the list, every member readable (testzip), the
    GameReader 2.0 build (manifest.json, subtitles.json with lines, at least one recording) and the same pack id as the
    list. NOT the list's `checksum` field — it is not the file's SHA-256 (measured 2026-11-22 on two packs). Some lines
    without a recording are normal (the GTA VI pack that reads the demo 14/14 has 222 of 373 thousand) — only counted.
    Returns lines without a recording; raises ValueError with the reason in Polish."""
    if os.path.getsize(plik) != rozmiar:
        raise ValueError(f"plik ma {os.path.getsize(plik)} B, a lista biblioteki podaje {rozmiar} B")
    try:
        with zipfile.ZipFile(plik) as z:
            zly = z.testzip()
            if zly:
                raise ValueError(f"uszkodzony plik w środku paczki: {zly}")
            nazwy = set(z.namelist())
            if "manifest.json" not in nazwy or "subtitles.json" not in nazwy:
                raise ValueError("brak manifest.json albo subtitles.json — to nie paczka GameReadera 2.0")
            man = json.loads(z.read("manifest.json"))
            napisy = json.loads(z.read("subtitles.json"))
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"nie da się odczytać paczki ({e})")
    if not isinstance(man, dict) or not isinstance(napisy, dict) or not isinstance(napisy.get("entries"), list):
        raise ValueError("pliki opisu paczki mają zły kształt")
    if man.get("id") is None or str(man["id"]) != id_spisu:  # a missing id too: library and "Moje gry" match by it
        raise ValueError("w środku jest inna paczka niż na liście biblioteki")
    ids = [str(e.get("id")) for e in napisy["entries"] if isinstance(e, dict) and e.get("id") is not None]
    if not ids:
        raise ValueError("paczka nie ma żadnej kwestii")
    audio = {n[6:].rsplit(".", 1)[0] for n in nazwy if n.startswith("audio/") and "." in n[6:]}  # as lektor.Paczka
    bez = sum(1 for i in ids if i not in audio)
    if bez == len(ids):
        raise ValueError("paczka nie ma żadnego nagrania")
    return bez


def przenies_bez_nadpisania(z, do):
    """os.rename that never replaces an existing file: macOS renamex_np(RENAME_EXCL). A filesystem without it (measured
    2026-11-25: exFAT on the WD disk answers ENOTSUP, APFS works) falls back to a check right before the rename."""
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        if libc.renamex_np(os.fsencode(z), os.fsencode(do), 0x4) == 0:
            return
        blad = ctypes.get_errno()
    except (OSError, AttributeError):
        blad = errno.ENOTSUP
    if blad == errno.EEXIST:
        raise FileExistsError(errno.EEXIST, "plik już istnieje", do)
    if blad not in (errno.ENOTSUP, errno.EINVAL, errno.ENOSYS):
        raise OSError(blad, os.strerror(blad), do)
    if os.path.exists(do):
        raise FileExistsError(errno.EEXIST, "plik już istnieje", do)
    os.rename(z, do)


class _Przekierowania(urllib.request.HTTPRedirectHandler):
    """A redirect from the GameReader server is followed only to an address the same check allows (https, their host)."""

    def __init__(self, dobry):
        super().__init__()
        self.dobry = dobry

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.dobry(newurl):
            raise urllib.error.HTTPError(newurl, code, "przekierowanie poza serwer GameReadera", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Przerwane(Exception):
    pass


class RozmiarInny(ValueError):
    pass


class Biblioteka:
    """The GameReader library: their public list of voice packs, read in a background thread and saved in the cache
    (offline = the last saved list, with its date), covers fetched in the background and served by pack id only, and
    "Pobierz": one download at a time into the packs folder as `<name>.czesc` (the catalogue scan never sees it), checked
    (sprawdz_paczke) and only then renamed to `.zip` — the tile then moves to "Moje gry" by itself. Every failure is a
    message on the tile; the server never falls over. Runs outside the reader's lock: a download never blocks "Graj"."""

    ODSWIEZ_S = 6 * 3600  # a fresh list this often
    PONOW_S = 600  # offline / no list: try again this often
    MAX_SPIS_B = 5 * 1024 * 1024
    MAX_OKLADKA_B = 2 * 1024 * 1024

    def __init__(self, pamiec, katalog, url=SPIS_URL, hosty=HOSTY_BIBLIOTEKI, https=True, wlaczone=POBIERANIE,
                 na_info=print):
        self.pamiec, self.katalog, self.url, self.hosty, self.https = pamiec, katalog, url, tuple(hosty), https
        self.wlaczone, self.na_info = wlaczone, na_info
        self.lock = threading.Lock()
        self.czyta = threading.Lock()  # one list download at a time
        self.stan, self.komunikat, self.data = "laduje", "Pobieram listę gier GameReadera…", None
        self.wpisy = {}  # pack id -> checked entry
        self.nr = 0
        self.proba_t = 0.0  # last list fetch attempt
        self.okladki_biegna = False
        self.okladki_t = 0.0  # last cover pass
        self.pob = None  # the current download (dict) or None
        self.pob_lock = threading.Lock()  # held for the whole download
        self.bledy = {}  # pack id -> message of its last failed download (shown on the tile, "Spróbuj ponownie")
        self.bledy_serwera = set()  # those failed on the GameReader side (network, server, pack): the tile offers the site

    # ---------------------------------------------------------- the list
    def _dobry_adres(self, adres):
        try:
            u = urlsplit(str(adres))
        except ValueError:
            return False
        return u.scheme in (("https",) if self.https else ("https", "http")) and u.hostname in self.hosty

    def _parsuj(self, dane):
        """Checked entries from the raw list (foreign data: wrong-shaped or off-host entries are dropped)."""
        if not isinstance(dane, dict) or not isinstance(dane.get("voice_packs"), list):
            raise ValueError("lista ma inny kształt niż się spodziewam")
        wpisy = {}
        for v in dane["voice_packs"]:
            if not isinstance(v, dict):
                continue
            i, rozmiar = str(v.get("id") or ""), v.get("file_size_bytes")
            if not ID_GRY.fullmatch(i) or i in wpisy or not isinstance(rozmiar, int) or isinstance(rozmiar, bool) \
                    or rozmiar <= 0 or not self._dobry_adres(v.get("download_url")):
                continue
            wym = []
            for r in v.get("requirements") or []:
                if isinstance(r, dict) and str(r.get("description") or "").strip():
                    link = str(r.get("url") or "")
                    wym.append({"tekst": str(r["description"]).strip()[:800],
                                "url": link if re.fullmatch(r"https://[^\s\"'<>]{4,300}", link) else None})
            wpisy[i] = {"id": i, "nazwa": str(v.get("name") or i)[:200], "wersja": str(v.get("version") or "")[:40],
                        "autor": str(v.get("author") or "")[:120], "opis": str(v.get("description") or "")[:4000],
                        "rozmiar": rozmiar, "url": str(v["download_url"]),
                        "okladka_url": str(v["cover_url"]) if self._dobry_adres(v.get("cover_url")) else None,
                        "na_macu": bool(v.get("has_preset")), "wtyczka": bool(v.get("has_plugin")), "wymagania": wym}
        if not wpisy:
            raise ValueError("lista nie ma żadnej paczki")
        return wpisy

    def _plik_spisu(self):
        return os.path.join(self.pamiec, "spis.json")

    def wczytaj_zapisany(self):
        """At start: the list saved last time (shown at once; the fresh one replaces it when it arrives)."""
        try:
            with open(self._plik_spisu(), encoding="utf-8") as f:
                zapis = json.load(f)
            wpisy, data = self._parsuj(zapis["spis"]), float(zapis["pobrano"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        with self.lock:
            self.wpisy, self.data = wpisy, data
            self.stan, self.komunikat = "offline", f"Pokazuję listę zapisaną {self._kiedy(data)} — sprawdzam nowszą…"
            self.nr += 1
        return True

    @staticmethod
    def _kiedy(t):
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(t))

    def _otworz(self, adres, timeout, dobry=None):
        """GET with the generic program name; redirects only to addresses `dobry` allows (default: the list's hosts)."""
        opener = urllib.request.build_opener(_Przekierowania(dobry or self._dobry_adres))
        return opener.open(urllib.request.Request(adres, headers={"User-Agent": UA}), timeout=timeout)

    def _ten_sam_host(self, adres):
        a, b = urlsplit(str(adres)), urlsplit(str(self.url))
        return (a.scheme, a.hostname) == (b.scheme, b.hostname)

    def _pobierz_dane(self, adres, limit, dobry=None):
        with self._otworz(adres, 20, dobry) as r:
            dane = r.read(limit + 1)
        if len(dane) > limit:
            raise ValueError("odpowiedź większa niż się spodziewam")
        return dane

    def odswiez_spis(self):
        """Fetch the list now (background thread). A failure keeps the saved list and says so."""
        if not self.url or not self.czyta.acquire(blocking=False):
            return
        try:
            self.proba_t = time.time()
            try:
                surowe = json.loads(self._pobierz_dane(self.url, self.MAX_SPIS_B, self._ten_sam_host))
                wpisy = self._parsuj(surowe)
            except Exception as e:  # network, HTTP, JSON, shape: the saved list stays, the reason is on screen
                powod = self._powod(e)
                with self.lock:
                    if self.wpisy:
                        self.stan, self.komunikat = "offline", (
                            f"Nie mogę pobrać aktualnej listy gier ({powod}). Pokazuję listę zapisaną {self._kiedy(self.data)}.")
                    else:
                        self.stan, self.komunikat = "brak", (
                            f"Nie mogę pobrać listy gier GameReadera ({powod}), a nie mam zapisanej. Sprawdź internet — "
                            f"spróbuję znów za kilka minut. „Moje gry” działają bez niej.")
                    self.nr += 1
                self.na_info(f"! Biblioteka: nie pobrałem listy gier ({e})")
                return
            t = time.time()
            try:  # atomic: a half-written list must never be read back as the "saved" one
                os.makedirs(self.pamiec, exist_ok=True)
                tmp = self._plik_spisu() + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"pobrano": t, "spis": surowe}, f, ensure_ascii=False)
                os.replace(tmp, self._plik_spisu())
            except OSError as e:
                self.na_info(f"! Biblioteka: nie zapisałem listy w pamięci podręcznej ({e})")
            with self.lock:
                self.wpisy, self.data, self.stan, self.komunikat = wpisy, t, "ok", None
                self.nr += 1
        finally:
            self.czyta.release()
        self._okladki_w_tle()

    @staticmethod
    def _powod(e):
        """Why the list did not come, in words for the screen (the exact error goes to the log)."""
        if isinstance(e, urllib.error.HTTPError):
            return f"serwer GameReadera odpowiedział błędem {e.code}"
        if isinstance(e, (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError)):
            return "brak połączenia z internetem albo serwer GameReadera nie odpowiada"
        return "lista przyszła w nieoczekiwanej postaci"

    def pilnuj(self):
        """Watchdog (every 5 s): a fresh list every few hours; while offline, a retry every few minutes; covers that
        failed (network) are tried again every few minutes too."""
        co = self.ODSWIEZ_S if self.stan == "ok" else self.PONOW_S
        if self.url and time.time() - self.proba_t > co and not self.czyta.locked():
            threading.Thread(target=self.odswiez_spis, daemon=True).start()
        elif self.wpisy and time.time() - self.okladki_t > self.PONOW_S:
            self._okladki_w_tle()

    def uruchom(self):
        self.wczytaj_zapisany()
        if self.url:
            threading.Thread(target=self.odswiez_spis, daemon=True).start()
        else:
            with self.lock:
                self.stan, self.komunikat = "wylaczona", None

    # ---------------------------------------------------------- covers
    def _plik_okladki(self, i):
        return os.path.join(self.pamiec, "okladki", i + ".jpg")  # i passed ID_GRY (no slash, no "..": dots only inside)

    def _okladki_w_tle(self):
        with self.lock:
            if self.okladki_biegna:
                return
            self.okladki_biegna = True
        threading.Thread(target=self._okladki, daemon=True).start()

    def _okladki(self):
        self.okladki_t = time.time()
        try:
            with self.lock:
                brak = [(i, w["okladka_url"]) for i, w in self.wpisy.items()
                        if w["okladka_url"] and not os.path.exists(self._plik_okladki(i))]
            nowe = 0
            for n, (i, adres) in enumerate(brak):
                try:
                    dane = self._pobierz_dane(adres, self.MAX_OKLADKA_B)
                    if typ_obrazka(dane) is None:  # two covers in the list are WebP named .jpg (2026-11-25)
                        raise ValueError("to nie obrazek JPEG, PNG ani WebP")
                    os.makedirs(os.path.dirname(self._plik_okladki(i)), exist_ok=True)
                    with open(self._plik_okladki(i) + ".tmp", "wb") as f:
                        f.write(dane)
                    os.replace(self._plik_okladki(i) + ".tmp", self._plik_okladki(i))
                    nowe += 1
                except Exception as e:
                    self.na_info(f"! Biblioteka: okładka {i} nie pobrana ({e})")
                if nowe and (n % 24 == 23 or n == len(brak) - 1):  # the panel refetches the list in a few batches
                    with self.lock:
                        self.nr += 1
        finally:
            with self.lock:
                self.okladki_biegna = False

    def okladka(self, i):
        """Cover bytes by pack id — only an id from the list (dictionary lookup), never a path from the request."""
        with self.lock:
            if i not in self.wpisy:
                return None
        try:
            with open(self._plik_okladki(i), "rb") as f:
                return f.read()
        except OSError:
            return None

    def wymagania(self, id_paczki):
        with self.lock:
            w = self.wpisy.get(id_paczki or "")
            return list(w["wymagania"]) if w else []

    # ---------------------------------------------------------- what the panel shows
    def postep(self):
        with self.lock:
            p = self.pob
            if not p:
                return None
            return {"id": p["id"], "nazwa": p["nazwa"], "faza": p["faza"], "pobrane": p["pobrane"],
                    "rozmiar": p["rozmiar"], "procent": int(100 * p["pobrane"] / p["rozmiar"]) if p["rozmiar"] else 0}

    def trwa(self):
        return self.pob_lock.locked()

    def publiczne(self):
        na_dysku = self.katalog.id_paczek()
        with self.lock:
            p = self.pob
            gry = []
            for i, w in self.wpisy.items():
                if i in na_dysku:
                    continue
                g = {k: v for k, v in w.items() if k not in ("url", "okladka_url")}
                g["okladka"] = os.path.exists(self._plik_okladki(i))
                if not w["na_macu"]:
                    g["stan"], g["stan_opis"] = "nie_na_macu", (
                        "Ta paczka nie ma obszaru napisów (presetu)" + (" — działa przez wtyczkę programu GameReader na PC"
                                                                        if w["wtyczka"] else "")
                        + ". Czytnik na Macu nie wie, gdzie na ekranie szukać tekstu, więc na razie jej nie pobieram.")
                elif p and p["id"] == i:
                    g["stan"], g["stan_opis"] = p["faza"], ("Pobieram paczkę…" if p["faza"] == "pobiera" else
                                                             "Sprawdzam pobraną paczkę (czy jest cała)…")
                elif i in self.bledy:
                    g["stan"], g["stan_opis"] = "blad_pobierania", self.bledy[i]
                    g["reczne"] = i in self.bledy_serwera  # not for a missing disk or low space: the site would not help
                else:
                    g["stan"], g["stan_opis"] = "nie_na_dysku", f"W bibliotece GameReadera — paczka ma {mb(w['rozmiar'])}."
                g["stan_nazwa"] = BIBLIOTEKA_STANY.get(g["stan"], "")
                gry.append(g)
            return {"stan": self.stan, "komunikat": self.komunikat, "data": self.data,
                    "data_tekst": self._kiedy(self.data) if self.data else None, "nr": self.nr,
                    "pobieranie": self.wlaczone, "wszystkich": len(self.wpisy),
                    "gry": sorted(gry, key=lambda g: g["nazwa"].lower())}

    # ---------------------------------------------------------- download
    def sprzataj(self):
        """Leftover `.czesc` files (and their `._` twins on exFAT disks) of a download cut short. Only old ones: a
        younger file may be another server's download in progress (a test server on the same folder)."""
        folder = self.katalog.folder
        if dysk_odlaczony(folder):
            return
        try:
            nazwy = os.listdir(folder)
        except OSError:
            return
        for n in nazwy:
            if n.endswith(".czesc"):
                plik = os.path.join(folder, n)
                try:
                    if time.time() - os.path.getmtime(plik) > STARA_CZESC_S:
                        os.remove(plik)
                        self.na_info(f"Usunąłem resztkę przerwanego pobierania: {n}")
                except OSError:
                    pass

    def _nazwa_pliku(self, w, folder):
        """`<name>.zip` from the download address (decoded, cleaned like a catalogue id); never an existing file."""
        baza = id_z_pliku(unquote(os.path.basename(urlsplit(w["url"]).path))) or id_z_pliku(w["nazwa"])
        nazwa, nr = baza + ".zip", 2
        while os.path.exists(os.path.join(folder, nazwa)) or os.path.exists(os.path.join(folder, nazwa + ".czesc")):
            nazwa, nr = f"{baza[:76]}-{nr}.zip", nr + 1
        return nazwa

    def pobierz(self, i):
        if not self.wlaczone:
            return {"ok": False, "blad": "Pobieranie z programu jest w tej instalacji wyłączone — pobierz paczkę ze strony "
                                         "gamereader.pl i wrzuć plik .zip do folderu gier."}
        with self.lock:
            w = self.wpisy.get(str(i))
        if not w:
            return {"ok": False, "blad": "Nie ma tej gry na liście biblioteki — lista mogła się zmienić. Odśwież stronę."}
        if not w["na_macu"]:
            return {"ok": False, "blad": "Ta paczka nie ma obszaru napisów — czytnik na Macu jej nie odczyta, więc jej "
                                         "nie pobieram."}
        if w["id"] in self.katalog.id_paczek():
            return {"ok": False, "blad": f"„{w['nazwa']}” już jest w „Moje gry”."}
        if not self.pob_lock.acquire(blocking=False):  # one at a time: a second "Pobierz" is refused, not queued
            p = self.postep()
            return {"ok": False, "blad": f"Pobieram już „{p['nazwa'] if p else 'inną grę'}” — poczekaj, aż skończy, "
                                         f"albo przerwij tamto pobieranie."}
        try:
            folder = self.katalog.folder
            dysk = dysk_odlaczony(folder)  # checked BEFORE any makedirs: nothing may land on the Mac's own disk
            if dysk:
                raise ValueError(f"Nie widzę dysku „{dysk}”, na którym leży folder gier. Podłącz dysk i kliknij "
                                 f"„Pobierz” jeszcze raz — nic nie zapisuję na dysku Maca.")
            try:
                os.makedirs(folder, exist_ok=True)
                wolne = shutil.disk_usage(folder).free
            except OSError as e:
                raise ValueError(f"Nie mogę pisać do folderu gier ({e}). Sprawdź dysk i spróbuj ponownie.")
            if wolne < w["rozmiar"] + ZAPAS_B:
                raise ValueError(f"Za mało miejsca na dysku z grami: paczka ma {mb(w['rozmiar'])}, wolne jest "
                                 f"{mb(wolne)} (zostawiam też {mb(ZAPAS_B)} zapasu). Zwolnij miejsce i spróbuj ponownie.")
            self.sprzataj()
            nazwa = self._nazwa_pliku(w, folder)
        except ValueError as e:
            self.pob_lock.release()
            with self.lock:
                self.bledy[w["id"]] = str(e)
                self.nr += 1
            return {"ok": False, "blad": str(e)}
        except BaseException:
            self.pob_lock.release()
            raise
        with self.lock:
            self.bledy.pop(w["id"], None)
            self.bledy_serwera.discard(w["id"])
            self.pob = {"id": w["id"], "nazwa": w["nazwa"], "rozmiar": w["rozmiar"], "pobrane": 0, "faza": "pobiera",
                        "przerwij": threading.Event(), "watek": None, "gniazdo": None,
                        "czesc": os.path.join(folder, nazwa + ".czesc"), "utworzony": False}
            self.nr += 1
            try:
                self.pob["watek"] = threading.Thread(target=self._pobieraj, args=(w, folder, nazwa), daemon=True)
                self.pob["watek"].start()
            except BaseException:  # no worker = nobody would ever release the one-download lock
                self.pob = None
                self.pob_lock.release()
                raise
        self.na_info(f"Pobieram z biblioteki: {w['nazwa']} ({mb(w['rozmiar'])}) → {nazwa}")
        return {"ok": True}

    def przerwij(self, czekaj_s=0):
        with self.lock:
            p = self.pob
        if not p:
            return {"ok": False, "blad": "Nic się teraz nie pobiera."}
        p["przerwij"].set()
        gniazdo = p.get("gniazdo")
        if gniazdo is not None:
            try:  # wakes a read waiting for data (up to CISZA_S) at once; close() could wait for that read's buffer lock
                gniazdo.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if czekaj_s and p["watek"]:
            p["watek"].join(czekaj_s)
            if p["watek"].is_alive() and p.get("utworzony"):  # the process is about to end: never leave our .czesc
                try:
                    os.remove(p["czesc"])
                except OSError:
                    pass
        return {"ok": True}

    def _pobieraj(self, w, folder, nazwa):
        cel = os.path.join(folder, nazwa)
        czesc = cel + ".czesc"
        p = self.pob
        try:
            f = open(czesc, "xb")  # exclusive: never another download's file
            p["utworzony"] = True
            with f, self._otworz(w["url"], CISZA_S) as r:
                p["gniazdo"] = getattr(getattr(r.fp, "raw", None), "_sock", None)  # for "Przerwij" (shutdown)
                while True:
                    if p["przerwij"].is_set():
                        raise Przerwane()
                    blok = r.read1(1 << 20)  # whatever has arrived (bar and "Przerwij" stay live); CISZA_S at most
                    if not blok:
                        break
                    f.write(blok)
                    with self.lock:
                        p["pobrane"] += len(blok)
                    if p["pobrane"] > w["rozmiar"]:
                        raise RozmiarInny("serwer wysyła więcej danych, niż podaje lista biblioteki")
            if p["pobrane"] != w["rozmiar"]:
                raise RozmiarInny(f"pobrałem {int(100 * p['pobrane'] / w['rozmiar'])} % paczki")
            with self.lock:
                p["faza"] = "sprawdza"
                self.nr += 1
            bez = sprawdz_paczke(czesc, w["rozmiar"], w["id"])
            if p["przerwij"].is_set():  # "Przerwij" / "Wyłącz program" during the check: nothing is published
                raise Przerwane()
            try:
                przenies_bez_nadpisania(czesc, cel)  # never overwrite a pack (another one took the name meanwhile)
            except FileExistsError:
                raise ValueError(f"w folderze gier pojawił się już plik {nazwa}")
            self.na_info(f"Pobrane i sprawdzone: {w['nazwa']} → {nazwa}"
                         + (f" ({bez} kwestii bez nagrania — czytnik je pomija)" if bez else ""))
            threading.Thread(target=self.katalog.skanuj, daemon=True).start()  # the tile moves to "Moje gry" at once
        except Exception as e:
            if isinstance(e, Przerwane) or p["przerwij"].is_set():  # a closed connection after "Przerwij" is a cancel
                self.na_info(f"Przerwane pobieranie: {w['nazwa']} — nic nie zapisałem.")
                return
            blad = self._opis_bledu(e, folder)
            if isinstance(e, RozmiarInny):  # a new pack version may have replaced the file after our list was read
                self.odswiez_spis()
                with self.lock:
                    nowy = self.wpisy.get(w["id"])
                if nowy and (nowy["rozmiar"], nowy["url"]) != (w["rozmiar"], w["url"]):
                    blad = (f"Paczka na serwerze zmieniła się w trakcie (nowa wersja {nowy['wersja']}, {mb(nowy['rozmiar'])}). "
                            f"Lista jest już odświeżona — kliknij „Pobierz” jeszcze raz.")
            serwer = isinstance(e, (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, ssl.SSLError,
                                    socket.gaierror)) or not isinstance(e, OSError)  # other OSError = our disk (gone, full)
            with self.lock:
                self.bledy[w["id"]] = blad
                if serwer:
                    self.bledy_serwera.add(w["id"])
            self.na_info(f"! Pobieranie {w['nazwa']} nie udało się: {blad} [{type(e).__name__}: {e}]")
        finally:
            if p["utworzony"]:  # only our own file (open "xb" failed = someone else's download — left alone)
                try:
                    os.remove(czesc)
                except OSError:
                    pass
            with self.lock:
                self.pob = None
                self.nr += 1
            self.pob_lock.release()

    @staticmethod
    def _opis_bledu(e, folder):
        if isinstance(e, urllib.error.HTTPError):
            return f"Serwer GameReadera odpowiedział błędem {e.code}. Spróbuj ponownie za chwilę."
        if isinstance(e, (TimeoutError, socket.timeout)):
            return (f"Serwer GameReadera przestał wysyłać dane ({CISZA_S} s bez postępu). Sprawdź internet i spróbuj "
                    f"ponownie — nic nie zapisałem.")
        if isinstance(e, urllib.error.URLError) or isinstance(e, (ConnectionError, socket.gaierror)):
            return "Brak połączenia z serwerem GameReadera. Sprawdź internet i spróbuj ponownie — nic nie zapisałem."
        if isinstance(e, OSError):
            dysk = dysk_odlaczony(folder)
            if dysk:
                return f"Dysk „{dysk}” zniknął w trakcie pobierania. Podłącz go i spróbuj ponownie — nic nie zapisałem."
            if getattr(e, "errno", None) == 28:
                return "Skończyło się miejsce na dysku z grami. Zwolnij miejsce i spróbuj ponownie — nic nie zapisałem."
            return f"Nie mogę zapisać paczki w folderze gier ({e}). Sprawdź dysk i spróbuj ponownie."
        if isinstance(e, RozmiarInny):
            return f"Połączenie z serwerem GameReadera urwało się w trakcie ({e}). Nic nie zapisałem — spróbuj ponownie."
        if isinstance(e, ValueError):
            return f"Pobrana paczka jest niepełna albo uszkodzona: {e}. Usunąłem ją — spróbuj ponownie."
        return f"Pobieranie przerwał nieoczekiwany błąd ({e}). Spróbuj ponownie."


BIBLIOTEKA_STANY = {"nie_na_dysku": "Do pobrania", "nie_na_macu": "Na razie nie na Macu", "pobiera": "Pobieram…",
                    "sprawdza": "Sprawdzam…", "blad_pobierania": "Nie pobrano"}


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
    def __init__(self, ustawienia=None, folder=None, pamiec=None, sprawdzone=SPRAWDZONE, spis=None,
                 hosty=HOSTY_BIBLIOTEKI, https=True, pobieranie=POBIERANIE):
        """spis = address of the library list; None (tests that do not ask for it) = no library, no network."""
        self.plik_ust = ustawienia or USTAWIENIA
        self.katalog = Katalog(folder or L.PACZKI, pamiec or PAMIEC, sprawdzone)
        # library cache inside the catalogue cache: a dot name can never be a catalogue id (hidden files are not scanned)
        self.biblioteka = Biblioteka(os.path.join(pamiec or PAMIEC, ".biblioteka"), self.katalog, url=spis, hosty=hosty,
                                     https=https, wlaczone=pobieranie, na_info=self.info)
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
        # "Wyłącz program" (panel button): unlike zamknij_o, a poll of an open window or phone never cancels it
        self.wylacz = False
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
        """Background start: scan the catalogue, then load the chosen game (holding the one lock); the library list."""
        self.sterowanie.acquire()
        threading.Thread(target=self._odswiez, daemon=True).start()
        self.biblioteka.sprzataj()
        self.biblioteka.uruchom()

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
                "Nie widzę paczki wybranej gry w folderze gier — podłącz dysk albo wybierz grę w katalogu.")
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
            return {"ok": False, "blad": "Nie widzę tej gry w folderze gier — odśwież katalog."}
        if not self._stop():
            return {"ok": False, "blad": "Lektor nie zatrzymał się — gry nie zmieniam. Spróbuj jeszcze raz; jeśli to się powtarza, zamknij panel i otwórz go ponownie."}
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
        self.biblioteka.pilnuj()
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

    def wylacz_program(self):
        """The user pressed "Wyłącz program": the watchdog stops the reader and ends the server within ~1 s."""
        with self.lock:
            self.wylacz = True
        self.info("Wyłączam program (przycisk w panelu)…")

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
                return {"ok": False, "blad": "Poprzedni lektor nie zatrzymał się (wyciszyłem go) — spróbuj za chwilę; jeśli to się powtarza, zamknij panel i otwórz go ponownie."}
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
            # PS5 without its app = nothing to read: refused before anything is opened or created (also no permission ask)
            if otworz_xbox and self.ust["konsola"] == "playstation" and not ps_remote_play():
                return {"ok": False, "blad": PS_BRAK}
            if not Quartz.CGPreflightScreenCaptureAccess():
                Quartz.CGRequestScreenCaptureAccess()
                return {"ok": False, "zgoda": True,
                        "blad": "macOS potrzebuje zgody na nagrywanie ekranu dla „Terminal” (to on uruchamia lektora). "
                                "Zezwól w okienku systemu albo w Ustawieniach, potem otwórz program ponownie."}
            src = L.ZrodloEkran(frac)
            if otworz_xbox:  # the console from the saved settings: the phone's Graj sends none
                blad = otworz_gre_zdalna(self.ust["konsola"])
                if blad:  # the app went away between the check and now
                    src.zamknij()
                    return {"ok": False, "blad": blad}
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
                                             "(.mp4, nazwany jak paczka) do folderu gier."}
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
                return {"ok": False, "blad": "Lektor nie zatrzymał się — spróbuj jeszcze raz; jeśli to się powtarza, zamknij panel i otwórz go ponownie."}
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
            return {"ok": True} if self._stop() else {"ok": False, "blad": "Lektor nie zatrzymał się w 5 s — wyciszyłem go. Jeśli to się powtarza, zamknij panel i otwórz go ponownie."}
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
                # the library list changes with its own number AND with the catalogue (a pack on disk leaves the list)
                "biblioteka_nr": f"{self.biblioteka.nr}.{self.katalog.nr}", "pobieranie": self.biblioteka.postep(),
                "wylacz": self.wylacz,  # every open window and phone shows "Program wyłączony", not a lost connection
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

    def _wyslij(self, kod, tresc, typ="application/json; charset=utf-8", naglowki=(), cache="no-store"):
        dane = tresc if isinstance(tresc, bytes) else json.dumps(tresc, ensure_ascii=False).encode()
        self.send_response(kod)
        self.send_header("Content-Type", typ)
        self.send_header("Cache-Control", cache)
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
                "name": "Lektor Gier na konsoli", "short_name": "Lektor", "lang": "pl", "display": "standalone",
                "start_url": f"/telefon?k={STAN.klucz}", "scope": "/", "id": "/telefon",
                "background_color": "#121318", "theme_color": "#121318",
                "icons": [{"src": f"/ikona-{r}.png", "sizes": f"{r}x{r}", "type": "image/png"} for r in (192, 512)],
            }, "application/manifest+json; charset=utf-8")
        if PUBLICZNE.fullmatch(sciezka):
            plik = os.path.join(PANEL, ALIASY.get(sciezka) or os.path.join(*sciezka.lstrip("/").split("/")))
            if os.path.exists(plik):
                return self._plik(plik, TYPY[plik.rsplit(".", 1)[1]])
        if sciezka == "/api/katalog":
            kat = STAN.katalog.publiczne()
            for g in kat["gry"]:  # the pack's own requirements win; a pack without them gets the library list's words
                if not g.get("wymagania") and g.get("id_paczki"):
                    g["wymagania"] = STAN.biblioteka.wymagania(g["id_paczki"])
            with STAN.lock:
                kat.update(wybrana=STAN.gra, ustawiona=STAN.ust.get("gra"))
            return self._wyslij(200, kat)
        if sciezka == "/api/biblioteka":
            return self._wyslij(200, STAN.biblioteka.publiczne())
        m = re.fullmatch(r"/api/biblioteka/okladka/([A-Za-z0-9_.-]{1,80})", sciezka)
        if m:  # pack id from the list only (dictionary lookup); covers never change for an id, so the phone may keep them
            dane = STAN.biblioteka.okladka(m.group(1))
            if dane is None:
                return self._wyslij(404, {"blad": "nie ma takiej okładki"})
            return self._wyslij(200, dane, typ_obrazka(dane) or "image/jpeg", cache="private, max-age=86400")
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
                for k in ("profil", "urzadzenie", "zrodlo", "kamera", "wyglad", "konsola"):
                    if k in dane:
                        try:
                            STAN.ust[k] = popraw_ustawienie(k, dane[k])
                        except ValueError:
                            pass
                STAN.zapisz_ustawienia()
            return self._wyslij(200, {"ok": True})
        if sciezka == "/api/wylacz":  # behind the pilot key from the network (_wejscie) and the X-Lektor header
            STAN.wylacz_program()
            return self._wyslij(200, {"ok": True})
        if sciezka in ("/api/gra-zdalna", "/api/xbox"):  # "Otwórz … ponownie"; /api/xbox = the name before PS5
            blad = otworz_gre_zdalna(STAN.ust["konsola"])
            return self._wyslij(200, {"ok": not blad, "blad": blad})
        if sciezka == "/api/zgoda-ekranu":
            Quartz.CGRequestScreenCaptureAccess()
            subprocess.Popen(["open", USTAWIENIA_EKRANU])
            return self._wyslij(200, {"ok": True})
        if sciezka == "/api/paczka":
            return self._wyslij(200, STAN.odswiez())
        if sciezka == "/api/biblioteka/pobierz":  # from the phone too (behind the pilot key): the pack lands on the Mac
            return self._wyslij(200, STAN.biblioteka.pobierz(dane.get("id")))
        if sciezka == "/api/biblioteka/przerwij":
            return self._wyslij(200, STAN.biblioteka.przerwij())
        if sciezka == "/api/biblioteka/odswiez":
            if STAN.biblioteka.url:
                threading.Thread(target=STAN.biblioteka.odswiez_spis, daemon=True).start()
            return self._wyslij(200, {"ok": True})
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
            zamknij_o, wylacz = STAN.zamknij_o, STAN.wylacz
        nikt = not STAN.obecni() and time.time() - STAN.start_t > LIMIT[("mac", False)]
        if not wylacz and STAN.biblioteka.trwa() and ((zamknij_o and time.time() > zamknij_o) or nikt):
            # closing the panel window must not cut a download: the server waits for it — but the reader goes silent
            # now, as it always did 5 s after the window closed (it must not keep talking for minutes of a big pack)
            if STAN.sesja is not None:
                STAN.stop(czekaj=True)
                STAN.info("Panel zamknięty — lektor zatrzymany; trwa pobieranie gry, kończę program, gdy się skończy.")
            continue
        if wylacz or (zamknij_o and time.time() > zamknij_o) or nikt:
            if STAN.biblioteka.trwa():  # "Wyłącz program": the download stops and its .czesc file is removed
                STAN.biblioteka.przerwij(czekaj_s=5)
            STAN.info("Program wyłączony przyciskiem — kończę lektora." if wylacz else "Panel zamknięty — kończę lektora.")
            if wylacz:
                time.sleep(1.5)  # the other windows and phones poll every second: let them read "wylacz" first
            STAN.stop(czekaj=True)
            serwer.shutdown()
            return


class Serwer(ThreadingHTTPServer):
    """IPv6 + IPv4 on one socket: the phone may reach MacBook….local over IPv6 first (Bonjour gives both)."""
    address_family = socket.AF_INET6
    # listen backlog: the default 5 resets connections when the browser asks for dozens of library covers at once
    # (measured 2026-11-25: 33 of 96 covers "Connection reset by peer")
    request_queue_size = 128

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def main():
    global STAN
    try:
        serwer = Serwer(("::", PORT), Obsluga)
    except OSError:
        sys.exit(f"Port {PORT} zajęty — panel już działa? Otwórz http://127.0.0.1:{PORT}/")
    STAN = Stan(spis=SPIS_URL)
    STAN.uruchom()
    threading.Thread(target=straznik, args=(serwer,), daemon=True).start()
    STAN.info(f"Panel lektora gier: http://127.0.0.1:{PORT}/ · pilot w sieci: {STAN.bonjour or adres_ip()}:{PORT}")
    serwer.serve_forever()


if __name__ == "__main__":
    main()
