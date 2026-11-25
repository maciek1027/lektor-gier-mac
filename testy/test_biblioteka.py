"""GameReader library tests: the list (cache, offline), covers, "Pobierz" (one at a time, disk and space checks, `.czesc`,
checking the pack before it gets its .zip name) — against a FAKE GameReader server on 127.0.0.1 (no network, no real
voice-pack content). List entries have the field shape of the real list (voicepack-proxy…workers.dev, read 2026-11-25).

Run (macOS, from the repo folder):   .venv/bin/python -m unittest testy.test_biblioteka -v
"""
import http.client
import json
import os
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under discover and as testy.test_biblioteka
import test_katalog as TK  # noqa: E402 — sets the environment (no operator disk) and loads the server module

S = TK.S
KLUCZ = "kluczTestowyBiblioteki123"


def paczka_bajty(id_paczki, nazwa, kwestie=4, bez_nagran=0, man_id=None, bez_napisow=False):
    """An invented GameReader 2.0 pack as bytes (same build as test_katalog.zrob_paczke)."""
    import io
    b = io.BytesIO()
    wpisy = [{"id": f"k{i}", "text": f"Kwestia numer {i} w grze {nazwa}, dość długa."} for i in range(kwestie)]
    with zipfile.ZipFile(b, "w") as z:
        man = {"name": nazwa, "id": id_paczki if man_id is None else man_id, "version": "1.0.0", "author": "test",
               "description": "Opis."}
        if man["id"] == "":  # a manifest without the id field
            del man["id"]
        z.writestr("manifest.json", json.dumps(man, ensure_ascii=False))
        if not bez_napisow:
            z.writestr("subtitles.json", json.dumps({"entries": wpisy}, ensure_ascii=False))
        z.writestr("preset.json", json.dumps(TK.PRESET_RE2))
        z.writestr("settings.json", "{}")
        for i, e in enumerate(wpisy[bez_nagran:]):
            z.writestr(f"audio/{e['id']}.opus", f"AUDIO-{nazwa}-{i}".encode())
        z.writestr("images/cover.jpg", TK.okladka_jpg())
    return b.getvalue()


class Udawany(ThreadingHTTPServer):
    """Fake GameReader: /spis (the list), /p/<name> (packs), /c/<id>.jpg (covers). `zachowanie[path]` changes one
    answer: 'obetnij' (half, then close), ('wolno', s) (the whole file in 8 slices over s seconds), ('zawies', s)
    (half, then silence for s seconds), 500."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), self.Obsluga)
        self.pliki, self.zachowanie, self.odwiedziny, self.naglowki = {}, {}, [], []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def adres(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    class Obsluga(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            srv = self.server
            srv.odwiedziny.append(self.path)
            srv.naglowki.append(dict(self.headers))
            dane, z = srv.pliki.get(self.path), srv.zachowanie.get(self.path)
            if isinstance(z, tuple) and z[0] == "przekieruj":
                self.send_response(302)
                self.send_header("Location", z[1])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if dane is None or z == 500:
                self.send_response(404 if dane is None else 500)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(dane)))
            self.end_headers()
            try:
                if z == "obetnij":
                    self.wfile.write(dane[:len(dane) // 2])
                elif isinstance(z, tuple) and z[0] == "wolno":
                    kawalek = max(1, len(dane) // 8)
                    for i in range(0, len(dane), kawalek):
                        self.wfile.write(dane[i:i + kawalek])
                        self.wfile.flush()
                        time.sleep(z[1] / 8)
                elif isinstance(z, tuple) and z[0] == "zawies":
                    self.wfile.write(dane[:len(dane) // 2])
                    self.wfile.flush()
                    time.sleep(z[1])
                else:
                    self.wfile.write(dane)
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True


def wpis_spisu(srv, id_paczki, nazwa, dane, preset=True, wtyczka=False, wymagania=None, plik=None):
    """One list entry with the field set of the real list; the pack and its cover served by the fake server."""
    plik = plik or (nazwa.lower().replace(" ", "-") + ".zip")
    srv.pliki[f"/p/{plik}"] = dane
    srv.pliki[f"/c/{id_paczki}.jpg"] = TK.okladka_jpg((30, 140, 200))
    v = {"id": id_paczki, "name": nazwa, "version": "1.0.0", "download_url": f"{srv.adres}/p/{plik}",
         "file_size_bytes": len(dane), "checksum": "64bac7c4946f06f48aab59ad551559eef665a6a99a4d1a7bcf1d587f5c74142f",
         "published_at": "2026-09-21T23:38:58Z", "description": "Opis gry.", "tts_engine": "elevenlabs",
         "author": "Autor", "locale": "pl-PL", "has_preset": preset, "has_plugin": wtyczka,
         "cover_url": f"{srv.adres}/c/{id_paczki}.jpg"}
    if wymagania:
        v["requirements"] = [{"name": "Polecamy", "description": w, "url": "https://example.org/spolszczenie"}
                             for w in wymagania]
    return v


def ustaw_spis(srv, wpisy):
    srv.pliki["/spis"] = json.dumps({"schema_version": "1.0.0", "generated_at": "2026-11-24T01:03:59Z",
                                     "voice_packs": wpisy}, ensure_ascii=False).encode()


def stan_z_biblioteka(srv, folder=None, pobieranie=True, dysk_paczki=()):
    k = TK.nowy_katalog("bib")
    f = folder or os.path.join(k, "paczki")
    for plik, nazwa, i in dysk_paczki:
        TK.zrob_paczke(f, plik, nazwa, id_paczki=i)
    plik_dowodow = os.path.join(k, "sprawdzone.json")
    with open(plik_dowodow, "w") as fh:
        json.dump({}, fh)
    st = S.Stan(ustawienia=TK.zapisz_ustawienia(k, {"klucz": KLUCZ}), folder=f, pamiec=os.path.join(k, "pamiec"),
                sprawdzone=plik_dowodow, spis=f"{srv.adres}/spis", hosty=("127.0.0.1",), https=False,
                pobieranie=pobieranie)
    st.uruchom()
    czekaj(lambda: not st.sterowanie.locked() and st.biblioteka.stan != "laduje", 15, "start")
    return st, k, f


def czekaj(warunek, s, co=""):
    koniec = time.time() + s
    while time.time() < koniec:
        if warunek():
            return True
        time.sleep(0.05)
    raise AssertionError(f"nie doczekałem się w {s} s: {co}")


def gra_w_bibliotece(st, i):
    return next((g for g in st.biblioteka.publiczne()["gry"] if g["id"] == i), None)


class TestSpis(unittest.TestCase):
    def test_lista_z_sieci_zapisana_offline_pokazuje_zapisana_z_data(self):
        srv = Udawany()
        ustaw_spis(srv, [wpis_spisu(srv, "aaa-1", "Gra A", paczka_bajty("aaa-1", "Gra A"))])
        st, k, f = stan_z_biblioteka(srv)
        pub = st.biblioteka.publiczne()
        self.assertEqual((pub["stan"], [g["nazwa"] for g in pub["gry"]]), ("ok", ["Gra A"]))
        srv.shutdown()
        srv.server_close()  # no network now: a new server on the same cache shows the saved list, with its date
        st2 = S.Stan(ustawienia=os.path.join(k, "ustawienia.json"), folder=f, pamiec=os.path.join(k, "pamiec"),
                     spis=f"{srv.adres}/spis", hosty=("127.0.0.1",), https=False)
        st2.uruchom()
        czekaj(lambda: "Nie mogę pobrać" in (st2.biblioteka.komunikat or ""), 25, "komunikat offline")
        pub2 = st2.biblioteka.publiczne()
        self.assertEqual(pub2["stan"], "offline")
        self.assertEqual([g["nazwa"] for g in pub2["gry"]], ["Gra A"])
        self.assertIn(pub2["data_tekst"], pub2["komunikat"])

    def test_brak_sieci_i_brak_zapisanej_listy_to_komunikat_a_moje_gry_dzialaja(self):
        srv = Udawany()  # no /spis on the server = 404
        st, k, f = stan_z_biblioteka(srv, dysk_paczki=[("aaa.zip", "Gra na dysku", "id-dysk")])
        pub = st.biblioteka.publiczne()
        self.assertEqual(pub["stan"], "brak")
        self.assertEqual(pub["gry"], [])
        self.assertIn("Moje gry", pub["komunikat"])
        czekaj(lambda: st.katalog.publiczne()["stan"] == "ok" and st.katalog.publiczne()["gry"][0].get("id_paczki"), 10)
        self.assertEqual([g["nazwa"] for g in st.katalog.publiczne()["gry"]], ["Gra na dysku"])

    def test_gra_z_dysku_nie_dubluje_sie_dopasowanie_po_id_nie_po_nazwie_pliku(self):
        srv = Udawany()
        ustaw_spis(srv, [wpis_spisu(srv, "id-dysk", "Gra na dysku", paczka_bajty("id-dysk", "Gra na dysku"),
                                    plik="inna-nazwa-1-0-4.zip"),
                         wpis_spisu(srv, "id-nowa", "Gra nowa", paczka_bajty("id-nowa", "Gra nowa"))])
        st, k, f = stan_z_biblioteka(srv, dysk_paczki=[("gra-v4.zip", "Gra na dysku", "id-dysk")])
        czekaj(lambda: "id-dysk" in st.katalog.id_paczek(), 10)
        pub = st.biblioteka.publiczne()
        self.assertEqual([g["id"] for g in pub["gry"]], ["id-nowa"])
        self.assertEqual(pub["wszystkich"], 2)

    def test_9_bez_obszaru_napisow_oznaczone_nie_ukryte_i_bez_pobierania(self):
        srv = Udawany()
        ustaw_spis(srv, [wpis_spisu(srv, "bez-1", "Gra z wtyczka", paczka_bajty("bez-1", "W"), preset=False, wtyczka=True)])
        st, k, f = stan_z_biblioteka(srv)
        g = gra_w_bibliotece(st, "bez-1")
        self.assertEqual((g["stan"], g["stan_nazwa"]), ("nie_na_macu", "Na razie nie na Macu"))
        wynik = st.biblioteka.pobierz("bez-1")
        self.assertFalse(wynik["ok"])
        self.assertEqual(os.listdir(f) if os.path.isdir(f) else [], [])

    def test_adres_spoza_serwera_gamereadera_odrzucony(self):
        srv = Udawany()
        obcy = wpis_spisu(srv, "obcy-1", "Obcy", paczka_bajty("obcy-1", "Obcy"))
        obcy["download_url"] = "http://zly-adres.example/p/obcy.zip"
        ok = wpis_spisu(srv, "ok-1", "Dobra", paczka_bajty("ok-1", "Dobra"))
        ok["cover_url"] = "http://zly-adres.example/c.jpg"
        ustaw_spis(srv, [obcy, ok])
        st, k, f = stan_z_biblioteka(srv)
        pub = st.biblioteka.publiczne()
        self.assertEqual([g["id"] for g in pub["gry"]], ["ok-1"])
        self.assertIsNone(st.biblioteka.wpisy["ok-1"]["okladka_url"])

    def test_do_serwera_gamereadera_idzie_tylko_ogolna_nazwa_programu(self):
        srv = Udawany()
        ustaw_spis(srv, [wpis_spisu(srv, "aaa-1", "Gra A", paczka_bajty("aaa-1", "Gra A"))])
        st, k, f = stan_z_biblioteka(srv)
        czekaj(lambda: len(srv.naglowki) >= 2, 10, "okładka")
        for n in srv.naglowki:
            self.assertEqual(n.get("User-Agent"), S.UA)
            self.assertNotIn(KLUCZ, json.dumps(n))
        self.assertTrue(all("?" not in p for p in srv.odwiedziny))

    def test_okladka_biblioteki_tylko_przez_identyfikator_z_listy(self):
        srv = Udawany()
        ustaw_spis(srv, [wpis_spisu(srv, "aaa-1", "Gra A", paczka_bajty("aaa-1", "Gra A"))])
        st, k, f = stan_z_biblioteka(srv)
        czekaj(lambda: st.biblioteka.okladka("aaa-1") is not None, 10, "okładka")
        self.assertEqual(st.biblioteka.okladka("aaa-1")[:2], b"\xff\xd8")
        for zla in ("nie-ma", "..", "../spis", "aaa-1/../../x"):
            self.assertIsNone(st.biblioteka.okladka(zla), zla)

    def test_okladka_webp_przyjeta(self):  # two covers in the real list are WebP named .jpg
        srv = Udawany()
        w = wpis_spisu(srv, "aaa-1", "Gra A", paczka_bajty("aaa-1", "Gra A"))
        srv.pliki["/c/aaa-1.jpg"] = b"RIFF\x1c{\x00\x00WEBPVP8 " + b"\x00" * 40
        ustaw_spis(srv, [w])
        st, k, f = stan_z_biblioteka(srv)
        czekaj(lambda: st.biblioteka.okladka("aaa-1") is not None, 10, "okładka WebP")

    def test_wymagania_z_listy_dla_gry_na_dysku_bez_wlasnych(self):
        srv = Udawany()
        ustaw_spis(srv, [wpis_spisu(srv, "id-dysk", "Gra", paczka_bajty("id-dysk", "Gra"),
                                    wymagania=["Przezroczystość tła napisów na 100%"])])
        st, k, f = stan_z_biblioteka(srv, dysk_paczki=[("gra.zip", "Gra", "id-dysk")])
        wym = st.biblioteka.wymagania("id-dysk")
        self.assertEqual([w["tekst"] for w in wym], ["Przezroczystość tła napisów na 100%"])
        self.assertEqual(wym[0]["url"], "https://example.org/spolszczenie")


class TestPobieranie(unittest.TestCase):
    def setUp(self):
        self.srv = Udawany()
        self.dane = paczka_bajty("pob-1", "Gra do pobrania", kwestie=6)
        self.wpis = wpis_spisu(self.srv, "pob-1", "Gra do pobrania", self.dane, plik="Gra%20do%20pobrania.zip")
        ustaw_spis(self.srv, [self.wpis])

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def pobierz_i_czekaj(self, st, i="pob-1", s=15):
        wynik = st.biblioteka.pobierz(i)
        if wynik["ok"]:
            czekaj(lambda: not st.biblioteka.trwa(), s, "koniec pobierania")
        return wynik

    def test_pobranie_sprawdzenie_i_kafelek_w_moje_gry_jako_niesprawdzona(self):
        st, k, f = stan_z_biblioteka(self.srv)
        self.assertTrue(self.pobierz_i_czekaj(st)["ok"])
        self.assertEqual(sorted(os.listdir(f)), ["Gra-do-pobrania.zip"])
        with open(os.path.join(f, "Gra-do-pobrania.zip"), "rb") as fh:
            self.assertEqual(fh.read(), self.dane)  # the pack is stored unchanged (INV-GPP-006)
        czekaj(lambda: any(g.get("stan") == "niesprawdzona" for g in st.katalog.publiczne()["gry"]), 10, "kafelek")
        self.assertIsNone(gra_w_bibliotece(st, "pob-1"))  # it left the library list

    def test_przerwane_polaczenie_nie_zostawia_zip_ani_czesc(self):
        self.srv.zachowanie["/p/Gra%20do%20pobrania.zip"] = "obetnij"
        st, k, f = stan_z_biblioteka(self.srv)
        self.pobierz_i_czekaj(st)
        self.assertEqual(os.listdir(f), [])
        g = gra_w_bibliotece(st, "pob-1")
        self.assertEqual(g["stan"], "blad_pobierania")
        self.assertIn("urwało się w trakcie", g["stan_opis"])  # a cut connection, not "a broken pack"
        self.assertTrue(g["reczne"])  # GameReader-side failure: the tile offers the manual route on their site
        self.assertIn("spróbuj ponownie", g["stan_opis"].lower())

    def test_cisza_serwera_konczy_pobieranie_z_komunikatem(self):
        self.srv.zachowanie["/p/Gra%20do%20pobrania.zip"] = ("zawies", 4)
        stary = S.CISZA_S
        S.CISZA_S = 1
        try:
            st, k, f = stan_z_biblioteka(self.srv)
            t = time.time()
            self.pobierz_i_czekaj(st, s=6)
            self.assertLess(time.time() - t, 3.5)
        finally:
            S.CISZA_S = stary
        self.assertIn("przestał wysyłać", gra_w_bibliotece(st, "pob-1")["stan_opis"])
        self.assertEqual(os.listdir(f), [])

    def test_uszkodzona_paczka_testzip_odrzucona(self):
        zle = bytearray(self.dane)
        p = zle.find(b"AUDIO-")  # a stored member's bytes changed: CRC no longer matches, size unchanged
        zle[p:p + 6] = b"XXXXXX"
        self.srv.pliki["/p/Gra%20do%20pobrania.zip"] = bytes(zle)
        st, k, f = stan_z_biblioteka(self.srv)
        self.pobierz_i_czekaj(st)
        self.assertEqual(os.listdir(f), [])
        self.assertIn("uszkodzony plik w środku", gra_w_bibliotece(st, "pob-1")["stan_opis"])

    def test_paczka_bez_nagran_odrzucona_a_z_kilkoma_brakami_przyjeta(self):
        st, k, f = stan_z_biblioteka(self.srv)
        bez = paczka_bajty("pob-1", "Gra do pobrania", kwestie=6, bez_nagran=6)
        self.srv.pliki["/p/Gra%20do%20pobrania.zip"] = bez
        st.biblioteka.wpisy["pob-1"]["rozmiar"] = len(bez)
        self.pobierz_i_czekaj(st)
        self.assertEqual(os.listdir(f), [])
        self.assertIn("nie ma żadnego nagrania", gra_w_bibliotece(st, "pob-1")["stan_opis"])
        czesciowo = paczka_bajty("pob-1", "Gra do pobrania", kwestie=6, bez_nagran=2)  # like GTA VI: some lines without
        self.srv.pliki["/p/Gra%20do%20pobrania.zip"] = czesciowo
        st.biblioteka.wpisy["pob-1"]["rozmiar"] = len(czesciowo)
        self.pobierz_i_czekaj(st)
        self.assertEqual(os.listdir(f), ["Gra-do-pobrania.zip"])

    def test_inna_paczka_w_srodku_odrzucona(self):
        inna = paczka_bajty("pob-1", "Gra do pobrania", man_id="zupelnie-inna")
        self.srv.pliki["/p/Gra%20do%20pobrania.zip"] = inna
        st, k, f = stan_z_biblioteka(self.srv)
        st.biblioteka.wpisy["pob-1"]["rozmiar"] = len(inna)
        self.pobierz_i_czekaj(st)
        self.assertEqual(os.listdir(f), [])

    def test_paczka_bez_id_w_manifescie_odrzucona(self):  # "Moje gry" and the library match packs by this id
        bez_id = paczka_bajty("pob-1", "Gra do pobrania", man_id="")
        self.srv.pliki["/p/Gra%20do%20pobrania.zip"] = bez_id
        st, k, f = stan_z_biblioteka(self.srv)
        st.biblioteka.wpisy["pob-1"]["rozmiar"] = len(bez_id)
        self.pobierz_i_czekaj(st)
        self.assertEqual(os.listdir(f), [])
        self.assertIn("inna paczka", gra_w_bibliotece(st, "pob-1")["stan_opis"])

    def test_przerwij_w_trakcie_sprawdzania_nic_nie_publikuje(self):
        st, k, f = stan_z_biblioteka(self.srv)
        prawdziwe = S.sprawdz_paczke

        def wolne_sprawdzenie(*a):  # the click on "Przerwij" lands while the (slow, for a 3 GB pack) check runs
            wynik = prawdziwe(*a)
            st.biblioteka.przerwij()
            return wynik
        S.sprawdz_paczke = wolne_sprawdzenie
        try:
            self.pobierz_i_czekaj(st)
        finally:
            S.sprawdz_paczke = prawdziwe
        self.assertEqual(os.listdir(f), [])

    def test_przekierowanie_poza_serwer_odrzucone(self):
        port = self.srv.server_address[1]
        self.srv.zachowanie["/p/Gra%20do%20pobrania.zip"] = ("przekieruj", f"http://localhost:{port}/obcy.zip")
        self.srv.pliki["/obcy.zip"] = self.dane  # same bytes, but the host is not on the allowed list
        st, k, f = stan_z_biblioteka(self.srv)
        self.pobierz_i_czekaj(st)
        self.assertEqual(os.listdir(f), [])
        self.assertNotIn("/obcy.zip", self.srv.odwiedziny)
        self.assertEqual(gra_w_bibliotece(st, "pob-1")["stan"], "blad_pobierania")

    def test_cudzy_czesc_nie_usuniety(self):  # two servers on one folder picked the same name at the same moment
        st, k, f = stan_z_biblioteka(self.srv)
        os.makedirs(f, exist_ok=True)
        cudzy = os.path.join(f, "Gra-do-pobrania.zip.czesc")
        prawdziwa = st.biblioteka._nazwa_pliku
        st.biblioteka._nazwa_pliku = lambda w, folder: (open(cudzy, "wb").write(b"cudze"), prawdziwa(w, folder))[1].replace("-2", "")
        try:
            self.pobierz_i_czekaj(st)
        finally:
            st.biblioteka._nazwa_pliku = prawdziwa
        self.assertTrue(os.path.exists(cudzy))
        with open(cudzy, "rb") as fh:
            self.assertEqual(fh.read(), b"cudze")
        self.assertFalse(gra_w_bibliotece(st, "pob-1")["reczne"])  # a local file problem: their site would not help

    def test_sprawdzenie_nie_uzywa_pola_checksum(self):  # the list's checksum is not the file's SHA-256 (2026-11-22)
        st, k, f = stan_z_biblioteka(self.srv)
        self.assertNotEqual(st.biblioteka.wpisy["pob-1"].get("checksum"), self.wpis["checksum"])
        self.pobierz_i_czekaj(st)
        self.assertEqual(os.listdir(f), ["Gra-do-pobrania.zip"])

    def test_jedno_pobieranie_naraz_drugie_odmowa_z_komunikatem(self):
        drugi = paczka_bajty("pob-2", "Druga gra")
        ustaw_spis(self.srv, [self.wpis, wpis_spisu(self.srv, "pob-2", "Druga gra", drugi)])
        self.srv.zachowanie["/p/Gra%20do%20pobrania.zip"] = ("wolno", 2)
        st, k, f = stan_z_biblioteka(self.srv)
        self.assertTrue(st.biblioteka.pobierz("pob-1")["ok"])
        odp = st.biblioteka.pobierz("pob-2")
        self.assertFalse(odp["ok"])
        self.assertIn("Pobieram już „Gra do pobrania”", odp["blad"])
        self.assertTrue(st.biblioteka.trwa())
        czekaj(lambda: not st.biblioteka.trwa(), 10)
        self.assertEqual(os.listdir(f), ["Gra-do-pobrania.zip"])
        self.assertTrue(st.biblioteka.pobierz("pob-2")["ok"])  # free again after the first one ended
        czekaj(lambda: not st.biblioteka.trwa(), 10)

    def test_pobieranie_nie_blokuje_graj(self):
        self.srv.zachowanie["/p/Gra%20do%20pobrania.zip"] = ("wolno", 2)
        st, k, f = stan_z_biblioteka(self.srv, dysk_paczki=[("aaa.zip", "Gra A", "id-a")])
        self.assertTrue(st.biblioteka.pobierz("pob-1")["ok"])
        self.assertTrue(st.sterowanie.acquire(blocking=False))  # the reader's one lock stays free during a download
        st.sterowanie.release()
        czekaj(lambda: not st.biblioteka.trwa(), 10)

    def test_odlaczony_dysk_podlacz_dysk_i_nic_nie_zapisane(self):
        folder = "/Volumes/LektorTestNieMaTakiegoDysku/paczki"
        st, k, f = stan_z_biblioteka(self.srv, folder=folder)
        odp = st.biblioteka.pobierz("pob-1")
        self.assertFalse(odp["ok"])
        self.assertIn("Podłącz dysk", odp["blad"])
        self.assertFalse(os.path.exists("/Volumes/LektorTestNieMaTakiegoDysku"))
        self.assertEqual(self.srv.odwiedziny.count("/p/Gra%20do%20pobrania.zip"), 0)
        self.assertEqual(gra_w_bibliotece(st, "pob-1")["stan"], "blad_pobierania")
        self.assertFalse(gra_w_bibliotece(st, "pob-1")["reczne"])  # our disk: their site would not help

    def test_za_malo_miejsca_komunikat_i_nic_nie_pobrane(self):
        st, k, f = stan_z_biblioteka(self.srv)
        prawdziwe = S.shutil.disk_usage
        S.shutil.disk_usage = lambda p: prawdziwe(p)._replace(free=len(self.dane) + S.ZAPAS_B - 1)
        try:
            odp = st.biblioteka.pobierz("pob-1")
        finally:
            S.shutil.disk_usage = prawdziwe
        self.assertFalse(odp["ok"])
        self.assertIn("Za mało miejsca", odp["blad"])
        self.assertEqual(os.listdir(f), [])
        self.assertNotIn("/p/Gra%20do%20pobrania.zip", self.srv.odwiedziny)

    def test_istniejacy_zip_nigdy_nie_nadpisany(self):
        st, k, f = stan_z_biblioteka(self.srv)
        os.makedirs(f, exist_ok=True)
        with open(os.path.join(f, "Gra-do-pobrania.zip"), "wb") as fh:
            fh.write(b"cudza paczka")
        self.pobierz_i_czekaj(st)
        with open(os.path.join(f, "Gra-do-pobrania.zip"), "rb") as fh:
            self.assertEqual(fh.read(), b"cudza paczka")
        self.assertIn("Gra-do-pobrania-2.zip", os.listdir(f))

    def test_przerwij_usuwa_czesc(self):
        self.srv.zachowanie["/p/Gra%20do%20pobrania.zip"] = ("wolno", 3)
        st, k, f = stan_z_biblioteka(self.srv)
        st.biblioteka.pobierz("pob-1")
        czekaj(lambda: (st.biblioteka.postep() or {}).get("pobrane", 0) > 0, 5, "pierwsze bajty")
        self.assertTrue(st.biblioteka.przerwij(czekaj_s=5)["ok"])
        self.assertFalse(st.biblioteka.trwa())
        self.assertEqual(os.listdir(f), [])
        self.assertEqual(gra_w_bibliotece(st, "pob-1")["stan"], "nie_na_dysku")  # a cancel is not an error

    def test_przerwij_budzi_zawieszony_odczyt(self):  # the server went silent mid-file: "Przerwij" must not wait 30 s
        self.srv.zachowanie["/p/Gra%20do%20pobrania.zip"] = ("zawies", 20)
        st, k, f = stan_z_biblioteka(self.srv)
        st.biblioteka.pobierz("pob-1")
        czekaj(lambda: (st.biblioteka.postep() or {}).get("pobrane", 0) > 0, 5, "pierwsza połowa")
        time.sleep(0.3)  # the worker is now blocked in a read
        t = time.time()
        st.biblioteka.przerwij(czekaj_s=5)
        self.assertLess(time.time() - t, 2.0)
        self.assertFalse(st.biblioteka.trwa())
        self.assertEqual(os.listdir(f), [])

    def test_wylaczone_pobieranie_odmawia_i_lista_zostaje(self):
        st, k, f = stan_z_biblioteka(self.srv, pobieranie=False)
        self.assertFalse(st.biblioteka.publiczne()["pobieranie"])
        odp = st.biblioteka.pobierz("pob-1")
        self.assertFalse(odp["ok"])
        self.assertIn("gamereader.pl", odp["blad"])
        self.assertEqual([g["id"] for g in st.biblioteka.publiczne()["gry"]], ["pob-1"])


class TestLokalneEnv(unittest.TestCase):
    def test_przelacznik_pobierania_z_pliku_lokalne_env(self):  # README/AGENTS promise this line works
        k = TK.nowy_katalog("env")
        plik = os.path.join(k, "lokalne.env")
        with open(plik, "w") as fh:
            fh.write("LEKTOR_POBIERANIE=0\n")
        stary = os.environ.pop("LEKTOR_POBIERANIE", None)
        try:
            TK.L.wczytaj_lokalne(plik)
            self.assertEqual(os.environ.get("LEKTOR_POBIERANIE"), "0")
        finally:
            os.environ.pop("LEKTOR_POBIERANIE", None)
            if stary is not None:
                os.environ["LEKTOR_POBIERANIE"] = stary


class TestCzesc(unittest.TestCase):
    def test_skan_katalogu_ignoruje_czesc(self):
        k = TK.nowy_katalog("czesc")
        f = os.path.join(k, "paczki")
        TK.zrob_paczke(f, "aaa.zip", "Gra A")
        os.rename(TK.zrob_paczke(f, "bbb.zip", "Gra B"), os.path.join(f, "bbb.zip.czesc"))
        st = TK.stan(k, f)
        czekaj(lambda: st.katalog.publiczne()["stan"] == "ok", 10)
        self.assertEqual([g["nazwa"] for g in st.katalog.publiczne()["gry"]], ["Gra A"])

    def test_start_sprzata_stara_czesc_i_cien_a_mloda_zostawia(self):
        k = TK.nowy_katalog("sprzatanie")
        f = os.path.join(k, "paczki")
        os.makedirs(f)
        for n in ("stara.zip.czesc", "._stara.zip.czesc", "mloda.zip.czesc"):
            with open(os.path.join(f, n), "wb") as fh:
                fh.write(b"x")
        stary = time.time() - S.STARA_CZESC_S - 60
        for n in ("stara.zip.czesc", "._stara.zip.czesc"):
            os.utime(os.path.join(f, n), (stary, stary))
        TK.stan(k, f)
        self.assertEqual(os.listdir(f), ["mloda.zip.czesc"])  # a young one may be another server's download


class TestStraznik(unittest.TestCase):
    """Closing the last panel window must not cut a download; "Wyłącz program" stops it and removes the .czesc."""

    class Atrapa:
        def __init__(self):
            self.koniec = threading.Event()

        def shutdown(self):
            self.koniec.set()

    def setUp(self):
        self.srv = Udawany()
        self.dane = paczka_bajty("pob-1", "Gra do pobrania", kwestie=40)
        ustaw_spis(self.srv, [wpis_spisu(self.srv, "pob-1", "Gra do pobrania", self.dane)])
        self.stary = S.STAN

    def tearDown(self):
        S.STAN = self.stary
        self.srv.shutdown()
        self.srv.server_close()

    def test_zamkniete_okno_czeka_na_koniec_pobierania(self):
        self.srv.zachowanie["/p/gra-do-pobrania.zip"] = ("wolno", 4)
        st, k, f = stan_z_biblioteka(self.srv)
        S.STAN = st
        st.biblioteka.pobierz("pob-1")
        st.zamknij_o = time.time() - 1  # the panel window closed
        atrapa = self.Atrapa()
        threading.Thread(target=S.straznik, args=(atrapa,), daemon=True).start()
        self.assertFalse(atrapa.koniec.wait(2.5))  # still downloading: no shutdown
        self.assertTrue(st.biblioteka.trwa())
        self.assertTrue(atrapa.koniec.wait(10))  # download done → the server ends
        self.assertEqual(os.listdir(f), ["gra-do-pobrania.zip"])

    def test_zamkniete_okno_w_trakcie_pobierania_ucisza_lektora(self):
        self.srv.zachowanie["/p/gra-do-pobrania.zip"] = ("wolno", 6)
        st, k, f = stan_z_biblioteka(self.srv)
        S.STAN = st
        stop, glos = threading.Event(), type("Glos", (), {"zamknij": lambda self: None})()
        watek = threading.Thread(target=stop.wait, args=(30,), daemon=True)  # stands in for the reader (OCR + voice)
        watek.start()
        st.sesja = {"stop": stop, "watek": watek, "glos": glos, "okno": None, "zrodlo": "ekran", "profil": "tv_mac",
                    "start": time.time()}
        st.biblioteka.pobierz("pob-1")
        st.zamknij_o = time.time() - 1  # the panel window closed
        atrapa = self.Atrapa()
        threading.Thread(target=S.straznik, args=(atrapa,), daemon=True).start()
        czekaj(lambda: st.sesja is None, 4, "lektor zatrzymany")
        self.assertTrue(stop.is_set())
        self.assertTrue(st.biblioteka.trwa())  # the download goes on
        self.assertFalse(atrapa.koniec.is_set())
        self.assertTrue(atrapa.koniec.wait(15))

    def test_wylacz_program_przerywa_pobieranie_i_sprzata(self):
        self.srv.zachowanie["/p/gra-do-pobrania.zip"] = ("wolno", 8)
        st, k, f = stan_z_biblioteka(self.srv)
        S.STAN = st
        st.biblioteka.pobierz("pob-1")
        czekaj(lambda: (st.biblioteka.postep() or {}).get("pobrane", 0) > 0, 5)
        st.wylacz = True
        atrapa = self.Atrapa()
        threading.Thread(target=S.straznik, args=(atrapa,), daemon=True).start()
        self.assertTrue(atrapa.koniec.wait(10))
        self.assertFalse(st.biblioteka.trwa())
        self.assertEqual(os.listdir(f), [])


class TestTrasyBiblioteki(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gr = Udawany()
        ustaw_spis(cls.gr, [wpis_spisu(cls.gr, "aaa-1", "Gra A", paczka_bajty("aaa-1", "Gra A"))])
        cls.st, _, _ = stan_z_biblioteka(cls.gr)
        czekaj(lambda: cls.st.biblioteka.okladka("aaa-1") is not None, 10, "okładka")
        cls.srv = TK._Serwer(cls.st)

    @classmethod
    def tearDownClass(cls):
        cls.srv.zamknij()
        cls.gr.shutdown()
        cls.gr.server_close()

    NOWE = (("GET", "/api/biblioteka"), ("GET", "/api/biblioteka/okladka/aaa-1"), ("POST", "/api/biblioteka/pobierz"),
            ("POST", "/api/biblioteka/przerwij"), ("POST", "/api/biblioteka/odswiez"))

    def test_trasy_biblioteki_z_sieci_bez_klucza_403(self):
        for metoda, sciezka in self.NOWE:
            kod, _ = self.srv.zapytaj(metoda, sciezka, host="macbook-testowy.local")
            self.assertEqual(kod, 403, sciezka)

    def test_z_kluczem_lista_i_okladka(self):
        kod, dane = self.srv.zapytaj("GET", "/api/biblioteka", host="macbook-testowy.local", klucz=KLUCZ)
        self.assertEqual(kod, 200)
        self.assertEqual([g["id"] for g in json.loads(dane)["gry"]], ["aaa-1"])
        self.assertNotIn(b"download_url", dane)
        self.assertNotIn(b"127.0.0.1:", dane)  # the list's addresses never reach the page
        kod, dane = self.srv.zapytaj("GET", f"/api/biblioteka/okladka/aaa-1?k={KLUCZ}", host="macbook-testowy.local")
        self.assertEqual((kod, dane[:2]), (200, b"\xff\xd8"))
        for sciezka in ("/api/biblioteka/okladka/nie-ma", "/api/biblioteka/okladka/..%2f..%2fspis.json"):
            self.assertEqual(self.srv.zapytaj("GET", sciezka)[0], 404, sciezka)

    def test_wiele_okladek_naraz_bez_zerwanych_polaczen(self):
        """The browser asks for dozens of library covers at once (6+ connections, lazy images): every request must get its
        answer. Measured 2026-11-25: 12 threads × 96 covers → 33 'Connection reset by peer' with the default backlog of 5."""
        from concurrent.futures import ThreadPoolExecutor

        def jedna(_):
            try:
                return self.srv.zapytaj("GET", "/api/biblioteka/okladka/aaa-1", timeout=10)[0]
            except OSError as e:
                return type(e).__name__
        with ThreadPoolExecutor(32) as ex:
            wyniki = list(ex.map(jedna, range(192)))
        self.assertEqual([w for w in wyniki if w != 200], [])

    def test_stan_niesie_numer_biblioteki_i_postep(self):
        _, st = self.srv.json("GET", "/api/stan")
        self.assertIn("biblioteka_nr", st)
        self.assertIsNone(st["pobieranie"])


if __name__ == "__main__":
    unittest.main()
