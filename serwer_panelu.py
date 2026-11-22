#!/usr/bin/env python3
"""GTA Lektor — panel server (port 8797): the Mac panel on 127.0.0.1 and an iPhone remote over home Wi-Fi.

Serves panel/index.html and a small JSON API that drives lektor.py: start/stop the reader, live volume,
output device, Chrome with Xbox Remote Play, demo. Requests from this Mac (127.0.0.1) work as before; every
request from the network needs the secret key `k` (kept in ustawienia.json, shown on the Mac panel as a QR code).
Profile "iphone": matched lines are not played on the Mac but sent to the phone (long-poll), which plays them.
Started by "GTA Lektor.app"; log in ~/Library/Logs/gta-lektor.log.
Test instance: LEKTOR_PORT=8798 LEKTOR_USTAWIENIA=/tmp/x.json .venv/bin/python serwer_panelu.py
"""
import base64
import collections
import hmac
import json
import os
import re
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
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
# the voice pack (.zip) and an optional demo video (.mp4): LEKTOR_PACZKA / LEKTOR_DEMO, else the first file in the
# packs folder (LEKTOR_PACZKI or paczki/; per-install values may sit in lokalne.env — see lektor.wczytaj_lokalne)
paczka_plik = lambda: L.znajdz(".zip", "LEKTOR_PACZKA")
demo_plik = lambda: L.znajdz(".mp4", "LEKTOR_DEMO")
XBOX = "https://www.xbox.com/pl-PL/play/consoles"
USTAWIENIA_EKRANU = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
# glosnosc = the lektor on the Mac; glosnosc_iphone = extra boost on the phone, on top of its side buttons (which a web
# page can neither read nor set) — kept apart so a 200 % phone boost never blasts the Mac speakers
DOMYSLNE = {"profil": "tv_mac", "urzadzenie": "", "glosnosc": 1.0, "glosnosc_iphone": 1.0, "zrodlo": "ekran", "kamera": 0}
PROFILE = ("tv_mac", "tv_glosnik", "sluchawki", "iphone")
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
    def __init__(self):
        self.lock = threading.RLock()
        self.sterowanie = threading.Lock()  # serialises start()
        self.p = self.m = None
        self.paczka_blad = "wczytuję paczkę…"
        self.sesja = None  # dict: watek, stop, glos, zrodlo, profil, start
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
        self.ust = dict(DOMYSLNE)
        self.klucz = None
        try:
            with open(USTAWIENIA) as f:
                zapis = json.load(f)
            self.ust.update({k: v for k, v in zapis.items() if k in DOMYSLNE})
            if re.fullmatch(r"[A-Za-z0-9_-]{12,64}", str(zapis.get("klucz", ""))):
                self.klucz = zapis["klucz"]
        except (OSError, ValueError):
            pass
        if self.klucz is None:
            self.nowy_klucz()
        threading.Thread(target=self.wczytaj_paczke, daemon=True).start()

    # ---------------------------------------------------------- pack
    def wczytaj_paczke(self):
        with self.lock:
            self.paczka_blad = "wczytuję paczkę…"
        plik = paczka_plik()
        if not plik or not os.path.exists(plik):
            with self.lock:
                self.paczka_blad = ("Nie widzę paczki lektora. Pobierz paczkę GTA VI z gamereader.pl, wrzuć plik "
                                    ".zip do folderu paczek (przycisk obok) i kliknij „Sprawdź ponownie”. "
                                    "Folder paczek na dysku zewnętrznym? Podłącz dysk.")
            return
        try:
            p = L.Paczka(plik)
            m = L.Dopasowanie(p)
        except BaseException as e:  # Paczka uses sys.exit on a broken file
            with self.lock:
                self.paczka_blad = f"Paczka uszkodzona albo nie z GameReadera 2.0: {e}"
            return
        with self.lock:
            self.p, self.m, self.paczka_blad = p, m, None
        self.info(f"Paczka gotowa: {p.manifest.get('name', '?')} v{p.manifest.get('version', '?')}, {len(p.ids)} kwestii")

    def zapisz_ustawienia(self):
        try:  # the key lives next to the settings but never in self.ust (which /api/stan returns)
            with open(USTAWIENIA, "w") as f:
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
    def nadaj(self, ev):
        with self.cv:
            self.nr += 1
            ev.update(n=self.nr, t=time.time())
            self.zdarzenia.append(ev)
            self.cv.notify_all()

    def czekaj_na_zdarzenia(self, od, ile_s):
        with self.cv:
            if od > self.nr:  # the phone remembers a server from before a restart: start counting afresh
                od = -1
            if od >= 0:
                self.cv.wait_for(lambda: self.nr > od, ile_s)
            t = time.time()
            return self.nr, [{**{k: v for k, v in e.items() if k != "t"}, "wiek_ms": int(1000 * (t - e["t"]))}
                             for e in self.zdarzenia if od >= 0 and e["n"] > od]

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

    def dzwiek(self, idx, fmt):
        """Clip for the phone: Ogg Opus straight from the pack, or WAV (Core Audio afconvert, ~20-30 ms)."""
        with self.lock:
            p = self.p
        if p is None or not (0 <= idx < len(p.ids)) or not p.audio[idx]:
            return None, None
        dane = p.dzwiek(idx)
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
    def start(self, *args, **kw):
        with self.sterowanie:  # one start at a time: a double click must never leave two voices running
            self.stop()
            return self._start(*args, **kw)

    def _start(self, zrodlo, profil, urzadzenie, glosnosc, kamera, otworz_xbox=True):
        if profil not in PROFILE:
            profil = "tv_mac"
        if zrodlo not in ("ekran", "kamera", "demo"):
            return {"ok": False, "blad": f"Nieznane źródło obrazu: {zrodlo}"}
        with self.lock:
            if self.p is None:
                return {"ok": False, "blad": self.paczka_blad or "Paczka jeszcze się wczytuje."}
            klucz_gl = "glosnosc_iphone" if profil == "iphone" else "glosnosc"
            if glosnosc is None:
                glosnosc = self.ust[klucz_gl]
            self.ust.update(profil=profil, urzadzenie=urzadzenie, kamera=int(kamera))
            self.ust[klucz_gl] = max(0.0, min(2.0, float(glosnosc)))
            if zrodlo != "demo":  # the demo must not change how the user normally plays
                self.ust["zrodlo"] = zrodlo
            self.zapisz_ustawienia()
            p, m = self.p, self.m
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
        else:  # demo
            demo = demo_plik()
            if not demo or not os.path.exists(demo):
                return {"ok": False, "blad": "Nie ma nagrania demo — wrzuć krótki film z napisami GTA VI (.mp4) do folderu „paczki”."}
            src = L.ZrodloWideo(demo, frac)
        if profil == "iphone":
            glos = GlosTelefon(self, self.ust["glosnosc_iphone"])
        else:
            try:
                glos = L.Glos(p, self.ust["glosnosc"], 2, False, urzadzenie=self.indeks_urzadzenia(urzadzenie))
            except Exception as e:
                return {"ok": False, "blad": f"Nie mogę otworzyć dźwięku: {e}"}
        glos.na_info = self.info
        glos.start()
        okno = None
        if zrodlo == "demo":
            try:
                okno = subprocess.Popen(["ffplay", "-loglevel", "quiet", "-an", "-autoexit", "-x", "960", "-y", "540",
                                         "-window_title", "GTA VI - demo lektora", demo])
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
            self.sesja = sesja
            self.kwestie.clear()
        sesja["watek"].start()
        self.info({"ekran": "Lektor czyta napisy z ekranu (gra zdalna).", "kamera": "Lektor czyta obraz z karty.",
                   "demo": "Demo: scenka z GTA VI."}[zrodlo] + (" Głos: iPhone." if profil == "iphone" else ""))
        return {"ok": True}

    def stop(self):
        with self.lock:
            s = self.sesja
        if s is None:
            return
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
        with self.lock:
            if self.sesja is s:
                self.sesja = None

    def glosnosc(self, v, iphone=False):
        klucz = "glosnosc_iphone" if iphone else "glosnosc"
        with self.lock:
            self.ust[klucz] = max(0.0, min(2.0, float(v)))
            if self.sesja and (self.sesja["profil"] == "iphone") == iphone:
                self.sesja["glos"].glosnosc = self.ust[klucz]
            self.zapisz_ustawienia()

    def adresy_pilota(self):
        ip = adres_ip()
        q = f"/telefon?k={self.klucz}"
        return {"local": f"http://{self.bonjour}:{PORT}{q}" if self.bonjour else None,
                "ip": f"http://{ip}:{PORT}{q}" if ip else None}

    def stan(self, lokalny):
        demo = demo_plik()
        with self.lock:
            s = self.sesja
            ostrz = next((t for _, t in reversed(self.komunikaty) if t.startswith("!")), None)
            slucha = self.telefon_slucha()
            if s and s["profil"] == "iphone" and not slucha:
                ostrz = ("! Lektor ma grać z iPhone'a, ale żaden telefon nie ma włączonego dźwięku — otwórz pilota "
                         "w iPhonie i stuknij „Włącz dźwięk w tym iPhonie”.")
            op = list(self.opoznienia)
            wynik = {
                "gotowy": self.p is not None, "paczka_blad": self.paczka_blad,
                "paczka": (self.p.manifest.get("name") if self.p else None),
                "dziala": s is not None, "zrodlo": s["zrodlo"] if s else None, "profil": s["profil"] if s else None,
                "od": int(time.time() - s["start"]) if s else 0,
                "kwestie": list(self.kwestie), "ostrzezenie": ostrz if s else None,
                "komunikaty": [f"{c} {t}" for c, t in list(self.komunikaty)[-8:]],
                "ustawienia": self.ust, "zgoda_ekranu": bool(Quartz.CGPreflightScreenCaptureAccess()),
                "demo": bool(demo and os.path.exists(demo)),  # a path set for a disk that is not plugged in = no demo
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


STAN = Stan()


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
        if sciezka == "/api/urzadzenia":
            return self._wyslij(200, {"urzadzenia": STAN.urzadzenia(odswiez="odswiez" in q)})
        if sciezka == "/api/qr.png" and not siec:
            adr = STAN.adresy_pilota().get((q.get("co") or ["local"])[0])
            if not adr:
                return self._wyslij(404, {"blad": "brak adresu w sieci"})
            if adr not in STAN.qr_cache:
                STAN.qr_cache[adr] = kod_qr(adr)
            return self._wyslij(200, STAN.qr_cache[adr], "image/png")
        if sciezka == "/api/telefon/kwestie":  # long-poll: returns at once when a line is matched
            cid = self._klient(q, siec)
            try:
                od = int((q.get("od") or ["-1"])[0])
            except ValueError:
                od = -1
            with STAN.lock:
                STAN.sluchacze[cid] = time.time() + 24  # the poll waits at most 20 s
            try:
                nr, zd = STAN.czekaj_na_zdarzenia(od, 20)
            finally:
                with STAN.lock:
                    STAN.sluchacze[cid] = time.time() + 4  # the phone reconnects at once
            if (q.get("f") or [""])[0] == "opus":  # the clip rides along: one Wi-Fi round trip instead of two
                for e in zd:
                    if e["typ"] == "kwestia":
                        dane, _ = STAN.dzwiek(e["idx"], "opus")
                        if dane:
                            e["audio"] = base64.b64encode(dane).decode()
            with STAN.lock:
                g = STAN.ust["glosnosc_iphone"]
            return self._wyslij(200, {"nr": nr, "zdarzenia": zd, "glosnosc": g})
        if sciezka == "/api/telefon/probka":  # a real pack clip for the phone's codec self-test
            with STAN.lock:
                p = STAN.p
            if p is None:
                return self._wyslij(503, {"blad": "paczka się wczytuje"})
            return self._wyslij(200, {"idx": next(i for i, a in enumerate(p.audio) if a)})
        m = re.fullmatch(r"/api/telefon/dzwiek/(\d{1,7})", sciezka)
        if m:
            dane, typ = STAN.dzwiek(int(m.group(1)), (q.get("f") or ["opus"])[0])
            if dane is None:
                return self._wyslij(404, {"blad": "nie ma takiego nagrania"})
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
                               dane.get("kamera", u["kamera"]), dane.get("otworz_xbox", True))
            return self._wyslij(200, wynik)
        if sciezka == "/api/stop":
            STAN.stop()
            return self._wyslij(200, {"ok": True})
        if sciezka == "/api/glosnosc":
            STAN.glosnosc(dane.get("v", 1.0), bool(dane.get("iphone")))
            return self._wyslij(200, {"ok": True})
        if sciezka == "/api/ustawienia":
            with STAN.lock:
                for k in ("profil", "urzadzenie", "zrodlo", "kamera"):
                    if k in dane and (k != "profil" or dane[k] in PROFILE):
                        STAN.ust[k] = dane[k]
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
            threading.Thread(target=STAN.wczytaj_paczke, daemon=True).start()
            return self._wyslij(200, {"ok": True})
        if sciezka == "/api/folder-paczek" and not siec:
            os.makedirs(L.PACZKI, exist_ok=True)
            subprocess.Popen(["open", L.PACZKI])
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
    around, or every client went silent past its limit (see LIMIT). The start gets the Mac idle limit as grace."""
    while True:
        time.sleep(1)
        with STAN.lock:
            zamknij_o = STAN.zamknij_o
        nikt = not STAN.obecni() and time.time() - STAN.start_t > LIMIT[("mac", False)]
        if (zamknij_o and time.time() > zamknij_o) or nikt:
            STAN.info("Panel zamknięty — kończę lektora.")
            STAN.stop()
            serwer.shutdown()
            return


class Serwer(ThreadingHTTPServer):
    """IPv6 + IPv4 on one socket: the phone may reach MacBook….local over IPv6 first (Bonjour gives both)."""
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def main():
    try:
        serwer = Serwer(("::", PORT), Obsluga)
    except OSError:
        sys.exit(f"Port {PORT} zajęty — panel już działa? Otwórz http://127.0.0.1:{PORT}/")
    threading.Thread(target=straznik, args=(serwer,), daemon=True).start()
    STAN.info(f"Panel GTA Lektor: http://127.0.0.1:{PORT}/ · pilot w sieci: {STAN.bonjour or adres_ip()}:{PORT}")
    serwer.serve_forever()


if __name__ == "__main__":
    main()
