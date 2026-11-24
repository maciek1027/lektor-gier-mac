"""Catalogue, game change and server tests on invented mini-packs (no voice-pack content in git).

Run (macOS, from the repo folder):   .venv/bin/python -m unittest discover -s testy -v
Positive controls (each guard removed in a COPY of the code, the named test must fail):  .venv/bin/python testy/kontrola.py

What is covered: an old settings file starts on GTA VI with the same pilot key · the one lock (change / start /
stop / sample / reload) refuses instead of queueing · a game change answers a waiting long-poll at once and the phone
gets no line and no clip of the old game · missing disk ≠ empty folder ≠ broken pack · tile states from test
records, not from the bare list of unhandled fields · cover theme contrast ≥ 4.5:1 · new routes need the pilot key
from the network · temporary folders of the reader are removed on close.
Optional checks on the real packs run only when LEKTOR_TEST_PACZKI points at a folder with them.
"""
import http.client
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import zipfile

TU = os.path.dirname(os.path.abspath(__file__))
KOD = os.path.dirname(TU)
TMP = tempfile.mkdtemp(prefix="lektor-test-katalog-")
_ENV = {k: os.environ.get(k) for k in ("LEKTOR_PACZKA", "LEKTOR_DEMO", "LEKTOR_PACZKI", "LEKTOR_USTAWIENIA",
                                       "LEKTOR_PAMIEC", "LEKTOR_PORT")}
# lokalne.env must not leak the operator's disk into the tests (the server would load GTA VI from WDBlue)
os.environ.update(LEKTOR_PACZKA="", LEKTOR_DEMO="", LEKTOR_PACZKI=os.path.join(TMP, "brak-paczek"),
                  LEKTOR_USTAWIENIA=os.path.join(TMP, "ustawienia-importu.json"), LEKTOR_PAMIEC=os.path.join(TMP, "pamiec"))
sys.path.insert(0, KOD)
_spec = importlib.util.spec_from_file_location("serwer_pod_testem", os.path.join(KOD, "serwer_panelu.py"))
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
L = S.L
PRAWDZIWE = os.environ.get("LEKTOR_TEST_PACZKI")

PRESET_GTA6 = {"rois": [{"rect": {"x": 0.25, "y": 0.8, "width": 0.5, "height": 0.12},
                         "signal_detection": {"combinator": "all", "colors": [[255, 255, 255]]},
                         "strip_speaker_prefix": True}]}
PRESET_TR = {"rois": [{"rect": {"x": 0.2, "y": 0.8, "width": 0.6, "height": 0.15},
                       "preprocessing": [{"kind": "grayscale"}, {"kind": "invert"}]}]}
PRESET_RE2 = {"rois": [{"rect": {"x": 0.2, "y": 0.8, "width": 0.6, "height": 0.15},
                        "signal_detection": {"combinator": "all", "colors": [[216, 215, 196]]}}]}


def tearDownModule():
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)
    for k, v in _ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def okladka_jpg(kolor=(200, 40, 90), drugi=None):
    from PIL import Image
    im = Image.new("RGB", (92, 43), kolor)
    if drugi:
        im.paste(Image.new("RGB", (46, 43), drugi), (46, 0))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=95)
    return b.getvalue()


def zrob_paczke(folder, plik, nazwa, id_paczki=None, wersja="1.0.0", preset=None, okladka=True, znacznik=None,
                wymagania=None, kwestie=4, opis="Opis testowy."):
    """Invented GameReader 2.0 pack: lines 'Kwestia N gry <name>', audio bytes b'AUDIO-<znacznik>-<N>'."""
    os.makedirs(folder, exist_ok=True)
    znacznik = znacznik or nazwa
    sciezka = os.path.join(folder, plik)
    man = {"name": nazwa, "id": id_paczki or ("id-" + nazwa), "version": wersja, "author": "test", "description": opis}
    if wymagania:
        man["requirements"] = [{"name": "INFO", "description": w} for w in wymagania]
    wpisy = [{"id": f"k{i}", "text": f"Kwestia numer {i} w grze {nazwa}, dość długa."} for i in range(kwestie)]
    with zipfile.ZipFile(sciezka, "w") as z:
        z.writestr("manifest.json", json.dumps(man, ensure_ascii=False))
        z.writestr("subtitles.json", json.dumps({"entries": wpisy}, ensure_ascii=False))
        z.writestr("preset.json", json.dumps(preset if preset is not None else PRESET_RE2))
        z.writestr("settings.json", json.dumps({"ocr": {}}))
        for i, e in enumerate(wpisy):
            z.writestr(f"audio/{e['id']}.opus", f"AUDIO-{znacznik}-{i}".encode())
        if okladka:
            z.writestr("images/cover.jpg", okladka_jpg())
    return sciezka


def nowy_katalog(nazwa):
    k = os.path.join(TMP, nazwa + "-" + str(time.time_ns()))
    os.makedirs(k)
    return k


def zapisz_ustawienia(katalog, dane):
    p = os.path.join(katalog, "ustawienia.json")
    with open(p, "w") as f:
        json.dump(dane, f, ensure_ascii=False)
    return p


def stan(katalog, folder, dowody=None, ustawienia=None):
    """A Stan on temporary folders, started like main() does, waited until the background load ends."""
    plik_dowodow = os.path.join(katalog, "sprawdzone.json")
    with open(plik_dowodow, "w") as f:
        json.dump(dowody or {}, f)
    st = S.Stan(ustawienia=ustawienia or os.path.join(katalog, "ustawienia.json"), folder=folder,
                pamiec=os.path.join(katalog, "pamiec"), sprawdzone=plik_dowodow)
    st.uruchom()
    koniec = time.time() + 15  # uruchom() takes the lock before its thread starts: locked() is true from the outset
    while st.sterowanie.locked() and time.time() < koniec:
        time.sleep(0.02)
    if st.sterowanie.locked():
        raise AssertionError("the background load did not end in 15 s")
    return st


KLUCZ = "testowy" + "Klucz" + "123456"  # invented, built at run time


class TestUstawienia(unittest.TestCase):
    def test_stary_plik_bez_gry_wstaje_na_gta6_z_tym_samym_kluczem(self):
        k = nowy_katalog("migracja")
        f = os.path.join(k, "paczki")
        zrob_paczke(f, "grand-theft-auto-v2.zip", "Grand Theft Auto V")  # sorts BEFORE the GTA VI file
        zrob_paczke(f, "grand-theft-auto-vi-v4.zip", "Grand Theft Auto VI")
        stary = {"klucz": KLUCZ, "profil": "iphone", "urzadzenie": "Głośnik X", "glosnosc": 0.7,
                 "glosnosc_iphone": 1.5, "zrodlo": "kamera", "kamera": 2}
        p = zapisz_ustawienia(k, stary)
        st = stan(k, f, ustawienia=p)
        self.assertEqual(st.gra, "grand-theft-auto-vi-v4")
        self.assertEqual(st.p.manifest["name"], "Grand Theft Auto VI")
        self.assertEqual(st.klucz, KLUCZ)
        for pole in ("profil", "urzadzenie", "glosnosc", "glosnosc_iphone", "zrodlo", "kamera"):
            self.assertEqual(st.ust[pole], stary[pole], pole)
        st.glosnosc(0.8)  # any later save keeps the key and remembers the game
        with open(p) as fh:
            zapis = json.load(fh)
        self.assertEqual((zapis["klucz"], zapis["gra"]), (KLUCZ, "grand-theft-auto-vi-v4"))

    def test_zmienna_paczki_wskazuje_gre_starej_instalacji(self):
        k = nowy_katalog("zmienna")
        f = os.path.join(k, "paczki")
        zrob_paczke(f, "grand-theft-auto-vi-v4.zip", "Grand Theft Auto VI")
        re2 = zrob_paczke(f, "resident-evil-2-remake.zip", "Resident Evil 2 Remake")
        os.environ["LEKTOR_PACZKA"] = re2
        try:
            st = stan(k, f)
        finally:
            os.environ["LEKTOR_PACZKA"] = ""
        self.assertEqual(st.gra, "resident-evil-2-remake")

    def test_zapisana_gra_wygrywa_ze_zmienna(self):
        k = nowy_katalog("zapisana")
        f = os.path.join(k, "paczki")
        gta = zrob_paczke(f, "grand-theft-auto-vi-v4.zip", "Grand Theft Auto VI")
        zrob_paczke(f, "tomb.zip", "Tomb Raider")
        p = zapisz_ustawienia(k, {"klucz": KLUCZ, "gra": "tomb"})
        os.environ["LEKTOR_PACZKA"] = gta
        try:
            st = stan(k, f, ustawienia=p)
        finally:
            os.environ["LEKTOR_PACZKA"] = ""
        self.assertEqual(st.gra, "tomb")

    def test_zly_plik_ustawien_nie_zatrzymuje_serwera(self):
        k = nowy_katalog("zly")
        f = os.path.join(k, "paczki")
        zrob_paczke(f, "grand-theft-auto-vi-v4.zip", "Grand Theft Auto VI")
        for tresc in ('{"glosnosc": "glosno", "profil": "x", "kamera": true, "gra": "../../etc", "klucz": "krotki",'
                      ' "wyglad": 5, "urzadzenie": 7}', "[1, 2]", "{nie json"):
            with open(os.path.join(k, "ustawienia.json"), "w") as fh:
                fh.write(tresc)
            st = stan(k, f)
            self.assertEqual({p: st.ust[p] for p in ("glosnosc", "profil", "kamera", "wyglad", "urzadzenie")},
                             {"glosnosc": 1.0, "profil": "tv_mac", "kamera": 0, "wyglad": "motyw", "urzadzenie": ""})
            self.assertEqual(st.gra, "grand-theft-auto-vi-v4")  # the bad "gra" was dropped, the default game loads
            self.assertRegex(st.klucz, r"^[A-Za-z0-9_-]{12,64}$")

    def test_zapisana_gra_bez_dysku_zostaje_wybrana(self):
        k = nowy_katalog("bezdysku")
        p = zapisz_ustawienia(k, {"klucz": KLUCZ, "gra": "tomb"})
        st = stan(k, "/Volumes/NieMaTakiegoDysku-lektor-test/paczki", ustawienia=p)
        self.assertIsNone(st.p)
        self.assertEqual(st.ust["gra"], "tomb")
        self.assertIn("Podłącz dysk", st.paczka_blad)
        st.zapisz_ustawienia()
        with open(p) as fh:
            self.assertEqual(json.load(fh)["gra"], "tomb")


class _Serwer:
    """The real HTTP handler on a free port of the loopback only (the test key is public — never on the Wi-Fi),
    with this test's Stan as the server state. A 'network' request is made with a non-loopback Host header."""

    def __init__(self, st):
        S.STAN = st
        self.srv = S.Serwer(("::1", 0), S.Obsluga)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def zapytaj(self, metoda, sciezka, host="127.0.0.1", klucz=None, body=None, timeout=30):
        c = http.client.HTTPConnection("::1", self.port, timeout=timeout)
        h = {"Host": f"{host}:{self.port}"}
        if klucz:
            h["X-Lektor-Klucz"] = klucz
        if metoda == "POST":
            h.update({"X-Lektor": "1", "Content-Type": "application/json"})
        c.request(metoda, sciezka, body=json.dumps(body or {}) if metoda == "POST" else None, headers=h)
        r = c.getresponse()
        dane = r.read()
        c.close()
        return r.status, dane

    def json(self, *a, **kw):
        kod, dane = self.zapytaj(*a, **kw)
        return kod, json.loads(dane)

    def zamknij(self):
        self.srv.shutdown()
        self.srv.server_close()


class TestZmianaGry(unittest.TestCase):
    def setUp(self):
        k = nowy_katalog("zmiana")
        f = os.path.join(k, "paczki")
        zrob_paczke(f, "aaa.zip", "Gra A", znacznik="A")
        zrob_paczke(f, "bbb.zip", "Gra B", znacznik="B")
        self.st = stan(k, f, ustawienia=zapisz_ustawienia(k, {"klucz": KLUCZ, "gra": "aaa"}))
        self.assertEqual(self.st.gra, "aaa")
        self.srv = _Serwer(self.st)

    def tearDown(self):
        self.srv.zamknij()

    def zmien(self, g):
        with self.st.sterowanie:
            return self.st._zmien_gre(g)

    def test_wiszacy_long_poll_wraca_od_razu_bez_kwestii_starej_gry(self):
        _, r0 = self.srv.json("GET", "/api/telefon/kwestie?od=-1&c=tel1")
        stara = r0["g"]
        wynik = {}

        def telefon():
            t = time.time()
            wynik["kod"], wynik["r"] = self.srv.json("GET", f"/api/telefon/kwestie?od={r0['nr']}&f=opus&c=tel1")
            wynik["s"] = time.time() - t

        w = threading.Thread(target=telefon)
        w.start()
        time.sleep(0.4)  # the poll is waiting now
        self.assertTrue(self.zmien("bbb")["ok"])
        w.join(25)
        self.assertLess(wynik["s"], 3, "a waiting poll must be answered by the game change, not after 20 s")
        self.assertNotEqual(wynik["r"]["g"], stara)
        self.assertEqual([e for e in wynik["r"]["zdarzenia"] if e["typ"] == "kwestia"], [])

    def test_kwestia_starej_gry_nie_trafia_do_telefonu_po_zmianie(self):
        _, r0 = self.srv.json("GET", "/api/telefon/kwestie?od=-1&c=tel2")
        self.st.nadaj({"typ": "kwestia", "idx": 1})  # matched in game A, the phone has not fetched it yet
        self.assertTrue(self.zmien("bbb")["ok"])
        _, r = self.srv.json("GET", f"/api/telefon/kwestie?od={r0['nr']}&f=opus&c=tel2")
        self.assertEqual([e for e in r["zdarzenia"] if e["typ"] == "kwestia"], [])
        self.assertFalse(self.st.czeka_na_telefon(), "an old unplayed line must not hold up the new game's voice")

    def test_spozniona_kwestia_ze_starym_numerem_jest_odfiltrowana(self):
        _, r0 = self.srv.json("GET", "/api/telefon/kwestie?od=-1&c=tel3")
        stara = r0["g"]
        self.assertTrue(self.zmien("bbb")["ok"])
        with self.st.cv:  # a late event stamped with the OLD session (e.g. a reader that did not stop in time)
            self.st.nr += 1
            self.st.zdarzenia.append({"typ": "kwestia", "idx": 1, "n": self.st.nr, "t": time.time(), "g": stara})
        _, r = self.srv.json("GET", f"/api/telefon/kwestie?od={r0['nr']}&f=opus&c=tel3")
        self.assertEqual([e for e in r["zdarzenia"] if e["typ"] == "kwestia"], [])

    def test_nagranie_ze_starym_numerem_sesji_nie_daje_dzwieku_nowej_gry(self):
        stara = self.st.sesja_gry
        self.assertTrue(self.zmien("bbb")["ok"])
        kod, dane = self.srv.zapytaj("GET", f"/api/telefon/dzwiek/1?f=opus&g={stara}")
        self.assertEqual(kod, 404)
        kod, dane = self.srv.zapytaj("GET", f"/api/telefon/dzwiek/1?f=opus&g={self.st.sesja_gry}")
        self.assertEqual((kod, dane), (200, b"AUDIO-B-1"))

    def test_nagranie_bez_numeru_sesji_nie_jest_wydawane(self):
        self.assertTrue(self.zmien("bbb")["ok"])  # an old phone page (before the update) asks without g
        kod, _ = self.srv.zapytaj("GET", "/api/telefon/dzwiek/1?f=opus")
        self.assertEqual(kod, 404)

    def test_kwestia_biezacej_gry_ma_nagranie_tej_gry(self):
        self.assertTrue(self.zmien("bbb")["ok"])
        _, r0 = self.srv.json("GET", "/api/telefon/kwestie?od=-1&c=tel4")
        self.st.nadaj({"typ": "kwestia", "idx": 2})
        _, r = self.srv.json("GET", f"/api/telefon/kwestie?od={r0['nr']}&f=opus&c=tel4")
        kw = [e for e in r["zdarzenia"] if e["typ"] == "kwestia"]
        self.assertEqual(len(kw), 1)
        import base64
        self.assertEqual(base64.b64decode(kw[0]["audio"]), b"AUDIO-B-2")
        self.assertEqual(kw[0]["g"], r["g"])


class TestBlokada(unittest.TestCase):
    def setUp(self):
        k = nowy_katalog("blokada")
        f = os.path.join(k, "paczki")
        zrob_paczke(f, "aaa.zip", "Gra A", znacznik="A")
        zrob_paczke(f, "bbb.zip", "Gra B", znacznik="B")
        self.st = stan(k, f, ustawienia=zapisz_ustawienia(k, {"klucz": KLUCZ, "gra": "aaa"}))

    def w_watku(self, fn):
        out = {}
        w = threading.Thread(target=lambda: out.setdefault("r", fn()), daemon=True)
        w.start()
        w.join(2)
        self.assertFalse(w.is_alive(), "a busy lock must give a refusal at once, not a queue")
        return out["r"]

    def test_zajeta_blokada_to_odmowa_z_komunikatem(self):
        self.st.sterowanie.acquire()  # a game change in progress
        try:
            for fn in (lambda: self.st.start("ekran", "iphone", "", None, 0, False, "bbb"),
                       lambda: self.st.probka("bbb", "iphone"), lambda: self.st.stop(), lambda: self.st.odswiez()):
                r = self.w_watku(fn)
                self.assertEqual((r["ok"], r["blad"]), (False, S.ZAJETY))
        finally:
            self.st.sterowanie.release()
        self.assertEqual(self.st.gra, "aaa")

    def test_probka_z_iphonea_bez_dzwieku_nie_zmienia_gry(self):
        r = self.st.probka("bbb", "iphone")  # no phone has its sound on
        self.assertFalse(r["ok"])
        self.assertIn("iPhone", r["blad"])
        self.assertEqual(self.st.gra, "aaa")

    def uparta_sesja(self, stoj):
        uparty = threading.Thread(target=lambda: stoj.wait(8), daemon=True)  # ignores its stop flag
        uparty.start()

        class Glos:
            def zamknij(self):
                pass

        self.st.sesja = {"stop": threading.Event(), "watek": uparty, "glos": Glos(), "okno": None,
                         "zrodlo": "ekran", "profil": "tv_mac", "start": time.time()}
        return self.st.sesja

    def test_graj_przy_tej_samej_grze_nie_doklada_drugiego_czytnika(self):
        stoj = threading.Event()
        stara = self.uparta_sesja(stoj)
        r = self.st.start("demo", "iphone", "", None, 0, False, "aaa")
        stoj.set()
        self.assertFalse(r["ok"])
        self.assertIn("nie zatrzymał", r["blad"])  # refused for THIS reason (not e.g. the missing demo video)
        self.assertIs(self.st.sesja, stara)

    def test_czytnik_ktory_nie_staje_blokuje_zmiane_gry(self):
        stoj = threading.Event()
        uparty = threading.Thread(target=lambda: stoj.wait(8), daemon=True)  # ignores its stop flag
        uparty.start()

        class Glos:
            def zamknij(self):
                pass

        self.st.sesja = {"stop": threading.Event(), "watek": uparty, "glos": Glos(), "okno": None,
                         "zrodlo": "ekran", "profil": "tv_mac", "start": time.time()}
        p = self.st.p
        with self.st.sterowanie:
            r = self.st._zmien_gre("bbb")
        stoj.set()
        self.assertFalse(r["ok"])
        self.assertEqual((self.st.gra, self.st.p), ("aaa", p))


class TestKatalog(unittest.TestCase):
    def kat(self, folder, dowody=None):
        k = nowy_katalog("kat")
        plik = os.path.join(k, "sprawdzone.json")
        with open(plik, "w") as f:
            json.dump(dowody or {}, f)
        kat = S.Katalog(folder, os.path.join(k, "pamiec"), plik)
        kat.skanuj()
        return kat

    def test_brak_dysku_to_komunikat_nie_pusty_katalog(self):
        kat = self.kat("/Volumes/NieMaTakiegoDysku-lektor-test/paczki")
        pub = kat.publiczne()
        self.assertEqual((pub["stan"], pub["gry"]), ("brak_dysku", []))
        self.assertIn("Podłącz dysk", pub["komunikat"])

    def test_pusty_folder_i_brak_folderu_to_inne_stany(self):
        self.assertEqual(self.kat(nowy_katalog("pusty")).publiczne()["stan"], "pusty")
        self.assertEqual(self.kat(os.path.join(TMP, "nie-ma-takiego-folderu")).publiczne()["stan"], "brak_folderu")

    def test_pliki_maca_i_czesci_pobran_pominiete(self):
        f = nowy_katalog("smieci")
        with open(os.path.join(f, "._gra.zip"), "wb") as fh:
            fh.write(b"\0" * 4096)
        with open(os.path.join(f, "gra2.zip.czesc"), "wb") as fh:
            fh.write(b"czesc")
        zrob_paczke(f, "gra.zip", "Gra")
        self.assertEqual([g["id"] for g in self.kat(f).publiczne()["gry"]], ["gra"])

    def test_uszkodzona_paczka_ma_stan_blad(self):
        f = nowy_katalog("uszkodzona")
        with open(os.path.join(f, "zepsuta.zip"), "wb") as fh:
            fh.write(b"to nie jest zip")
        with zipfile.ZipFile(os.path.join(f, "bez-napisow.zip"), "w") as z:
            z.writestr("manifest.json", "{}")
        gry = {g["id"]: g for g in self.kat(f).publiczne()["gry"]}
        for i in ("zepsuta", "bez-napisow"):
            self.assertEqual(gry[i]["stan"], "blad", i)
            self.assertIn("uszkodzona", gry[i]["stan_opis"])

    def test_stany_kafelkow_z_dowodu_proby_nie_z_samej_listy_pol(self):
        f = nowy_katalog("stany")
        zrob_paczke(f, "gta6.zip", "Szósta", id_paczki="ID6", wersja="1.0.2", preset=PRESET_GTA6)
        zrob_paczke(f, "gta6-bez-proby.zip", "Szósta bez próby", id_paczki="ID6b", wersja="1.0.2", preset=PRESET_GTA6)
        zrob_paczke(f, "gta6-inna-wersja.zip", "Szósta nowsza", id_paczki="ID6", wersja="1.0.3", preset=PRESET_RE2)
        zrob_paczke(f, "tomb.zip", "Tomb", id_paczki="IDT", preset=PRESET_TR)
        zrob_paczke(f, "re2.zip", "RE2", id_paczki="IDR", preset=PRESET_RE2)
        zrob_paczke(f, "bez-obszaru.zip", "Bez obszaru", preset={})
        dowody = {"ID6": {"wersja": "1.0.2", "rodzaj": "na_zywo", "data": "2026-11-21", "jak": "Xbox"},
                  "IDT": {"wersja": "1.0.0", "rodzaj": "nagranie", "data": "2026-11-22", "jak": "YouTube"}}
        gry = {g["id"]: g["stan"] for g in self.kat(f, dowody).publiczne()["gry"]}
        self.assertEqual(gry, {"gta6": "sprawdzona",          # two unhandled fields, still the live-tested pack
                               "gta6-bez-proby": "ryzyko",    # same fields, no test record: may lose lines
                               "gta6-inna-wersja": "niesprawdzona",  # the record is for another version
                               "tomb": "nagranie", "re2": "niesprawdzona", "bez-obszaru": "nie_na_macu"})

    @unittest.skipUnless(PRAWDZIWE, "LEKTOR_TEST_PACZKI not set")
    def test_prawdziwe_paczki_stany_i_kontrast(self):
        kat = self.kat(PRAWDZIWE, json.load(open(os.path.join(KOD, "sprawdzone.json"), encoding="utf-8")))
        gry = {g["nazwa"]: g for g in kat.publiczne()["gry"]}
        self.assertEqual({n: g["stan"] for n, g in gry.items()},
                         {"Grand Theft Auto VI": "sprawdzona", "Grand Theft Auto V": "nagranie",
                          "Tomb Raider I-III Remastered": "nagranie", "Resident Evil 2 Remake": "nagranie"})
        for n, g in gry.items():
            self.assertGreaterEqual(g["motyw"]["kontrast"], 4.5, n)
            self.assertGreaterEqual(g["motyw"]["kontrast_tla"], 4.5, n)
        self.assertEqual(gry["Grand Theft Auto VI"]["wymagania"][0][:20], "Napisy mają być  kon")
        self.assertEqual([n for n, g in gry.items() if g["wymagania"]], ["Grand Theft Auto VI"])

    def test_pamiec_podreczna_uniewazniana_rozmiarem_i_data(self):
        f = nowy_katalog("pamiec")
        zrob_paczke(f, "gra.zip", "Pierwsza nazwa")
        kat = self.kat(f)
        self.assertEqual(kat.publiczne()["gry"][0]["nazwa"], "Pierwsza nazwa")
        time.sleep(0.01)
        zrob_paczke(f, "gra.zip", "Druga nazwa po aktualizacji paczki", kwestie=7)
        kat.skanuj()
        self.assertEqual(kat.publiczne()["gry"][0]["nazwa"], "Druga nazwa po aktualizacji paczki")

    def test_demo_tylko_dla_wlasnej_gry(self):
        f = nowy_katalog("demo")
        zrob_paczke(f, "grand-theft-auto-vi-v4.zip", "Grand Theft Auto VI")
        zrob_paczke(f, "re2.zip", "RE2")
        zrob_paczke(f, "tomb.zip", "Tomb")
        for n in ("tomb.mp4", "demo_gta6_30s.mp4"):
            open(os.path.join(f, n), "wb").close()
        gry = {g["id"]: g["demo"] for g in self.kat(f).publiczne()["gry"]}
        self.assertEqual(gry, {"grand-theft-auto-vi-v4": True, "re2": False, "tomb": True})

    def test_nowe_demo_i_nowy_zapis_proby_odswiezaja_kafelki(self):
        f = nowy_katalog("odcisk")
        zrob_paczke(f, "re2.zip", "RE2", id_paczki="IDR")
        kat = self.kat(f)
        self.assertEqual([(g["demo"], g["stan"]) for g in kat.publiczne()["gry"]], [(False, "niesprawdzona")])
        open(os.path.join(f, "re2.mp4"), "wb").close()  # the zip itself did not change
        with open(kat.plik_dowodow, "w") as fh:
            json.dump({"IDR": {"wersja": "1.0.0", "rodzaj": "nagranie", "data": "x", "jak": "y"}}, fh)
        kat.skanuj()
        self.assertEqual([(g["demo"], g["stan"]) for g in kat.publiczne()["gry"]], [(True, "nagranie")])


class TestMotyw(unittest.TestCase):
    def test_bialy_napis_na_przycisku_ma_co_najmniej_4_5(self):
        for kolor, drugi in (((255, 255, 255), None), ((255, 230, 0), None), ((210, 210, 210), None),
                             ((255, 79, 154), (255, 154, 74)), ((0, 0, 0), None), ((120, 220, 255), (255, 255, 160))):
            m = S.motyw_z_okladki(okladka_jpg(kolor, drugi))
            for k in ("akcent", "akcent2"):
                rgb = tuple(int(m[k][i:i + 2], 16) for i in (1, 3, 5))
                self.assertGreaterEqual(S.kontrast(S.BIALY, rgb), 4.5, (kolor, k, m[k]))
            self.assertGreaterEqual(m["kontrast_tla"], 4.5, kolor)


class TestTrasy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        k = nowy_katalog("trasy")
        f = os.path.join(k, "paczki")
        zrob_paczke(f, "aaa.zip", "Gra A", znacznik="A")
        cls.st = stan(k, f, ustawienia=zapisz_ustawienia(k, {"klucz": KLUCZ, "gra": "aaa"}))
        cls.srv = _Serwer(cls.st)

    @classmethod
    def tearDownClass(cls):
        cls.srv.zamknij()

    NOWE = (("GET", "/api/katalog"), ("GET", "/api/okladka/aaa"), ("POST", "/api/probka"), ("POST", "/api/paczka"),
            ("POST", "/api/stop"), ("POST", "/api/start"))

    def test_nowe_trasy_z_sieci_bez_klucza_403(self):
        for metoda, sciezka in self.NOWE:
            kod, _ = self.srv.zapytaj(metoda, sciezka, host="macbook-testowy.local")
            self.assertEqual(kod, 403, sciezka)
            kod, _ = self.srv.zapytaj(metoda, sciezka, host="macbook-testowy.local", klucz="zly" + KLUCZ)
            self.assertEqual(kod, 403, sciezka)

    def test_z_kluczem_200(self):
        for metoda, sciezka in (("GET", "/api/katalog"), ("GET", "/api/okladka/aaa"), ("POST", "/api/stop")):
            kod, dane = self.srv.zapytaj(metoda, sciezka, host="macbook-testowy.local", klucz=KLUCZ)
            self.assertEqual(kod, 200, sciezka)
        kod, dane = self.srv.zapytaj("GET", f"/api/okladka/aaa?k={KLUCZ}", host="macbook-testowy.local")
        self.assertEqual((kod, dane[:2]), (200, b"\xff\xd8"))  # a JPEG; <img> on the phone sends the key in ?k=

    def test_okladka_tylko_przez_identyfikator_z_katalogu(self):
        for sciezka in ("/api/okladka/nie-ma-takiej", "/api/okladka/..%2f..%2fustawienia.json", "/api/okladka/"):
            kod, _ = self.srv.zapytaj("GET", sciezka)
            self.assertEqual(kod, 404, sciezka)

    def test_strona_i_stan_maja_ta_sama_wersje(self):
        _, html = self.srv.zapytaj("GET", "/")
        _, st = self.srv.json("GET", "/api/stan")
        self.assertIn(f'const WERSJA="{st["wersja"]}"'.encode(), html)
        self.assertIn(b'"__WER"+"SJA__"', html)  # the placeholder check survives the substitution

    def test_katalog_przez_http(self):
        _, kat = self.srv.json("GET", "/api/katalog")
        self.assertEqual((kat["stan"], [g["id"] for g in kat["gry"]], kat["wybrana"]), ("ok", ["aaa"], "aaa"))
        self.assertNotIn("plik", kat["gry"][0])  # disk paths never leave the server


class TestSprzatanie(unittest.TestCase):
    def setUp(self):
        self.stary = tempfile.tempdir
        tempfile.tempdir = nowy_katalog("tmp")  # only this test's folders are counted

    def tearDown(self):
        tempfile.tempdir = self.stary

    def foldery(self):
        return sorted(n for n in os.listdir(tempfile.tempdir) if n.startswith("lektor-"))

    def test_start_stop_nie_zostawia_folderow_tej_sesji(self):
        p = L.Paczka(zrob_paczke(nowy_katalog("sprz"), "gra.zip", "Gra"))
        glos = L.Glos(p, 1.0, 2, True)
        glos.start()
        src = L.ZrodloEkran(p.roi()[0])
        self.assertEqual(len(self.foldery()), 2)
        glos.zamknij()
        src.zamknij()
        self.assertEqual(self.foldery(), [])
        glos.join(2)
        self.assertFalse(glos.is_alive(), "the voice thread ends on close and lets the pack go")

    def test_wyjscie_programu_sprzata_reszte(self):
        L.nowy_tymczasowy("lektor-glos-")
        self.assertEqual(len(self.foldery()), 1)
        L.sprzataj_tymczasowe()
        self.assertEqual(self.foldery(), [])


if __name__ == "__main__":
    unittest.main()
