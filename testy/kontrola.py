#!/usr/bin/env python3
"""Positive controls for the tests: each guard below is removed in a COPY of the code (never in this folder) and the
named test must FAIL. A guard whose test stays green means that test does not check what its name promises.

Run from the repo folder:   .venv/bin/python testy/kontrola.py            (all)
                            .venv/bin/python testy/kontrola.py <word>     (only mutations whose name contains it)
Exit codes: 0 every mutation caught · 1 one survived or could not be applied (result) · 2 NOT MEASURED (the
unmodified copy already fails, or the copy could not be made).
"""
import os
import shutil
import subprocess
import sys
import tempfile

TU = os.path.dirname(os.path.abspath(__file__))
KOD = os.path.dirname(TU)
T = "testy.test_katalog."
B = "testy.test_biblioteka."

# (name, file, find — must match exactly once, replace, test)
MUTACJE = [
    ("konsola: zła wartość z pliku ustawień → Xbox (PS5)", "serwer_panelu.py",
     '    if k == "konsola" and v in KONSOLE:', '    if k == "konsola":',
     T + "TestUstawienia.test_zly_plik_ustawien_nie_zatrzymuje_serwera"),
    ("konsola zapisywana przez Ustawienia (PS5)", "serwer_panelu.py",
     '"wyglad", "konsola"):', '"wyglad"):',
     T + "TestKonsola.test_wybor_konsoli_zapisany_w_stanie_i_pliku"),
    ("PS5 bez aplikacji: Graj odmawia, zanim coś otworzy (PS5)", "serwer_panelu.py",
     '            if otworz_xbox and self.ust["konsola"] == "playstation" and not ps_remote_play():',
     '            if False:',
     T + "TestKonsola.test_graj_ps5_bez_aplikacji_odmawia_zanim_cokolwiek_otworzy"),
    ("Graj otwiera konsolę z ustawień, nie zawsze Xbox — też z pilota (PS5)", "serwer_panelu.py",
     'blad = otworz_gre_zdalna(self.ust["konsola"])', 'blad = otworz_gre_zdalna("xbox")',
     T + "TestKonsola.test_pilot_graj_otwiera_konsole_z_ustawien"),
    ("PS5: brak aplikacji = komunikat, nie cicha próba (PS5)", "serwer_panelu.py",
     "        if not aplikacja:\n            return PS_BRAK", "        if False:\n            return PS_BRAK",
     T + "TestKonsola.test_playstation_bez_aplikacji_to_komunikat_co_dlaczego_jak"),
    ("Otwórz ponownie wg konsoli (PS5)", "serwer_panelu.py",
     '            blad = otworz_gre_zdalna(STAN.ust["konsola"])', '            blad = otworz_gre_zdalna("xbox")',
     T + "TestKonsola.test_otworz_ponownie_wg_konsoli"),
    ("stary plik ustawień: gra domyślna = GTA VI (popr. 3)", "serwer_panelu.py",
     '        return wpis.get("nazwa") == GRA_DOMYSLNA', "        return False",
     T + "TestUstawienia.test_stary_plik_bez_gry_wstaje_na_gta6_z_tym_samym_kluczem"),
    ("stary plik ustawień: klucz pilota zostaje (popr. 3)", "serwer_panelu.py",
     '    if re.fullmatch(r"[A-Za-z0-9_-]{12,64}", str(zapis.get("klucz", ""))):', "    if False:",
     T + "TestUstawienia.test_stary_plik_bez_gry_wstaje_na_gta6_z_tym_samym_kluczem"),
    ("każde pole ustawień z wartością domyślną (popr. 3)", "serwer_panelu.py",
     "                ust[k] = popraw_ustawienie(k, v)", "                ust[k] = v",
     T + "TestUstawienia.test_zly_plik_ustawien_nie_zatrzymuje_serwera"),
    ("zmiana gry budzi wiszący long-poll (popr. 1)", "serwer_panelu.py",
     '                self._nadaj({"typ": "zmiana_gry"})', "                pass",
     T + "TestZmianaGry.test_wiszacy_long_poll_wraca_od_razu_bez_kwestii_starej_gry"),
    ("zmiana gry usuwa niezagrane kwestie starej gry (popr. 1, 10)", "serwer_panelu.py",
     '                self.zdarzenia = collections.deque((e for e in self.zdarzenia if e["typ"] != "kwestia"), maxlen=50)',
     "                pass",
     T + "TestZmianaGry.test_kwestia_starej_gry_nie_trafia_do_telefonu_po_zmianie"),
    ("long-poll oddaje tylko zdarzenia bieżącej sesji gry (popr. 1)", "serwer_panelu.py",
     'if od >= 0 and e["n"] > od and e["g"] == g]', 'if od >= 0 and e["n"] > od]',
     T + "TestZmianaGry.test_spozniona_kwestia_ze_starym_numerem_jest_odfiltrowana"),
    ("nagranie tylko dla bieżącego numeru sesji gry (popr. 1)", "serwer_panelu.py",
     "g != biezaca or not (0 <= idx", "not (0 <= idx",
     T + "TestZmianaGry.test_nagranie_ze_starym_numerem_sesji_nie_daje_dzwieku_nowej_gry"),
    ("nagranie bez numeru sesji nie jest wydawane (recenzja r2)", "serwer_panelu.py",
     "g != biezaca or not (0 <= idx", "(g is not None and g != biezaca) or not (0 <= idx",
     T + "TestZmianaGry.test_nagranie_bez_numeru_sesji_nie_jest_wydawane"),
    ("Graj przy tej samej grze odmawia, gdy czytnik nie stanął (recenzja r2)", "serwer_panelu.py",
     "            elif not self._stop():  # a reader that did not end", "            elif self._stop() and False:  # a reader that did not end",
     T + "TestBlokada.test_graj_przy_tej_samej_grze_nie_doklada_drugiego_czytnika"),
    ("odcisk katalogu obejmuje dema i zapis prób (recenzja r2)", "serwer_panelu.py",
     "odcisk = (tuple((n, staty[n].st_size, staty[n].st_mtime_ns) for n in nazwy), tuple(mp4), dowody_t)",
     "odcisk = (tuple((n, staty[n].st_size, staty[n].st_mtime_ns) for n in nazwy),)",
     T + "TestKatalog.test_nowe_demo_i_nowy_zapis_proby_odswiezaja_kafelki"),
    ("jedna blokada: zajęta = odmowa, nie kolejka (popr. 9)", "serwer_panelu.py",
     "        if not self.sterowanie.acquire(blocking=False):  # one at a time",
     "        if not self.sterowanie.acquire():  # one at a time",
     T + "TestBlokada.test_zajeta_blokada_to_odmowa_z_komunikatem"),
    ("potwierdzone zatrzymanie przed podmianą paczki (popr. 2)", "serwer_panelu.py",
     "        if not self._stop():\n            return {\"ok\": False, \"blad\": \"Lektor nie zatrzymał się — gry nie zmieniam.",
     "        if not self._stop() and False:\n            return {\"ok\": False, \"blad\": \"Lektor nie zatrzymał się — gry nie zmieniam.",
     T + "TestBlokada.test_czytnik_ktory_nie_staje_blokuje_zmiane_gry"),
    ("próbka odmówiona (iPhone bez dźwięku) nie zmienia gry", "serwer_panelu.py",
     '            if profil == "iphone" and not self.telefon_slucha():  # checked BEFORE anything is stopped or swapped',
     '            if False:',
     T + "TestBlokada.test_probka_z_iphonea_bez_dzwieku_nie_zmienia_gry"),
    ("brak dysku ≠ brak folderu (popr. 11)", "serwer_panelu.py",
     'czesci[1] == "Volumes" and not os.path.ismount', 'czesci[1] == "Volumes_" and not os.path.ismount',
     T + "TestKatalog.test_brak_dysku_to_komunikat_nie_pusty_katalog"),
    ("stan kafelka wg reguły z planu: pole czytania = nie na Macu (pułapka E1b)", "serwer_panelu.py",
     '        d = dowody.get(w.get("id_paczki") or "")',
     '        if any(u.get("czytanie") for u in (w.get("uwagi") or [])):\n'
     '            w.update(stan="nie_na_macu", stan_opis="x")\n            return\n'
     '        d = dowody.get(w.get("id_paczki") or "")',
     T + "TestKatalog.test_stany_kafelkow_z_dowodu_proby_nie_z_samej_listy_pol"),
    ("dowód próby tylko dla tej samej wersji paczki", "serwer_panelu.py",
     'd.get("wersja") == w.get("wersja")', "True",
     T + "TestKatalog.test_stany_kafelkow_z_dowodu_proby_nie_z_samej_listy_pol"),
    ("pliki ._* i ukryte pomijane w folderze paczek", "lektor.py",
     ' and not f.startswith(".")', "",
     T + "TestKatalog.test_pliki_maca_i_czesci_pobran_pominiete"),
    ("demo stare tylko przy GTA VI", "serwer_panelu.py",
     "        if not self.jest_domyslna(wpis):\n            return None\n", "",
     T + "TestKatalog.test_demo_tylko_dla_wlasnej_gry"),
    ("pamięć podręczna unieważniana rozmiarem i datą (popr. 6)", "serwer_panelu.py",
     'meta.get("rozmiar") == st.st_size and meta.get("mtime_ns") == st.st_mtime_ns and ', "",
     T + "TestKatalog.test_pamiec_podreczna_uniewazniana_rozmiarem_i_data"),
    ("kolor akcentu przyciemniany do 4,5:1 (krok 3)", "serwer_panelu.py",
     "    while kontrast(BIALY, wynik) < cel and l > 0:", "    while False:",
     T + "TestMotyw.test_bialy_napis_na_przycisku_ma_co_najmniej_4_5"),
    ("okładka za kluczem pilota z sieci (popr. 15)", "serwer_panelu.py",
     r'|apple-touch-icon(-precomposed)?\.png|favicon\.ico)")',
     r'|apple-touch-icon(-precomposed)?\.png|favicon\.ico|api/okladka/.*)")',
     T + "TestTrasy.test_nowe_trasy_z_sieci_bez_klucza_403"),
    ("numer wersji wstawiany w stronę (popr. 16)", "serwer_panelu.py",
     '            html = html.replace(b"__WERSJA__", wersja_strony().encode())', "            pass",
     T + "TestTrasy.test_strona_i_stan_maja_ta_sama_wersje"),
    ("Wyłącz program kończy serwer mimo otwartego panelu (E1d)", "serwer_panelu.py",
     "        if wylacz or (zamknij_o and time.time() > zamknij_o) or nikt:",
     "        if (zamknij_o and time.time() > zamknij_o) or nikt:",
     T + "TestWylacz.test_program_konczy_sie_mimo_otwartego_panelu"),
    ("przycisk Wyłącz program ustawia znacznik wyłączenia (E1d)", "serwer_panelu.py",
     "            self.wylacz = True", "            self.zamknij_o = time.time() + 5  # the old way: a poll cancels it",
     T + "TestWylacz.test_program_konczy_sie_mimo_otwartego_panelu"),
    ("inne okna widzą wyłączenie w /api/stan (recenzja E1d)", "serwer_panelu.py",
     '                "wylacz": self.wylacz,', "",
     T + "TestWylacz.test_program_konczy_sie_mimo_otwartego_panelu"),
    ("paczka w trakcie kopiowania = Kopiuję…, nie błąd (E1d)", "serwer_panelu.py",
     "            if 0 <= time.time() - mtime < KOPIA_S:", "            if False:",
     T + "TestKatalog.test_paczka_w_trakcie_kopiowania_to_nie_blad"),
    ("data z przyszłości to błąd, nie wieczne kopiowanie (recenzja E1d r2)", "serwer_panelu.py",
     "            if 0 <= time.time() - mtime < KOPIA_S:", "            if time.time() - mtime < KOPIA_S:",
     T + "TestKatalog.test_uszkodzona_z_data_z_przyszlosci_to_blad_nie_kopiowanie"),
    ("kopiowany plik oceniany znów przy następnym przejściu (E1d)", "serwer_panelu.py",
     'self.odcisk = None if any(w.get("stan") == "kopiuje" for w in gry.values()) else odcisk', "self.odcisk = odcisk",
     T + "TestKatalog.test_paczka_w_trakcie_kopiowania_to_nie_blad"),
    ("głos usuwa swój folder tymczasowy (krok 4)", "lektor.py",
     "            self.wolne.set()\n        usun_tymczasowy(self.tmp)", "            self.wolne.set()",
     T + "TestSprzatanie.test_start_stop_nie_zostawia_folderow_tej_sesji"),
    ("zrzut ekranu usuwa swój folder tymczasowy (krok 4)", "lektor.py",
     "    def zamknij(self):\n        usun_tymczasowy(self.folder)", "    def zamknij(self):\n        pass",
     T + "TestSprzatanie.test_start_stop_nie_zostawia_folderow_tej_sesji"),
    ("wątek głosu kończy się przy zamknięciu", "lektor.py",
     "                while not self.q and not self.koniec:", "                while not self.q:",
     T + "TestSprzatanie.test_start_stop_nie_zostawia_folderow_tej_sesji"),
    ("ikona iPhone'a bez przezroczystości (E1e)", "aplikacja/ikona.py",
     "        body.convert(\"RGB\").save(out)", "        body.save(out)",
     "testy.test_ikona.TestIkona.test_ikona_iphonea_bez_przezroczystosci"),
    ("ikona Docka przycięta do kształtu macOS (E1e)", "aplikacja/ikona.py",
     "    icon.paste(canvas, (0, 0), mask)", "    icon.paste(canvas, (0, 0))",
     "testy.test_ikona.TestIkona.test_ikona_docka_ma_pelne_wnetrze_i_przezroczyste_rogi"),
    ("brak obrazu ikony = błąd, nie inna ikona (E1e)", "aplikacja/ikona.py",
     "        return 2\n", "        art = Image.new(\"RGBA\", (S, S), (0, 0, 0, 255))\n",
     "testy.test_ikona.TestIkona.test_brak_obrazu_to_blad_nie_inna_ikona"),
    ("wyjście programu sprząta resztę folderów", "lektor.py",
     "    for sciezka in list(TYMCZASOWE):\n        usun_tymczasowy(sciezka)",
     "    for sciezka in list(TYMCZASOWE):\n        pass",
     T + "TestSprzatanie.test_wyjscie_programu_sprzata_reszte"),
    # E1c — GameReader library and "Pobierz"
    ("sprawdzenie po pobraniu: testzip (E1c)", "serwer_panelu.py",
     "            zly = z.testzip()", "            zly = None",
     B + "TestPobieranie.test_uszkodzona_paczka_testzip_odrzucona"),
    ("sprawdzenie po pobraniu: paczka bez nagrań (E1c)", "serwer_panelu.py",
     "    if bez == len(ids):", "    if False:",
     B + "TestPobieranie.test_paczka_bez_nagran_odrzucona_a_z_kilkoma_brakami_przyjeta"),
    ("sprawdzenie po pobraniu: częściowe braki nagrań przyjęte (E1c, odstępstwo od planu)", "serwer_panelu.py",
     "    if bez == len(ids):", "    if bez:",
     B + "TestPobieranie.test_paczka_bez_nagran_odrzucona_a_z_kilkoma_brakami_przyjeta"),
    ("sprawdzenie po pobraniu: ta sama paczka co na liście (E1c)", "serwer_panelu.py",
     '    if man.get("id") is None or str(man["id"]) != id_spisu:', "    if False:",
     B + "TestPobieranie.test_inna_paczka_w_srodku_odrzucona"),
    ("urwane połączenie: rozmiar jak na liście (E1c)", "serwer_panelu.py",
     '            if p["pobrane"] != w["rozmiar"]:', "            if False:",
     B + "TestPobieranie.test_przerwane_polaczenie_nie_zostawia_zip_ani_czesc"),
    ("plik .czesc nie wchodzi do skanu katalogu (E1c, popr. 14)", "lektor.py",
     'if f.lower().endswith(rozszerzenie) and not f.startswith("."))',
     'if rozszerzenie in f.lower() and not f.startswith("."))',
     B + "TestCzesc.test_skan_katalogu_ignoruje_czesc"),
    ("sprzątanie .czesc przy starcie (E1c, popr. 4)", "serwer_panelu.py",
     "        self.biblioteka.sprzataj()\n", "",
     B + "TestCzesc.test_start_sprzata_stara_czesc_i_cien_a_mloda_zostawia"),
    ("sprzątanie zostawia młody .czesc — cudze pobieranie (E1c)", "serwer_panelu.py",
     "                    if time.time() - os.path.getmtime(plik) > STARA_CZESC_S:", "                    if True:",
     B + "TestCzesc.test_start_sprzata_stara_czesc_i_cien_a_mloda_zostawia"),
    ("jedno pobieranie naraz (E1c, popr. 4)", "serwer_panelu.py",
     "        if not self.pob_lock.acquire(blocking=False):  # one at a time",
     "        if not (self.pob_lock.acquire(blocking=False) or True):  # one at a time",
     B + "TestPobieranie.test_jedno_pobieranie_naraz_drugie_odmowa_z_komunikatem"),
    ("brak dysku = podłącz dysk, nic na dysk Maca (E1c, popr. 11)", "serwer_panelu.py",
     "            dysk = dysk_odlaczony(folder)  # checked BEFORE", "            dysk = None  # checked BEFORE",
     B + "TestPobieranie.test_odlaczony_dysk_podlacz_dysk_i_nic_nie_zapisane"),
    ("wolne miejsce przed pobraniem (E1c, popr. 4)", "serwer_panelu.py",
     '            if wolne < w["rozmiar"] + ZAPAS_B:', "            if False:",
     B + "TestPobieranie.test_za_malo_miejsca_komunikat_i_nic_nie_pobrane"),
    ("istniejący .zip nigdy nadpisany (E1c)", "serwer_panelu.py",
     '        while os.path.exists(os.path.join(folder, nazwa)) or', '        while False and os.path.exists(os.path.join(folder, nazwa)) or',
     B + "TestPobieranie.test_istniejacy_zip_nigdy_nie_nadpisany"),
    ("zamknięte okno nie przerywa pobierania (E1c)", "serwer_panelu.py",
     "        if not wylacz and STAN.biblioteka.trwa() and", "        if False and STAN.biblioteka.trwa() and",
     B + "TestStraznik.test_zamkniete_okno_czeka_na_koniec_pobierania"),
    ("„Wyłącz program” przerywa pobieranie i sprząta (E1c)", "serwer_panelu.py",
     "                STAN.biblioteka.przerwij(czekaj_s=5)", "                pass",
     B + "TestStraznik.test_wylacz_program_przerywa_pobieranie_i_sprzata"),
    ("adresy z listy tylko z serwera GameReadera (E1c)", "serwer_panelu.py",
     '        return u.scheme in (("https",) if self.https else ("https", "http")) and u.hostname in self.hosty',
     "        return True",
     B + "TestSpis.test_adres_spoza_serwera_gamereadera_odrzucony"),
    ("gra z dysku nie dubluje się w bibliotece (E1c)", "serwer_panelu.py",
     "                if i in na_dysku:\n                    continue\n", "",
     B + "TestSpis.test_gra_z_dysku_nie_dubluje_sie_dopasowanie_po_id_nie_po_nazwie_pliku"),
    ("offline = zapisana lista (E1c)", "serwer_panelu.py",
     "                os.replace(tmp, self._plik_spisu())", "                os.remove(tmp)",
     B + "TestSpis.test_lista_z_sieci_zapisana_offline_pokazuje_zapisana_z_data"),
    ("trasy biblioteki za kluczem pilota (E1c, INV-GPP-004)", "serwer_panelu.py",
     "        if PUBLICZNE.fullmatch(u.path):\n            return True, True, u.path, q",
     "        if PUBLICZNE.fullmatch(u.path) or u.path.startswith(\"/api/biblioteka\"):\n            return True, True, u.path, q",
     B + "TestTrasyBiblioteki.test_trasy_biblioteki_z_sieci_bez_klucza_403"),
    ("paczka bez id w manifeście odrzucona (E1c, panel r1)", "serwer_panelu.py",
     '    if man.get("id") is None or str(man["id"]) != id_spisu:', '    if man.get("id") and str(man["id"]) != id_spisu:',
     B + "TestPobieranie.test_paczka_bez_id_w_manifescie_odrzucona"),
    ("przerwanie w trakcie sprawdzania nic nie publikuje (E1c, panel r1)", "serwer_panelu.py",
     '            if p["przerwij"].is_set():  # "Przerwij" / "Wyłącz program" during the check', '            if False:  #',
     B + "TestPobieranie.test_przerwij_w_trakcie_sprawdzania_nic_nie_publikuje"),
    ("przekierowanie poza serwer GameReadera odrzucone (E1c, panel r1)", "serwer_panelu.py",
     "        if not self.dobry(newurl):", "        if False:",
     B + "TestPobieranie.test_przekierowanie_poza_serwer_odrzucone"),
    ("cudzy .czesc nie usunięty (E1c, panel r1)", "serwer_panelu.py",
     '            if p["utworzony"]:  # only our own file', "            if True:  # only our own file",
     B + "TestPobieranie.test_cudzy_czesc_nie_usuniety"),
    ("Przerwij budzi zawieszony odczyt (E1c, panel r2)", "serwer_panelu.py",
     "                gniazdo.shutdown(socket.SHUT_RDWR)", "                pass",
     B + "TestPobieranie.test_przerwij_budzi_zawieszony_odczyt"),
    ("zamknięte okno w trakcie pobierania ucisza lektora (E1c, doradca)", "serwer_panelu.py",
     "            if STAN.sesja is not None:\n                STAN.stop(czekaj=True)", "            if False:\n                STAN.stop(czekaj=True)",
     B + "TestStraznik.test_zamkniete_okno_w_trakcie_pobierania_ucisza_lektora"),
    ("przełącznik D2 czytany z lokalne.env (E1c, doradca)", "lektor.py",
     ', "LEKTOR_POBIERANIE")  # last', ')  # last',
     B + "TestLokalneEnv.test_przelacznik_pobierania_z_pliku_lokalne_env"),
    ("awaria po stronie GameReadera proponuje stronę (E1c, operator)", "serwer_panelu.py",
     "                if serwer:\n                    self.bledy_serwera.add", "                if False:\n                    self.bledy_serwera.add",
     B + "TestPobieranie.test_przerwane_polaczenie_nie_zostawia_zip_ani_czesc"),
    ("brak dysku nie odsyła na stronę (E1c, operator)", "serwer_panelu.py",
     "            serwer = isinstance(e, (urllib.error.URLError,", "            serwer = True or isinstance(e, (urllib.error.URLError,",
     B + "TestPobieranie.test_cudzy_czesc_nie_usuniety"),
    ("wiele okładek naraz bez zerwanych połączeń (E1c, zrzuty do repo)", "serwer_panelu.py",
     "    request_queue_size = 128\n", "",
     B + "TestTrasyBiblioteki.test_wiele_okladek_naraz_bez_zerwanych_polaczen"),
    ("pobieranie wyłączone przełącznikiem D2 (E1c)", "serwer_panelu.py",
     "        if not self.wlaczone:\n            return {\"ok\": False", "        if False:\n            return {\"ok\": False",
     B + "TestPobieranie.test_wylaczone_pobieranie_odmawia_i_lista_zostaje"),
]


def kopia():
    cel = tempfile.mkdtemp(prefix="lektor-kontrola-")
    for n in ("lektor.py", "serwer_panelu.py", "sprawdzone.json"):
        shutil.copy2(os.path.join(KOD, n), cel)
    os.makedirs(os.path.join(cel, "panel"))
    shutil.copy2(os.path.join(KOD, "panel", "index.html"), os.path.join(cel, "panel"))
    os.makedirs(os.path.join(cel, "aplikacja"))
    for n in ("ikona.py", "ikona-zrodlo.jpg"):
        shutil.copy2(os.path.join(KOD, "aplikacja", n), os.path.join(cel, "aplikacja"))
    shutil.copytree(TU, os.path.join(cel, "testy"), ignore=shutil.ignore_patterns("__pycache__"))
    return cel


def test(cel, nazwa):
    env = dict(os.environ)
    env.pop("LEKTOR_TEST_PACZKI", None)
    try:
        r = subprocess.run([sys.executable, "-m", "unittest", nazwa], cwd=cel, env=env, capture_output=True, text=True,
                           timeout=180)
    except subprocess.TimeoutExpired:  # a mutation that hangs the test (e.g. a waiting lock) is caught, not a crash
        return -1, ["limit czasu 180 s — test zawisł"]
    return r.returncode, (r.stderr or r.stdout).strip().splitlines()


def main():
    slowo = sys.argv[1] if len(sys.argv) > 1 else ""
    wybrane = [m for m in MUTACJE if slowo.lower() in m[0].lower()]
    if not wybrane:
        print(f"NIE ZMIERZYŁEM: żadna mutacja nie pasuje do „{slowo}”.")
        return 2
    try:
        cel = kopia()
    except OSError as e:
        print(f"NIE ZMIERZYŁEM: nie udała się kopia kodu ({e}).")
        return 2
    try:
        testy = sorted({m[4] for m in wybrane})
        for t in testy:  # the unmodified copy must be green, else a red result would mean nothing
            kod, wyjscie = test(cel, t)
            if kod != 0:
                print(f"NIE ZMIERZYŁEM: test pada już bez mutacji: {t}\n   " + "\n   ".join(wyjscie[-6:]))
                return 2
        zle, niezastosowane = 0, 0
        for nazwa, plik, szukaj, zamien, t in wybrane:
            sciezka = os.path.join(cel, plik)
            with open(sciezka, encoding="utf-8") as f:
                oryginal = f.read()
            if oryginal.count(szukaj) != 1:
                print(f"[NIEZASTOSOWANA ×{oryginal.count(szukaj)}] {nazwa}  (wzorzec nie trafia dokładnie raz)")
                niezastosowane += 1
                continue
            with open(sciezka, "w", encoding="utf-8") as f:
                f.write(oryginal.replace(szukaj, zamien))
            try:
                kod, wyjscie = test(cel, t)
            finally:
                with open(sciezka, "w", encoding="utf-8") as f:
                    f.write(oryginal)
            if kod != 0:
                powod = next((l for l in reversed(wyjscie) if l.startswith(("AssertionError", "FAIL", "ERROR"))), "")
                print(f"[ZŁAPANA] {nazwa}  ← {powod[:110]}")
            else:
                print(f"[PRZEŻYŁA] {nazwa}  (test {t.rsplit('.', 1)[1]} zielony z wyjętym zabezpieczeniem)")
                zle += 1
        print(f"\nMutacje: {len(wybrane)}, złapane: {len(wybrane) - zle - niezastosowane}, przeżyły: {zle}, "
              f"niezastosowane: {niezastosowane}.")
        return 1 if zle or niezastosowane else 0
    finally:
        shutil.rmtree(cel, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
