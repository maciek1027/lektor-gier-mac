"""Reader tests on synthetic frames and synthetic mini-packs (no voice-pack text, no demo frames in git).

Run (macOS, from the repo folder):   .venv/bin/python -m unittest discover -s testy -v
Old reader as a positive control:    LEKTOR_MODUL=/path/to/old/lektor.py .venv/bin/python -m unittest discover -s testy

Every OCR test checks the recognised TEXT first and the match second — an OCR miss must not look like a matcher bug.
Optional checks on real packs run only when LEKTOR_TEST_PACZKI points at a folder with them (nothing is copied).
"""
import importlib.util
import json
import os
import tempfile
import unittest
import zipfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from rapidfuzz import fuzz

TU = os.path.dirname(os.path.abspath(__file__))
MODUL = os.environ.get("LEKTOR_MODUL") or os.path.join(os.path.dirname(TU), "lektor.py")
_spec = importlib.util.spec_from_file_location("lektor_pod_testem", MODUL)
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)

CZCIONKA = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def kadr(tekst, tlo="ciemne", kolor=(255, 255, 255), obrys=0, rozmiar=40, w=1100, h=110):
    """Synthetic subtitle strip: one line of text on a plain, bright or blurred-noise background."""
    rng = np.random.default_rng(7)
    if tlo == "ciemne":
        a = np.full((h, w, 3), 20, np.uint8)
    elif tlo == "jasne":
        a = np.full((h, w, 3), 225, np.uint8)
    else:  # "szum": a busy game-like scene
        a = cv2.GaussianBlur(rng.integers(60, 230, (h, w, 3), dtype=np.uint8), (9, 9), 0)
    im = Image.fromarray(a)
    ImageDraw.Draw(im).text((w // 2, h // 2), tekst, font=ImageFont.truetype(CZCIONKA, rozmiar), fill=kolor,
                            anchor="mm", stroke_width=obrys, stroke_fill=(0, 0, 0))
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def paczka(katalog, wpisy, preset=None, settings=None):
    """Minimal GameReader 2.0 pack with invented lines and dummy audio files."""
    sciezka = os.path.join(katalog, "paczka.zip")
    with zipfile.ZipFile(sciezka, "w") as z:
        z.writestr("manifest.json", json.dumps({"name": "Test", "version": "0"}))
        z.writestr("subtitles.json", json.dumps({"entries": wpisy}, ensure_ascii=False))
        z.writestr("preset.json", json.dumps(preset if preset is not None else {"rois": [{"rect": {
            "x": 0.3, "y": 0.8, "width": 0.4, "height": 0.15}}]}))
        z.writestr("settings.json", json.dumps(settings if settings is not None else {"ocr": {}}))
        for e in wpisy:
            z.writestr(f"audio/{e['id']}.opus", b"x")
    return L.Paczka(sciezka)


def przeczytaj(img, wymagaj_dokladnego=True):
    """OCR of a frame. macOS Accurate recognition sometimes fails system-wide for minutes (CRImageReaderError 1):
    then the test is SKIPPED as not measured — a Fast-level read must not pass or fail an Accurate test."""
    tekst = " ".join(L.ocr(img, [])).strip()
    if wymagaj_dokladnego and getattr(getattr(L, "OCR", None), "awaria", False):
        raise unittest.SkipTest(f"NIE ZMIERZYŁEM: tryb dokładny OCR macOS nie działa ({L.OCR.blad}) — powtórz później")
    return tekst


# Invented dialogue (not from any voice pack). Neighbours are close in wording so a wrong field would show.
DWUJEZYCZNE = [
    {"id": "a1", "text": "Where were you last night, Michael?",
     "text_en": "Where were you last night, Michael?", "text_pl": "Gdzie byłeś wczoraj w nocy, Michał?"},
    {"id": "a2", "text": "Nie mam pojęcia, o czym mówisz.",
     "text_en": "I have no idea what you are talking about.", "text_pl": "Nie mam pojęcia, o czym mówisz."},
    {"id": "a3", "text": "Get in the car, we are leaving now.",
     "text_en": "Get in the car, we are leaving now.", "text_pl": "Wsiadaj do auta, jedziemy teraz."},
    {"id": "a4", "text": "Gdzie byłeś wczoraj rano, Franklin?",
     "text_en": "Where were you yesterday morning, Franklin?", "text_pl": "Gdzie byłeś wczoraj rano, Franklin?"},
]


class TestDwujezyczne(unittest.TestCase):
    """(a) packs with text_pl + text_en: the line is found under Polish AND English game subtitles."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.p = paczka(cls.tmp.name, DWUJEZYCZNE)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _sprawdz(self, napis, oczekiwany_id):
        tekst = przeczytaj(kadr(napis))
        self.assertGreaterEqual(fuzz.ratio(L.norm(tekst), L.norm(napis)), 90, f"OCR przeczytał {tekst!r}")
        idx, score = L.Dopasowanie(self.p).znajdz(tekst)
        self.assertIsNotNone(idx, f"brak dopasowania dla {tekst!r} (najlepsza zgodność {score:.0f})")
        self.assertEqual(self.p.ids[idx], oczekiwany_id)

    def test_polski_napis_kwestia_z_angielskim_text(self):
        self._sprawdz("Gdzie byłeś wczoraj w nocy, Michał?", "a1")

    def test_polski_napis_druga_kwestia_z_angielskim_text(self):
        self._sprawdz("Wsiadaj do auta, jedziemy teraz.", "a3")

    def test_angielski_napis_kwestia_z_polskim_text(self):
        self._sprawdz("I have no idea what you are talking about.", "a2")

    def test_napis_w_jezyku_pola_text_dalej_trafia(self):
        self._sprawdz("Where were you last night, Michael?", "a1")
        self._sprawdz("Gdzie byłeś wczoraj rano, Franklin?", "a4")


class TestTylkoText(unittest.TestCase):
    """(a) regression guard: packs with only `text` keep the old search list (no copy, no extra variants)."""

    def test_polski_napis_trafia_jak_dotad(self):
        with tempfile.TemporaryDirectory() as t:
            p = paczka(t, [{"id": "b1", "text": "Lara, musisz znaleźć klucz do grobowca."},
                           {"id": "b2", "text": "Lara, musisz znaleźć wyjście z grobowca."}])
            napis = "Lara, musisz znaleźć klucz do grobowca."
            tekst = przeczytaj(kadr(napis))
            self.assertGreaterEqual(fuzz.ratio(L.norm(tekst), L.norm(napis)), 90, f"OCR przeczytał {tekst!r}")
            idx, _ = L.Dopasowanie(p).znajdz(tekst)
            self.assertEqual(p.ids[idx], "b1")
            self.assertIs(getattr(p, "szukaj", p.norm), p.norm)
            self.assertIsNone(getattr(p, "szukaj_idx", None))


class TestPrzetwarzanieKadru(unittest.TestCase):
    """(b) evidence for `preprocessing: [grayscale, invert]` (Tomb Raider I-III): the reader WITHOUT it reads
    frames like these. ASSUMPTION, not a game capture: light or dark subtitle text, with and without an outline,
    over dark, bright and busy backgrounds. If a real capture fails while these pass, add preprocessing then."""

    NAPIS = "Lara, musisz znaleźć klucz do grobowca."

    def test_kadry_czytane_bez_przetwarzania(self):
        warianty = [("ciemne", (255, 255, 255), 0), ("szum", (255, 255, 255), 2), ("szum", (255, 255, 255), 0),
                    ("jasne", (255, 255, 255), 2), ("jasne", (255, 255, 255), 0), ("jasne", (25, 25, 25), 0),
                    ("szum", (230, 210, 120), 0)]
        for tlo, kolor, obrys in warianty:
            with self.subTest(tlo=tlo, kolor=kolor, obrys=obrys):
                tekst = przeczytaj(kadr(self.NAPIS, tlo=tlo, kolor=kolor, obrys=obrys))
                self.assertGreaterEqual(fuzz.ratio(L.norm(tekst), L.norm(self.NAPIS)), 90, f"OCR: {tekst!r}")


class _Zrodlo:
    """Picture source for czytaj(): the given frames, then end."""

    def __init__(self, klatki):
        self.klatki, self.koniec, self.szybko = list(klatki), False, True

    def obszar(self, krok):
        if not self.klatki:
            self.koniec = True
            return None
        return self.klatki.pop(0)


class TestAwariaOCR(unittest.TestCase):
    """macOS Accurate text recognition failing (CRImageReaderError 1, 2026-11-22) must not silence the reader:
    the error is reported, the line is read with the Fast level, Accurate is retried after a pause."""

    NAPIS = "Gdzie byłeś wczoraj w nocy, Michał?"

    def setUp(self):
        if not hasattr(L, "_rozpoznaj"):
            self.skipTest("stary czytnik: brak _rozpoznaj (kontrola: kopia czytnika bez przejścia na tryb szybki)")
        self.prawdziwe = L._rozpoznaj
        self.wolania = []

        def awaria_dokladnego(data, dokladny):
            self.wolania.append(dokladny)
            return (None, "TextRecognition.CRImageReaderError 1") if dokladny else self.prawdziwe(data, False)

        L._rozpoznaj = awaria_dokladnego
        L.OCR = L.StanOCR()
        L.OCR.PRZERWA = 3600  # the pause never runs out on its own during the test (no wall-clock race)

    def tearDown(self):
        if hasattr(self, "prawdziwe"):
            L._rozpoznaj = self.prawdziwe
            L.OCR = L.StanOCR()

    def test_tekst_czytany_trybem_szybkim_i_blad_zgloszony(self):
        tekst = przeczytaj(kadr(self.NAPIS), wymagaj_dokladnego=False)
        self.assertGreaterEqual(fuzz.ratio(L.norm(tekst), L.norm(self.NAPIS)), 90, f"OCR: {tekst!r}")
        self.assertEqual((L.OCR.bledy, L.OCR.szybkie, L.OCR.awaria), (1, 1, True))
        przeczytaj(kadr(self.NAPIS), False)  # within the pause: Fast only, Accurate not retried
        self.assertEqual(self.wolania, [True, False, False])
        L.OCR.przerwa_do = 0  # pause over: Accurate is tried again
        przeczytaj(kadr(self.NAPIS), False)
        self.assertEqual(self.wolania[-2:], [True, False])

    def test_petla_czytania_mowi_o_awarii_i_dopasowuje(self):
        with tempfile.TemporaryDirectory() as t:
            p = paczka(t, DWUJEZYCZNE)
            info, kwestie = [], []
            glos = type("Glos", (), {"dodaj": lambda self, idx: None})()
            licz = L.czytaj(p, L.Dopasowanie(p), glos, _Zrodlo([kadr(self.NAPIS)]), na_info=info.append,
                            na_kwestie=lambda tekst, idx, s: kwestie.append(p.ids[idx]))
        self.assertTrue(any(i.startswith("! Rozpoznawanie tekstu macOS") for i in info), info)
        self.assertEqual(kwestie, ["a1"])
        self.assertEqual((licz["ocr_bledy"], licz["ocr_szybkie"]), (1, 1))


def roi(**pola):
    r = {"name": "subtitle-bottom", "rect": {"x": 0.3, "y": 0.8, "width": 0.4, "height": 0.15},
         "geometry": {"mode": "anchored", "reference": {"width": 2560, "height": 1440}}, "preprocessing": [],
         "signal_detection": None, "center_line_filter": None, "strip_speaker_prefix": False, "multi_line": None}
    r.update(pola)
    return r


SYGNAL = {"signals": [{"color": [200, 200, 200], "tolerance": 10}], "combinator": "any", "min_signal_pixels": 20}


class TestNieobslugiwane(unittest.TestCase):
    """(d) the reader reports pack fields it does not apply — by ACTIVE value, not by key presence."""

    def _pola(self, preset, settings=None):
        with tempfile.TemporaryDirectory() as t:
            p = paczka(t, [{"id": "c1", "text": "Kwestia testowa numer jeden."}], preset, settings)
            return p.nieobslugiwane()

    def test_preset_w_ksztalcie_tomb_raider(self):
        pola = self._pola({"rois": [roi(preprocessing=[{"kind": "grayscale"}, {"kind": "invert"}],
                                        geometry={"mode": "anchored", "reference": {"width": 2556, "height": 1434}})]},
                          {"ocr": {"capture_interval": 250, "adaptive_capture": False, "match_strategy": "wholetext"}})
        self.assertEqual([n["pole"] for n in pola], ["preprocessing"])
        self.assertEqual(pola[0]["opis"], "grayscale, invert")
        self.assertTrue(pola[0]["czytanie"])

    def test_preset_w_ksztalcie_re2(self):
        pola = self._pola({"rois": [roi(signal_detection=dict(SYGNAL, combinator="all"),
                                        center_line_filter={"bands": [{"start_ratio": 0.4, "width_ratio": 0.2,
                                                                       "enabled": True}]})]})
        self.assertEqual([(n["pole"], n["czytanie"]) for n in pola], [("signal_detection", False)])

    def test_preset_w_ksztalcie_gta6_paczka_czytajaca_14_z_14(self):
        # known-good pack shape: two unapplied fields, yet the demo reads 14/14 — the catalogue needs `czytanie`
        pola = self._pola({"rois": [roi(signal_detection=SYGNAL, strip_speaker_prefix=True,
                                        multi_line={"coalesce_frames": 0, "forget_frames": 5})]})
        self.assertEqual([(n["pole"], n["czytanie"]) for n in pola],
                         [("signal_detection", False), ("strip_speaker_prefix", True)])

    def test_puste_wartosci_nie_sa_zglaszane(self):
        self.assertEqual(self._pola({"rois": [roi()]}, {"ocr": {"match_strategy": "wholetext"}}), [])
        self.assertEqual(self._pola({"rois": [roi()]}, {"ocr": {"match_strategy": ""}}), [])
        self.assertEqual(self._pola({}), [])

    def test_wiele_obszarow_nieznane_pola_i_strategia(self):
        pola = self._pola({"rois": [roi(), roi()], "cos_nowego": {"a": 1}},
                          {"ocr": {"match_strategy": "fuzzy-lines", "nowe_ustawienie": True}})
        self.assertEqual(sorted(n["pole"] for n in pola),
                         ["cos_nowego", "ocr.match_strategy", "ocr.nowe_ustawienie", "rois"])

    def test_obraz_nie_16_9_i_dlugie_laczenie_linii(self):
        pola = self._pola({"rois": [roi(geometry={"mode": "anchored", "reference": {"width": 1440, "height": 1080}},
                                        multi_line={"coalesce_frames": 3, "forget_frames": 5})]})
        self.assertEqual([n["pole"] for n in pola], ["geometry", "multi_line"])


@unittest.skipUnless(os.environ.get("LEKTOR_TEST_PACZKI"), "LEKTOR_TEST_PACZKI nie ustawione — bez prawdziwych paczek")
class TestPrawdziwePaczki(unittest.TestCase):
    """Optional: the real packs (read in place, nothing copied). File names as published by gamereader.pl."""

    def _p(self, nazwa):
        sciezka = os.path.join(os.environ["LEKTOR_TEST_PACZKI"], nazwa)
        if not os.path.exists(sciezka):
            self.skipTest(f"brak {nazwa}")
        return L.Paczka(sciezka)

    def test_tomb_raider(self):
        p = self._p("tomb-raider-i-iii-remastered.zip")
        self.assertEqual([n["pole"] for n in p.nieobslugiwane()], ["preprocessing"])
        self.assertEqual(len(p.preset["rois"]), 1)

    def test_re2(self):
        p = self._p("resident-evil-2-remake.zip")
        self.assertEqual([n["pole"] for n in p.nieobslugiwane()], ["signal_detection"])
        self.assertEqual(len(p.preset["rois"]), 1)

    def test_gta5_dwujezyczna(self):
        p = self._p("grand-theft-auto-v2.zip")
        self.assertEqual([n["pole"] for n in p.nieobslugiwane()], ["signal_detection"])
        self.assertIsNotNone(p.szukaj_idx)
        self.assertGreater(len(p.szukaj), len(p.ids))


class TestLokalne(unittest.TestCase):
    """lokalne.env: only the three path variables, the real environment wins."""

    KLUCZE = ("LEKTOR_PACZKI", "LEKTOR_PACZKA", "LEKTOR_DEMO", "LEKTOR_PORT", "LEKTOR_TEST_INNA_ZMIENNA")

    def setUp(self):
        self.stare = {k: os.environ.pop(k, None) for k in self.KLUCZE}

    def tearDown(self):
        for k, v in self.stare.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def _wczytaj(self, tresc, kodowanie="utf-8"):
        with tempfile.TemporaryDirectory() as t:
            sciezka = os.path.join(t, "lokalne.env")
            with open(sciezka, "w", encoding=kodowanie, newline="") as f:
                f.write(tresc)
            L.wczytaj_lokalne(sciezka)

    def test_tylko_sciezki_i_srodowisko_wygrywa(self):
        os.environ["LEKTOR_DEMO"] = "/z/srodowiska.mp4"
        self._wczytaj('# komentarz\nLEKTOR_PACZKI="/dysk/paczki"\nLEKTOR_PACZKA=/dysk/paczki/gra.zip\n'
                      "LEKTOR_DEMO=/z/pliku.mp4\nLEKTOR_PORT=9999\nLEKTOR_TEST_INNA_ZMIENNA=1\n")
        self.assertEqual(os.environ.get("LEKTOR_PACZKI"), "/dysk/paczki")
        self.assertEqual(os.environ.get("LEKTOR_PACZKA"), "/dysk/paczki/gra.zip")
        self.assertEqual(os.environ.get("LEKTOR_DEMO"), "/z/srodowiska.mp4")
        self.assertIsNone(os.environ.get("LEKTOR_PORT"))
        self.assertIsNone(os.environ.get("LEKTOR_TEST_INNA_ZMIENNA"))
        L.wczytaj_lokalne(os.path.join(TU, "nie-ma-takiego-pliku.env"))  # missing file = no-op

    def test_bom_crlf_komentarz_za_wartoscia_i_cudzyslowy(self):
        self._wczytaj('LEKTOR_PACZKI=/Volumes/Dysk/paczki # dysk zewnętrzny\r\n'
                      'LEKTOR_PACZKA="/Volumes/Mój Dysk/gra #1.zip" # paczka\r\n'
                      "LEKTOR_DEMO='/Volumes/Dysk/film.mp4'\r\n", kodowanie="utf-8-sig")
        self.assertEqual(os.environ.get("LEKTOR_PACZKI"), "/Volumes/Dysk/paczki")
        self.assertEqual(os.environ.get("LEKTOR_PACZKA"), "/Volumes/Mój Dysk/gra #1.zip")
        self.assertEqual(os.environ.get("LEKTOR_DEMO"), "/Volumes/Dysk/film.mp4")


if __name__ == "__main__":
    unittest.main()
