"""Community reports: the GitHub form, the prefilled link, the privacy checks and the sprawdzone.json format.

Run (any Python 3.9+, no .venv needed):   python3 -m unittest testy/test_zgloszenie.py -v
The form checks need PyYAML (the GitHub workflow installs it; on a Mac /usr/bin/python3 usually has it) — without it
they are SKIPPED, which means "not measured", not "passed".

Why this exists: sprawdzone.json is merged from Pull Requests of strangers, and the panel reads it with
`except ValueError: return {}` — a broken file would silently take every badge away from every user.
"""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import urllib.parse
import zipfile

TU = os.path.dirname(os.path.abspath(__file__))
KOD = os.path.dirname(TU)
sys.path.insert(0, KOD)
import zgloszenie as Z  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None

FORMULARZ = os.path.join(KOD, ".github", "ISSUE_TEMPLATE", Z.SZABLON)
POLA_WPISU = {"nazwa", "wersja", "rodzaj", "data", "jak", "obraz", "napisy", "zrodlo", "zgloszenie"}
WYMAGANE = {"nazwa", "wersja", "rodzaj", "data", "jak"}
TMP = tempfile.mkdtemp(prefix="lektor-test-zgloszenie-")


def bledy_wpisu(k, v):
    """Everything wrong with one sprawdzone.json record (empty list = fine)."""
    b = []
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", k):
        b.append("klucz to nie id paczki")
    if not isinstance(v, dict):
        return b + ["rekord nie jest obiektem"]
    b += [f"brak pola {p}" for p in sorted(WYMAGANE - set(v))]
    b += [f"nieznane pole {p}" for p in sorted(set(v) - POLA_WPISU)]
    b += [f"pole {p} nie jest napisem" for p, x in v.items() if not isinstance(x, str)]
    if v.get("rodzaj") not in ("na_zywo", "nagranie"):
        b.append("rodzaj spoza na_zywo / nagranie")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v.get("data", ""))):
        b.append("data nie w formacie RRRR-MM-DD")
    if "obraz" in v and v["obraz"] not in Z.OBRAZ_WPIS.values():
        b.append("obraz spoza xbox_zdalnie / karta")
    for p, x in v.items():  # every value is public (the panel shows jak; GitHub shows the whole file)
        tekst = str(x)
        if len(tekst) > 300:
            b.append(f"pole {p} za długie")
        if any(w.search(tekst) for w, _ in Z.WRAZLIWE):
            b.append(f"pole {p} wygląda na dane wrażliwe")
    return b


def uruchom(*argv):
    """main() with stdout/stderr captured -> (exit code, output)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            kod = Z.main(list(argv))
        except SystemExit as e:
            kod = e.code
    return kod, out.getvalue()


def paczka(nazwa="Gra Testowa Żółć", wersja="1.2.3", id_="abcd-1234"):
    plik = os.path.join(tempfile.mkdtemp(dir=TMP), "Moja Paczka.zip")
    with zipfile.ZipFile(plik, "w") as z:
        z.writestr("manifest.json", json.dumps({"name": nazwa, "version": wersja, "id": id_}))
    return plik


class TestSprawdzone(unittest.TestCase):
    def test_plik_w_repo_jest_poprawny(self):
        with open(Z.SPRAWDZONE, encoding="utf-8") as f:
            dowody = json.load(f)
        self.assertIsInstance(dowody, dict)
        zle = {k: bledy_wpisu(k, v) for k, v in dowody.items() if k != "_opis" and bledy_wpisu(k, v)}
        self.assertEqual(zle, {})
        self.assertGreaterEqual(len(dowody) - 1, 1)

    def test_zapis_skryptu_daje_ten_sam_uklad_co_plik(self):
        """--dopisz rewrites the file: an unchanged file must come out byte-identical (a PR diff = one line)."""
        with open(Z.SPRAWDZONE, encoding="utf-8") as f:
            przed = f.read()
        cel = os.path.join(TMP, "uklad.json")
        Z.zapisz_sprawdzone(json.loads(przed), cel)
        with open(cel, encoding="utf-8") as f:
            self.assertEqual(f.read(), przed)

    def test_dopisz_zmienia_jedna_linie(self):
        kopia = os.path.join(tempfile.mkdtemp(dir=TMP), "sprawdzone.json")
        shutil.copy2(Z.SPRAWDZONE, kopia)
        with open(kopia, encoding="utf-8") as f:
            przed = f.read().splitlines()
        stary, stare_ust = Z.SPRAWDZONE, Z.USTAWIENIA
        Z.SPRAWDZONE, Z.USTAWIENIA = kopia, os.path.join(TMP, "brak-ustawien.json")
        try:
            kod, out = uruchom(paczka(), "--wynik", "dziala", "--obraz", "karta", "--napisy", "mały", "--dopisz")
            self.assertEqual(kod, 2, out)  # no settings file = blind key check: --dopisz refuses
            Z.USTAWIENIA = os.path.join(tempfile.mkdtemp(dir=TMP), "ustawienia.json")
            with open(Z.USTAWIENIA, "w") as f:
                json.dump({"klucz": "Klucz_Testowy_123456"}, f)
            kod, _ = uruchom(paczka(), "--wynik", "dziala", "--obraz", "karta", "--napisy", "mały", "--dopisz")
        finally:
            Z.SPRAWDZONE, Z.USTAWIENIA = stary, stare_ust
        self.assertEqual(kod, 0)
        with open(kopia, encoding="utf-8") as f:
            po_tekst = f.read()
        po = po_tekst.splitlines()
        self.assertEqual(len(po), len(przed) + 1)
        self.assertEqual([x for x in po if x not in przed], [po[2]])  # one added line, under "_opis", nothing else
        nowy = json.loads(po_tekst)["abcd-1234"]
        self.assertEqual(bledy_wpisu("abcd-1234", nowy), [])
        self.assertEqual((nowy["rodzaj"], nowy["obraz"], nowy["zrodlo"]), ("na_zywo", "karta", "zgloszenie"))

    def test_dopisz_nie_nadpisuje_wersji_juz_sprawdzonej_na_zywo(self):
        kopia = os.path.join(tempfile.mkdtemp(dir=TMP), "sprawdzone.json")
        shutil.copy2(Z.SPRAWDZONE, kopia)
        with open(kopia, encoding="utf-8") as f:
            przed = f.read()
        stary, stare_ust = Z.SPRAWDZONE, Z.USTAWIENIA
        Z.SPRAWDZONE, Z.USTAWIENIA = kopia, os.path.join(tempfile.mkdtemp(dir=TMP), "ustawienia.json")
        with open(Z.USTAWIENIA, "w") as f:
            json.dump({"klucz": "Klucz_Testowy_123456"}, f)
        try:  # GTA VI 1.0.2 is in the repo file as na_zywo (the author's own Xbox test)
            kod, out = uruchom("--gra", "Grand Theft Auto VI", "--wersja", "1.0.2", "--id",
                               "7dc77631-292f-4077-a5ee-aad5b6346ad1", "--wynik", "dziala", "--obraz", "xbox",
                               "--napisy", "mały", "--dopisz")
        finally:
            Z.SPRAWDZONE, Z.USTAWIENIA = stary, stare_ust
        self.assertEqual(kod, 2, out)
        self.assertIn("już sprawdzona na żywo", out)  # refused for this reason, not for a missing settings file
        with open(kopia, encoding="utf-8") as f:
            self.assertEqual(f.read(), przed)

    def test_wynik_negatywny_nie_trafia_do_sprawdzonych(self):
        """A 'gubi' record would show 'Sprawdzona na Xboxie': the panel reads only rodzaj, not the result."""
        for wynik in ("gubi", "nie_czyta"):
            kod, out = uruchom(paczka(), "--wynik", wynik, "--obraz", "xbox", "--napisy", "mały", "--wpis")
            self.assertEqual(kod, 2, wynik)
            self.assertIn("formularzem", out)
        kod, _ = uruchom(paczka(), "--wynik", "dziala", "--obraz", "inne", "--napisy", "mały", "--wpis")
        self.assertEqual(kod, 2)

    def test_wpis_bez_id_odmowa(self):
        kod, out = uruchom("--gra", "X", "--wersja", "1", "--wynik", "dziala", "--obraz", "xbox", "--napisy", "m",
                           "--wpis")
        self.assertEqual(kod, 2)
        self.assertIn("id paczki", out)


class TestPrywatnosc(unittest.TestCase):
    def odmowa(self, *argv):
        kod, out = uruchom(paczka(), "--wynik", "gubi", "--obraz", "xbox", *argv)
        self.assertEqual(kod, 2, out)
        self.assertNotIn("https://github.com", out)
        return out

    def test_sciezka_z_dysku(self):
        for uwagi in ("paczka leży w /Volumes/Dysk/paczki", "zobacz ~/Library/Logs", "plik /tmp/prywatne.txt",
                      "C:/Users/Admin/Gry", "file:///Users/x", "wrzuciłem paczki/prywatna.zip"):
            self.odmowa("--napisy", "mały", "--uwagi", uwagi)

    def test_zwykly_tekst_przechodzi(self):
        """The filters must not refuse ordinary notes (a refusal for 'AC/DC' or 'tło 100 %' would stop people)."""
        kod, out = uruchom(paczka(), "--wynik", "gubi", "--obraz", "xbox", "--napisy", "rozmiar mały, tło 100 %",
                           "--uwagi", "Gubi w misji AC/DC o 21:30, wersja 1.0.2; pomogło: tło 100 %?\n? dziwne")
        self.assertEqual(kod, 0, out)

    def test_adres_pilota_i_email_i_log(self):
        self.odmowa("--napisy", "mały", "--uwagi", "pilot http://192.168.1.20:8797/telefon")
        self.odmowa("--napisy", "mały", "--uwagi", "pisz na jan.kowalski@example.com")
        self.odmowa("--napisy", "mały", "--uwagi", "z dziennika:\n🎙 Hello there")
        self.odmowa("--napisy", "mały", "--uwagi", "14:08:26 Paczka gotowa: GTA VI")

    def z_kluczem(self, *argv, ust_tresc='{"klucz": "Sekretny_Klucz_123456"}', plik=None):
        ust = plik or os.path.join(tempfile.mkdtemp(dir=TMP), "ustawienia.json")
        if ust_tresc is not None:
            with open(ust, "w") as f:
                f.write(ust_tresc)
        stary = Z.USTAWIENIA
        Z.USTAWIENIA = ust
        try:
            return uruchom(*argv)
        finally:
            Z.USTAWIENIA = stary

    def test_klucz_pilota_w_kazdym_polu(self):
        for argv in (("--napisy", "mały", "--uwagi", "mój kod Sekretny_Klucz_123456"),
                     ("--napisy", "Sekretny_Klucz_123456"),):
            kod, out = self.z_kluczem(paczka(), "--wynik", "gubi", "--obraz", "xbox", *argv)
            self.assertEqual(kod, 2, out)
            self.assertNotIn("Sekretny_Klucz_123456", out)
        for pole in ("--gra", "--id", "--wersja"):  # also from a crafted manifest / by hand
            kod, out = self.z_kluczem("--gra", "G", "--wersja", "1", pole, "Sekretny_Klucz_123456", "--wynik",
                                      "gubi", "--obraz", "xbox", "--napisy", "mały")
            self.assertEqual(kod, 2, (pole, out))
            self.assertNotIn("Sekretny_Klucz_123456", out)
        kod, out = self.z_kluczem(paczka(id_="Sekretny_Klucz_123456"), "--wynik", "gubi", "--obraz", "xbox",
                                  "--napisy", "mały")
        self.assertEqual(kod, 2, out)

    def test_zepsute_ustawienia_zatrzymuja_zamiast_wylaczyc_kontrole(self):
        kod, out = self.z_kluczem(paczka(), "--wynik", "gubi", "--obraz", "xbox", "--napisy", "mały",
                                  ust_tresc="{zepsuty")
        self.assertEqual(kod, 2, out)
        stary_env = os.environ.get("LEKTOR_USTAWIENIA")
        os.environ["LEKTOR_USTAWIENIA"] = "/nie/ma/takiego/pliku.json"
        try:
            kod, out = self.z_kluczem(paczka(), "--wynik", "gubi", "--obraz", "xbox", "--napisy", "mały",
                                      ust_tresc=None, plik="/nie/ma/takiego/pliku.json")
        finally:
            if stary_env is None:
                del os.environ["LEKTOR_USTAWIENIA"]
            else:
                os.environ["LEKTOR_USTAWIENIA"] = stary_env
        self.assertEqual(kod, 2, out)

    def test_nazwa_pliku_paczki_w_tekscie(self):
        plik = os.path.join(tempfile.mkdtemp(dir=TMP), "Jan_Kowalski_gry.zip")
        shutil.copy2(paczka(), plik)
        self.odmowa_z(plik, "--napisy", "mały", "--uwagi", "plik Jan_Kowalski_gry z dysku")

    def odmowa_z(self, plik, *argv):
        kod, out = uruchom(plik, "--wynik", "gubi", "--obraz", "xbox", *argv)
        self.assertEqual(kod, 2, out)

    def test_za_dlugie_uwagi(self):
        self.odmowa("--napisy", "mały", "--uwagi", "x" * (Z.LIMIT["uwagi"] + 1))

    def test_sciezka_paczki_nie_wychodzi(self):
        plik = paczka()
        kod, out = uruchom(plik, "--wynik", "dziala", "--obraz", "xbox", "--napisy", "mały")
        self.assertEqual(kod, 0)
        self.assertNotIn(os.path.dirname(plik), out)
        self.assertNotIn("Moja Paczka", out)
        self.assertNotIn("Moja%20Paczka", out)


class TestAdres(unittest.TestCase):
    def test_obieg_adresu_z_ogonkami_i_znakami_specjalnymi(self):
        uwagi = "Gubi w nocy & w deszczu #2\nPomogło tło 100 % (zażółć gęślą jaźń)"
        kod, out = uruchom(paczka(), "--wynik", "gubi", "--obraz", "karta", "--napisy", "mały; tło 100 %",
                           "--uwagi", uwagi)
        self.assertEqual(kod, 0, out)
        url = [w for w in out.splitlines() if w.startswith("https://github.com/")][0]
        cz = urllib.parse.urlsplit(url)
        self.assertEqual((cz.netloc, cz.path), ("github.com", f"/{Z.REPO}/issues/new"))
        self.assertEqual(cz.fragment, "")  # '#' encoded, not cutting the link
        q = {k: v[0] for k, v in urllib.parse.parse_qs(cz.query, keep_blank_values=True).items()}
        self.assertEqual(q["uwagi"], uwagi)
        self.assertEqual(q["gra"], "Gra Testowa Żółć")
        self.assertEqual(q["wynik"], Z.WYNIKI["gubi"][0])
        self.assertEqual(q["obraz"], Z.OBRAZY["karta"][0])
        self.assertEqual(q["template"], Z.SZABLON)
        self.assertIn("— gubi kwestie", q["title"])

    def test_tresc_do_pliku_dla_pull_requestu(self):
        cel = os.path.join(tempfile.mkdtemp(dir=TMP), "tresc.txt")
        kod, out = uruchom(paczka(), "--wynik", "dziala", "--obraz", "xbox", "--napisy", "mały $(printenv)",
                           "--tresc-do", cel)
        self.assertEqual(kod, 0, out)
        with open(cel, encoding="utf-8") as f:
            zapis = f.read()
        self.assertIn("Ustawienia napisów: mały $(printenv)", zapis)  # literal text, nothing expanded
        self.assertIn(zapis.strip(), out)  # the file = exactly what the user was shown

    def test_tresc_do_pokazania_ma_te_same_dane_co_adres(self):
        kod, out = uruchom(paczka(), "--wynik", "dziala", "--obraz", "xbox", "--napisy", "rozmiar mały")
        self.assertEqual(kod, 0)
        tresc = out.split("=== Zgłoszenie jest publiczne")[0]
        for tekst in ("Gra Testowa Żółć", "1.2.3", "abcd-1234", Z.WYNIKI["dziala"][0], Z.OBRAZY["xbox"][0],
                      "rozmiar mały"):
            self.assertIn(tekst, tresc)


@unittest.skipUnless(yaml, "brak PyYAML — formularza NIE sprawdzono")
class TestFormularz(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FORMULARZ, encoding="utf-8") as f:
            cls.f = yaml.safe_load(f)
        cls.pola = {p["id"]: p for p in cls.f["body"] if "id" in p}

    def test_naglowek_formularza(self):
        for k in ("name", "description", "body"):
            self.assertIn(k, self.f)
        self.assertIsInstance(self.f["name"], str)
        self.assertIsInstance(self.f["labels"], list)

    def test_id_unikalne_ascii_i_pokrywaja_adres(self):
        ids = [p["id"] for p in self.f["body"] if "id" in p]
        self.assertEqual(len(ids), len(set(ids)))
        for i in ids:
            self.assertRegex(i, r"^[a-z][a-z0-9_-]*$")
        adres = Z.adres({"gra": "G", "wersja": "1", "id": "x", "wynik": "gubi", "obraz": "xbox", "napisy": "n",
                         "uwagi": "u"})
        parametry = set(urllib.parse.parse_qs(urllib.parse.urlsplit(adres).query))
        self.assertEqual(parametry - {"template", "title"} - set(ids), set())

    def test_opcje_list_rowne_skryptowi(self):
        """yaml turns yes/no/on/numbers into other types: every option must stay a string equal to the script's."""
        self.assertEqual(self.pola["wynik"]["attributes"]["options"], [v[0] for v in Z.WYNIKI.values()])
        self.assertEqual(self.pola["obraz"]["attributes"]["options"], [v[0] for v in Z.OBRAZY.values()])

    def test_wszystkie_teksty_to_napisy_i_typy_znane(self):
        for p in self.f["body"]:
            self.assertIn(p["type"], ("markdown", "input", "textarea", "dropdown", "checkboxes"))
            for k, v in p.get("attributes", {}).items():
                if k == "options":
                    for o in v:
                        self.assertIsInstance(o["label"] if isinstance(o, dict) else o, str)
                else:
                    self.assertIsInstance(v, str, f"{p.get('id')}.{k}")

    def test_wymagane_pola(self):
        wymagane = {i for i, p in self.pola.items() if p.get("validations", {}).get("required")}
        self.assertEqual(wymagane, {"gra", "wersja", "wynik", "obraz", "napisy"})


if __name__ == "__main__":
    unittest.main()
