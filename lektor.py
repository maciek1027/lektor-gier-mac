#!/usr/bin/env python3
"""Lektor GTA — Polish voice-over from GameReader 2.0 voice packs, on macOS.

Pipeline: game picture (capture card / screen / recording) -> subtitle region -> macOS Vision OCR
(pl-PL) -> fuzzy match against the pack's subtitles.json -> play the matching audio/<id>.opus.

Examples:
  lektor.py --paczka PACK.zip --ekran                 # game in a fullscreen window (Remote Play)
  lektor.py --paczka PACK.zip --kamera 0              # HDMI capture card (UVC)
  lektor.py --paczka PACK.zip --wideo clip.mp4        # demo on a recording
  lektor.py --lista-kamer
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import zipfile

import cv2
import numpy as np
import objc
import Quartz
import Vision
from Foundation import NSData
from rapidfuzz import fuzz, process

FOLDER = os.path.dirname(os.path.abspath(__file__))
PODGLAD = os.path.join(FOLDER, "podglad.png")
PACZKI = os.path.join(FOLDER, "paczki")  # put the GameReader 2.0 voice pack (.zip) and an optional demo video (.mp4) here


def znajdz(rozszerzenie, zmienna):
    """Pack / demo path: the environment variable first, else the first matching file in paczki/ (None if none)."""
    if os.environ.get(zmienna):
        return os.environ[zmienna]
    try:
        pliki = sorted(f for f in os.listdir(PACZKI) if f.lower().endswith(rozszerzenie) and not f.startswith("."))
    except OSError:
        return None
    return os.path.join(PACZKI, pliki[0]) if pliki else None


def norm(text):
    """Lowercase, drop diacritics and punctuation — OCR-tolerant comparison key."""
    s = text.lower().replace("ł", "l")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- voice pack
class Paczka:
    def __init__(self, path):
        if not os.path.exists(path):
            sys.exit(f"Nie ma paczki: {path} — wrzuć plik .zip paczki GameReadera do folderu paczki/")
        self.zip = zipfile.ZipFile(path)
        self.lock = threading.Lock()
        self.manifest = self._json("manifest.json", {})
        self.preset = self._json("preset.json", {})
        self.settings = self._json("settings.json", {})
        entries = self._json("subtitles.json")["entries"]
        audio = {}
        for name in self.zip.namelist():
            if name.startswith("audio/") and "." in name:
                audio[name[6:].rsplit(".", 1)[0]] = name
        self.ids = [e["id"] for e in entries]
        self.texts = [e["text"] for e in entries]
        self.norm = [norm(t) for t in self.texts]
        self.raw = [t.lower() for t in self.texts]
        self.audio = [audio.get(i) for i in self.ids]

    def _json(self, name, default=None):
        try:
            return json.loads(self.zip.read(name))
        except KeyError:
            if default is None:
                sys.exit(f"Paczka nie ma pliku {name} — to nie jest paczka GameReader 2.0")
            return default

    def roi(self):
        """Subtitle region as fractions of the game picture + centre bands for line filtering."""
        rois = self.preset.get("rois") or [{}]
        r = rois[0].get("rect") or {"x": 0.30, "y": 0.79, "width": 0.40, "height": 0.15}
        bands = [b for b in (rois[0].get("center_line_filter") or {}).get("bands", []) if b.get("enabled")]
        forget = ((rois[0].get("multi_line") or {}).get("forget_frames")) or 5
        return (r["x"], r["y"], r["width"], r["height"]), bands, forget

    def dzwiek(self, idx):
        with self.lock:
            return self.zip.read(self.audio[idx])


# ---------------------------------------------------------------- matching
def prog(n):
    """Minimum similarity by text length: short lines must match (almost) exactly."""
    return 100 if n < 10 else 90 if n < 20 else 82


class Dopasowanie:
    def __init__(self, paczka):
        self.p = paczka
        self.ostatni = None  # index of the last matched line (dialogue lines sit next to each other)

    def znajdz(self, text):
        q = norm(text)
        if len(q) < 3:
            return None, 0
        found = process.extract(q, self.p.norm, scorer=fuzz.ratio, score_cutoff=prog(len(q)), limit=25)
        if not found:
            best = process.extractOne(q, self.p.norm, scorer=fuzz.ratio)
            return None, (best[1] if best else 0)
        top = found[0][1]
        pool = [f for f in found if f[1] >= top - 2]

        def waga(f):
            _, score, i = f
            bonus = 0.1 * fuzz.ratio(text.lower(), self.p.raw[i])  # punctuation / diacritics as tie-break
            if self.ostatni is not None:
                d = abs(i - self.ostatni)
                bonus += 3 if d <= 30 else 1.5 if d <= 300 else 0
            if self.p.audio[i] is None:
                bonus -= 50
            return score + bonus

        _, score, idx = max(pool, key=waga)
        self.ostatni = idx
        return idx, score


# ---------------------------------------------------------------- OCR (macOS Vision)
def ocr(img, bands):
    """Returns subtitle lines (top to bottom) recognised in the region image."""
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        return []
    with objc.autorelease_pool():
        data = NSData.dataWithBytes_length_(buf.tobytes(), len(buf))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setRecognitionLanguages_(["pl-PL"])
        req.setUsesLanguageCorrection_(True)
        handler.performRequests_error_([req], None)
        lines = []
        for obs in req.results() or []:
            cand = obs.topCandidates_(1)
            if not cand:
                continue
            bb = obs.boundingBox()
            x0, x1 = bb.origin.x, bb.origin.x + bb.size.width
            if bands and not any(x0 < b["start_ratio"] + b["width_ratio"] and x1 > b["start_ratio"] for b in bands):
                continue
            lines.append((bb.origin.y + bb.size.height, str(cand[0].string())))
    return [t for _, t in sorted(lines, key=lambda l: -l[0])]


# ---------------------------------------------------------------- picture sources
def kadr_gry(w, h):
    """Largest centred 16:9 rectangle (the game picture inside a 16:10 screen or a 16:9 frame)."""
    if w * 9 >= h * 16:
        gw, gh = h * 16 / 9, h
    else:
        gw, gh = w, w * 9 / 16
    return (w - gw) / 2, (h - gh) / 2, gw, gh


def wytnij(kadr, frac):
    gx, gy, gw, gh = kadr
    fx, fy, fw, fh = frac
    return gx + fx * gw, gy + fy * gh, fw * gw, fh * gh


class ZrodloWideo:
    """Capture card (camera index) or a recorded video file."""

    def __init__(self, src, frac, szybko=False):
        self.plik = isinstance(src, str)
        self.cap = cv2.VideoCapture(src) if self.plik else cv2.VideoCapture(src, cv2.CAP_AVFOUNDATION)
        if not self.cap.isOpened():
            sys.exit(f"Nie mogę otworzyć {'pliku' if self.plik else 'urządzenia'}: {src}"
                     + ("" if self.plik else " — sprawdź --lista-kamer i zgodę na kamerę dla Terminala"))
        if not self.plik:  # ask for Full HD — some cards start in a small 4:3 mode
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            w, h = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH), self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            log(f"Karta przechwytująca: obraz {w:.0f}×{h:.0f}" + ("" if abs(w / max(h, 1) - 16 / 9) < 0.05 else
                "  ! to nie 16:9 — obszar napisów może być w złym miejscu, sprawdź podglad.png"))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
        self.frac, self.szybko, self.t0, self.koniec = frac, szybko, None, False
        self.pozycja = 0.0  # seconds of video consumed (file mode)
        self.klatek = 0  # frames read so far (file mode) — own counter, container timestamps are unreliable

    def czas(self):
        return self.klatek / self.fps if self.plik else None

    def zamknij(self):
        self.cap.release()

    def _klatka(self, krok):
        if not self.plik:
            for _ in range(2):  # drop a buffered frame so we read the newest one
                if not self.cap.grab():
                    return None
            ok, f = self.cap.retrieve()
            return f if ok else None
        if self.t0 is None:
            self.t0 = time.time()
        cel = self.pozycja + krok if self.szybko else time.time() - self.t0
        while (self.klatek + 1) / self.fps < cel:
            if not self.cap.grab():
                self.koniec = True
                return None
            self.klatek += 1
        ok, f = self.cap.read()
        self.klatek += 1
        self.pozycja = cel
        if not ok:
            self.koniec = True
            return None
        return f

    def obszar(self, krok):
        f = self._klatka(krok)
        if f is None:
            return None
        h, w = f.shape[:2]
        x, y, rw, rh = (int(round(v)) for v in wytnij(kadr_gry(w, h), self.frac))
        return f[y:y + rh, x:x + rw]

    def podglad(self, path):
        f = self._klatka(0)
        if f is None:
            return False
        h, w = f.shape[:2]
        x, y, rw, rh = (int(round(v)) for v in wytnij(kadr_gry(w, h), self.frac))
        cv2.imwrite(path.replace(".png", "_obszar.png"), f[y:y + rh, x:x + rw])
        cv2.rectangle(f, (x, y), (x + rw, y + rh), (0, 0, 255), 3)
        return cv2.imwrite(path, f)


class ZrodloEkran:
    """Main display via the system `screencapture` tool (needs Screen Recording permission)."""

    def __init__(self, frac, obszar=None):
        b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        self.ekran = (b.size.width, b.size.height)
        if obszar:
            kadr = obszar
        else:  # fullscreen apps sit below the camera notch on MacBooks
            try:
                import AppKit
                gora = float(AppKit.NSScreen.mainScreen().safeAreaInsets().top)
            except Exception:
                gora = 0.0
            gx, gy, gw, gh = kadr_gry(self.ekran[0], self.ekran[1] - gora)
            kadr = (gx, gy + gora, gw, gh)
        self.rect = tuple(int(round(v)) for v in wytnij(kadr, frac))
        self.kadr = tuple(int(round(v)) for v in kadr)
        self.tmp = os.path.join(tempfile.mkdtemp(prefix="lektor-ekran-"), "obszar.png")
        self.koniec = False

    def zamknij(self):
        pass

    def _zrzut(self, rect, path):
        x, y, w, h = rect  # -R takes points; the PNG comes out in Retina pixels
        if os.path.exists(path):
            os.remove(path)  # never read a stale capture
        r = subprocess.run(["screencapture", "-x", "-t", "png", f"-R{x},{y},{w},{h}", path], capture_output=True)
        return r.returncode == 0 and os.path.exists(path)

    def obszar(self, krok):
        if not self._zrzut(self.rect, self.tmp):
            return None
        return cv2.imread(self.tmp)

    def podglad(self, path):
        if self._zrzut(self.rect, path.replace(".png", "_obszar.png")) is False:
            return False  # the exact region the reader will OCR — check it shows the subtitles
        if not self._zrzut((0, 0, int(self.ekran[0]), int(self.ekran[1])), path):
            return False
        img = cv2.imread(path)
        s = img.shape[1] / self.ekran[0]  # Retina: pixels per point
        x, y, w, h = (int(v * s) for v in self.rect)
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), 4)
        return cv2.imwrite(path, img)


# ---------------------------------------------------------------- voice playback
class Glos(threading.Thread):
    """One audio stream kept open for the whole session (the output never goes to sleep between lines,
    which used to eat the first/last syllable: pack clips start ~30 ms into speech and end ~20 ms after it).
    A worker thread decodes the next line (Core Audio `afconvert`) while the current one plays."""

    SR = 48000
    PRZED, PO = 0.03, 0.08  # seconds of silence around each line (the stream is always open, so little is needed)

    def __init__(self, paczka, glosnosc, kolejka, cicho, urzadzenie=None):
        super().__init__(daemon=True)
        self.p, self.glosnosc, self.cicho = paczka, glosnosc, cicho  # glosnosc may change live (0..2)
        self.q = collections.deque(maxlen=kolejka)
        self.cv = threading.Condition()
        self.tmp = tempfile.mkdtemp(prefix="lektor-glos-")
        self.gra, self.poz = None, 0  # samples being played and position (read by the audio callback)
        self.lock = threading.Lock()
        self.wolne = threading.Event()
        self.wolne.set()
        self.wydane = 0  # samples sent to the speakers (for tests)
        self.na_info = log
        self.stream = None
        if not cicho:
            import sounddevice as sd
            self.stream = sd.OutputStream(samplerate=self.SR, channels=1, dtype="float32", device=urzadzenie,
                                          latency="high", callback=self._callback)
            self.stream.start()

    def zamknij(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def _callback(self, out, frames, czas, status):
        with self.lock:
            if self.gra is None:
                out.fill(0)  # keep streaming silence: the device stays awake
                return
            n = min(frames, len(self.gra) - self.poz)
            out[:n, 0] = np.clip(self.gra[self.poz:self.poz + n] * self.glosnosc, -1, 1)
            out[n:] = 0
            self.poz += n
            self.wydane += n
            if self.poz >= len(self.gra):
                self.gra = None
                self.wolne.set()

    def _dekoduj(self, idx):
        src, dst = os.path.join(self.tmp, "kwestia.opus"), os.path.join(self.tmp, "kwestia.wav")
        with open(src, "wb") as f:
            f.write(self.p.dzwiek(idx))
        r = subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{self.SR}", "-c", "1", src, dst],
                           capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode(errors="replace").strip() or "afconvert padł")
        import wave
        with wave.open(dst) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        return np.concatenate([np.zeros(int(self.SR * self.PRZED), np.float32), x,
                               np.zeros(int(self.SR * self.PO), np.float32)])

    def dodaj(self, idx):
        with self.cv:
            if len(self.q) == self.q.maxlen:
                self.na_info(f"   (pomijam zaległą kwestię — lektor nie nadąża: {self.p.texts[self.q[0]]!r})")
            self.q.append(idx)
            self.cv.notify()

    def zajety(self):
        return bool(self.q) or not self.wolne.is_set()

    def run(self):
        while True:
            with self.cv:
                while not self.q:
                    self.cv.wait()
                idx = self.q.popleft()
            if self.cicho:
                continue
            try:
                x = self._dekoduj(idx)
            except Exception as e:  # one broken file must not stop the voice
                self.na_info(f"   ! nie odtworzyłem {self.p.ids[idx]}: {e}")
                continue
            self.wolne.wait()  # previous line finished
            with self.lock:
                self.gra, self.poz = x, 0
                self.wolne.clear()


# ---------------------------------------------------------------- main loop
def czytaj(p, m, glos, src, stop=None, szczegoly=False, na_kwestie=None, na_info=log):
    """Reading loop: subtitle region -> OCR -> match -> voice. Ends at the end of a recording, on Ctrl+C
    or when `stop` (threading.Event) is set. `na_kwestie(tekst_z_ekranu, idx, zgodnosc)` reports spoken lines."""
    _, bands, forget = p.roi()
    ustaw = p.settings.get("ocr", {})
    krok_min = ustaw.get("capture_interval", 250) / 1000
    krok_max = ustaw.get("backoff_max_interval_ms", 500) / 1000 if ustaw.get("adaptive_capture", True) else krok_min
    po_ilu = ustaw.get("backoff_after_quiet_ticks", 12)
    ostatni_ocr, ostatni_tekst, cisza, krok = "", None, 0, krok_min
    nieznane, licz, bez_tekstu_od = set(), collections.Counter(), time.time()

    def czekaj(s):
        if stop is not None:
            stop.wait(s)
        else:
            time.sleep(s)

    try:
        while not src.koniec and not (stop is not None and stop.is_set()):
            t0 = time.time()
            img = src.obszar(krok)
            if img is not None and img.size:
                linie = ocr(img, bands)
                tekst = " ".join(linie).strip()
                if len(norm(tekst)) < 3:
                    cisza += 1
                    if cisza >= forget:
                        ostatni_ocr, ostatni_tekst = "", None
                    if cisza >= po_ilu:
                        krok = krok_max
                    if time.time() - bez_tekstu_od > 90 and not licz["glos"]:
                        na_info("! Od 90 s nie widzę napisów — czy obraz gry jest na pełnym ekranie, "
                                f"a napisy w grze włączone? Podgląd: {PODGLAD}")
                        bez_tekstu_od = time.time()
                else:
                    cisza, krok, bez_tekstu_od = 0, krok_min, time.time()
                    if fuzz.ratio(norm(tekst), norm(ostatni_ocr)) < 90:  # a different subtitle than last tick
                        ostatni_ocr = tekst
                        idx, score = m.znajdz(tekst)
                        if idx is None:
                            licz["bez"] += 1
                            if norm(tekst) not in nieznane:
                                nieznane.add(norm(tekst))
                                na_info(f"   ? bez dopasowania ({score:.0f}): {tekst!r}")
                        elif p.norm[idx] == ostatni_tekst:
                            pass  # same line re-read with OCR jitter — do not speak it twice
                        else:
                            ostatni_tekst = p.norm[idx]
                            licz["glos"] += 1
                            stan = "" if p.audio[idx] else "  [BRAK PLIKU GŁOSU w paczce]"
                            kiedy = f"[nagranie {src.czas():5.1f} s] " if getattr(src, "plik", False) else ""
                            # show what is on screen: the pack's text list has cut-off endings,
                            # its recordings do not (checked by speech-to-text on the clips)
                            szcz = f"  ← paczka {p.ids[idx]} {p.texts[idx]!r}" if szczegoly else ""
                            na_info(f"🎙  {kiedy}{tekst}  (zgodność {score:.0f}){stan}{szcz}")
                            if na_kwestie:
                                na_kwestie(tekst, idx, score)
                            if p.audio[idx]:
                                glos.dodaj(idx)
            elif isinstance(src, ZrodloEkran):
                na_info("! Zrzut ekranu się nie udał — brak zgody na nagrywanie ekranu?")
                licz["zrzut_blad"] += 1
                czekaj(2)
            if not getattr(src, "szybko", False):
                czekaj(max(0.0, krok - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    return licz


def lista_kamer():
    r = subprocess.run(["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                       capture_output=True, text=True)
    out = r.stderr
    if "video devices" not in out:
        print("Nie udało się wypisać urządzeń (potrzebny ffmpeg).")
        return
    print("Urządzenia obrazu (numer = --kamera N):")
    for line in out.split("audio devices")[0].splitlines():
        m = re.search(r"\[(\d+)\] (.+)$", line)
        if m:
            print(f"  {m.group(1)}: {m.group(2)}")


def main():
    ap = argparse.ArgumentParser(description="Polski lektor z paczek GameReader 2.0 na macOS")
    ap.add_argument("--paczka", default=znajdz(".zip", "LEKTOR_PACZKA"), help="domyślnie pierwszy .zip w paczki/")
    zr = ap.add_mutually_exclusive_group()
    zr.add_argument("--ekran", action="store_true", help="obraz gry na głównym ekranie (np. gra zdalna)")
    zr.add_argument("--kamera", type=int, help="numer karty przechwytującej (zob. --lista-kamer)")
    zr.add_argument("--wideo", help="plik nagrania (demo / test)")
    zr.add_argument("--lista-kamer", action="store_true")
    ap.add_argument("--obszar", help="ekran: prostokąt OBRAZU GRY w punktach x,y,szer,wys (domyślnie cały ekran 16:9)")
    ap.add_argument("--glosnosc", type=float, default=1.0)
    ap.add_argument("--kolejka", type=int, default=2, help="ile kwestii może czekać na lektora")
    ap.add_argument("--bez-dzwieku", action="store_true", help="tylko wypisuj dopasowania")
    ap.add_argument("--szybko", action="store_true", help="wideo: licz w czasie nagrania, bez czekania")
    ap.add_argument("--podglad", action="store_true", help="zapisz klatkę z zaznaczonym obszarem napisów i wyjdź")
    ap.add_argument("--pokaz", action="store_true", help="wideo: pokaż nagranie w okienku (bez dźwięku gry)")
    ap.add_argument("--szczegoly", action="store_true", help="wypisuj też dopasowany wpis paczki (diagnostyka)")
    a = ap.parse_args()

    if a.lista_kamer:
        return lista_kamer()
    if not (a.ekran or a.kamera is not None or a.wideo):
        ap.error("wybierz źródło: --ekran, --kamera N albo --wideo PLIK")

    if not a.paczka:
        sys.exit("Nie ma paczki lektora: pobierz paczkę z gamereader.pl i wrzuć plik .zip do folderu paczki/")
    t = time.time()
    p = Paczka(a.paczka)
    frac = p.roi()[0]
    log(f"Paczka: {p.manifest.get('name', '?')} v{p.manifest.get('version', '?')} — {len(p.ids)} kwestii, "
        f"wczytana w {time.time() - t:.1f} s")
    for req in p.manifest.get("requirements", []):
        log(f"Wymóg paczki: {req.get('description', '').strip()}")

    if a.ekran:
        obszar = tuple(float(v) for v in a.obszar.split(",")) if a.obszar else None
        src = ZrodloEkran(frac, obszar)
    else:
        src = ZrodloWideo(a.wideo if a.wideo else a.kamera, frac, a.szybko)
    if src.podglad(PODGLAD):
        log(f"Podgląd obszaru napisów (czerwona ramka): {PODGLAD}")
        if a.podglad:
            subprocess.run(["open", PODGLAD])
            return
    else:
        log("! Nie udało się pobrać obrazu — przy --ekran nadaj Terminalowi zgodę: Ustawienia systemowe → "
            "Prywatność i ochrona → Nagrywanie ekranu i dźwięku z systemu.")
        if a.podglad:
            return

    m = Dopasowanie(p)
    glos = Glos(p, a.glosnosc, max(1, a.kolejka), a.bez_dzwieku)
    glos.start()
    if a.wideo and a.pokaz and not a.szybko:
        try:  # muted video window so the demo can be watched; timing restarts with it
            subprocess.Popen(["ffplay", "-loglevel", "quiet", "-an", "-autoexit", "-x", "960", "-y", "540",
                              "-window_title", "GTA VI - nagranie testowe", a.wideo])
            src.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            src.klatek, src.pozycja, src.t0 = 0, 0.0, None
            time.sleep(0.4)
        except FileNotFoundError:
            log("(brak ffplay — demo bez obrazu, tylko lektor)")
    log("Lektor gotowy. Ctrl+C kończy.")

    licz = czytaj(p, m, glos, src, szczegoly=a.szczegoly)
    log(f"Koniec. Przeczytane kwestie: {licz['glos']}, napisy bez dopasowania: {licz['bez']}.")
    if a.wideo and not a.bez_dzwieku:
        koniec = time.time() + 15
        while glos.zajety() and time.time() < koniec:  # let the last line finish
            time.sleep(0.1)
        time.sleep(0.3)
    if glos.stream:
        glos.stream.stop()
        glos.stream.close()


if __name__ == "__main__":
    main()
