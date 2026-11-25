#!/usr/bin/env python3
"""Share how a voice pack worked with this reader — only after the user said yes (see AGENTS.md → "Cegiełka").

Prints the EXACT report text to show the user, then a GitHub link that opens the report form already filled in
(the user reviews it and clicks "Submit new issue" on his own account). With --wpis it prints the record for
sprawdzone.json instead; with --dopisz it adds that record to sprawdzone.json (the advanced Pull Request path).

Only our own data leaves the Mac: pack name, version and id (read from manifest.json inside the zip), the result,
the subtitle settings and the user's notes. Never the pack itself, its path, the remote key, logs or screenshots —
the text fields are checked for paths, IP addresses, e-mails, log lines and the remote key before anything prints.

Standard library only (Python 3.9+), so any AI can run it without the program's .venv:

    python3 zgloszenie.py paczki/gra.zip --wynik dziala --obraz xbox --napisy "rozmiar mały, tło 100 %"
    python3 zgloszenie.py paczki/gra.zip --wynik dziala --obraz xbox --napisy "..." --wpis
    python3 zgloszenie.py --gra "Nazwa" --wersja 1.0.0 --wynik gubi --obraz karta --napisy "..." --uwagi "..."

Exit codes: 0 printed · 2 refused (sensitive data / bad input — the message says what to change) · 3 pack unreadable.
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse
import zipfile

REPO = "maciek1027/lektor-gier-mac"
SZABLON = "zgloszenie-gry.yml"  # .github/ISSUE_TEMPLATE/<this>; the field ids below are its ids
FOLDER = os.path.dirname(os.path.abspath(__file__))
SPRAWDZONE = os.path.join(FOLDER, "sprawdzone.json")
USTAWIENIA = os.environ.get("LEKTOR_USTAWIENIA") or os.path.join(FOLDER, "ustawienia.json")

# key -> (dropdown option in the form, word for the title). Options must equal the form's options exactly.
WYNIKI = {
    "dziala": ("Działa — lektor czyta kwestie", "działa"),
    "gubi": ("Gubi kwestie — czyta tylko część", "gubi kwestie"),
    "nie_czyta": ("Nie czyta — lektor milczy", "nie czyta"),
}
OBRAZY = {  # both Xbox options say "Xbox": the badge reads "Sprawdzona na Xboxie" (another console = "inne")
    "xbox": ("Xbox — gra zdalna na Macu", "gra zdalna z Xboxa"),
    "karta": ("Xbox — karta przechwytująca", "Xbox przez kartę przechwytującą"),
    "inne": ("Inna konsola albo inne źródło (opisz w uwagach)", "inne źródło obrazu"),
}
OBRAZ_WPIS = {"xbox": "xbox_zdalnie", "karta": "karta"}  # value of "obraz" in a sprawdzone.json record
LIMIT = {"napisy": 300, "uwagi": 600}
DLUGI_ADRES = 6000  # GitHub cuts links around 8 KB; above this open the empty form and paste the text instead

WRAZLIWE = [
    (re.compile(r"(?<![\w.:])/[\w.-]+/|~/|file:/|\b[A-Za-z]:[/\\]|\\\\|\.(zip|json|log|png|jpe?g|mp4|env)\b", re.I),
     "ścieżkę albo nazwę pliku z dysku"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b|:8797\b|[?&]k="), "adres sieciowy pilota"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "adres e-mail"),
    (re.compile(r"gta-lektor|[🎙📱]|^\s*\d{1,2}:\d{2}:\d{2}\s", re.M), "linie z dziennika programu"),
]


def blad(tekst, kod=2):
    print(f"NIE WYSYŁAJ: {tekst}", file=sys.stderr)
    sys.exit(kod)


def klucz_pilota():
    """The remote key, so no field can carry it. No settings file at the default place = no remote was ever set up
    (nothing to leak); a file we cannot read, or LEKTOR_USTAWIENIA pointing nowhere, stops the report — a broken
    read must not silently switch the key check off."""
    if not os.path.exists(USTAWIENIA):
        if os.environ.get("LEKTOR_USTAWIENIA"):
            blad("LEKTOR_USTAWIENIA wskazuje plik, którego nie ma — podaj ustawienia.json z folderu instalacji, "
                 "inaczej nie sprawdzę, czy w tekście nie ma klucza pilota.")
        return ""
    try:
        with open(USTAWIENIA, encoding="utf-8") as f:
            k = json.load(f).get("klucz") or ""
    except (OSError, ValueError, AttributeError) as e:
        blad(f"nie mogę odczytać ustawienia.json ({type(e).__name__}) — bez tego nie sprawdzę klucza pilota.")
    return str(k) if len(str(k)) >= 6 else ""


def sprawdz_tekst(nazwa, tekst, klucz):
    if len(tekst) > LIMIT.get(nazwa, 120):
        blad(f"pole „{nazwa}” ma {len(tekst)} znaków (limit {LIMIT.get(nazwa, 120)}) — skróć je.")
    if klucz and klucz in tekst:
        blad(f"pole „{nazwa}” zawiera klucz pilota z iPhone'a — usuń go.")
    for wzor, co in WRAZLIWE:
        if wzor.search(tekst):
            blad(f"pole „{nazwa}” wygląda, jakby zawierało {co} — usuń to i opisz własnymi słowami.")


def z_paczki(plik):
    """Name, version and id from manifest.json in the zip. The zip path itself is never printed."""
    try:
        with zipfile.ZipFile(plik) as z:
            man = json.loads(z.read("manifest.json"))
        if not isinstance(man, dict):
            raise ValueError("manifest.json nie jest obiektem")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as e:
        blad(f"nie mogę odczytać manifest.json z paczki ({type(e).__name__}). Podaj --gra i --wersja ręcznie.", 3)
    return str(man.get("name") or ""), str(man.get("version") or ""), str(man.get("id") or "")


def dane(a):
    gra, wersja, id_ = z_paczki(a.paczka) if a.paczka else ("", "", "")
    gra, wersja, id_ = (a.gra or gra).strip(), (a.wersja or wersja).strip(), (a.id or id_).strip()
    if not gra or not wersja:
        blad("brak nazwy gry albo wersji paczki — podaj paczkę .zip albo --gra i --wersja.")
    if id_ and not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", id_):
        blad("id paczki ma dziwne znaki — skopiuj je dokładnie z manifest.json albo pomiń --id.")
    klucz = klucz_pilota()
    napisy, uwagi = a.napisy.strip(), (a.uwagi or "").strip()
    if not napisy:
        blad("opisz ustawienia napisów w grze (--napisy), np. „rozmiar mały, tło 100 %”.")
    # every field that is published goes through the same checks (a pack's manifest is not ours either)
    for nazwa, tekst in (("gra", gra), ("wersja", wersja), ("id", id_), ("napisy", napisy), ("uwagi", uwagi)):
        sprawdz_tekst(nazwa, tekst, klucz)
    if a.paczka:  # the zip's own file name may be personal ("Jan_Kowalski.zip") — it must not slip into the text
        plik = os.path.splitext(os.path.basename(a.paczka))[0].lower()
        if len(plik) >= 8 and plik != gra.lower() and any(plik in t.lower() for t in (napisy, uwagi)):
            blad("w tekście jest nazwa pliku paczki z Twojego dysku — usuń ją.")
    return {"gra": gra, "wersja": wersja, "id": id_, "wynik": a.wynik, "obraz": a.obraz,
            "napisy": napisy, "uwagi": uwagi}


def tresc(d):
    return "\n".join([
        f"Gra: {d['gra']}",
        f"Wersja paczki: {d['wersja']}",
        f"Id paczki: {d['id'] or '(puste)'}",
        f"Wynik próby: {WYNIKI[d['wynik']][0]}",
        f"Skąd obraz gry: {OBRAZY[d['obraz']][0]}",
        f"Ustawienia napisów: {d['napisy']}",
        f"Uwagi: {d['uwagi'] or '(brak)'}",
    ])


def adres(d):
    pola = {"template": SZABLON,
            "title": f"[gra] {d['gra']} {d['wersja']} — {WYNIKI[d['wynik']][1]}",
            "gra": d["gra"], "wersja": d["wersja"], "id": d["id"],
            "wynik": WYNIKI[d["wynik"]][0], "obraz": OBRAZY[d["obraz"]][0],
            "napisy": d["napisy"], "uwagi": d["uwagi"]}
    pola = {k: v for k, v in pola.items() if v}
    return f"https://github.com/{REPO}/issues/new?" + urllib.parse.urlencode(pola, quote_via=urllib.parse.quote)


def wpis(d, dzis=None):
    if d["wynik"] != "dziala" or d["obraz"] not in OBRAZ_WPIS:
        blad("do sprawdzone.json trafia tylko wynik „Działa” na Xboxie (gra zdalna albo karta) — wynik negatywny "
             "albo inne źródło obrazu wyślij formularzem (bez --wpis / --dopisz).")
    if not d["id"]:
        blad("wpis do sprawdzone.json potrzebuje id paczki — podaj paczkę .zip albo --id.")
    return d["id"], {"nazwa": d["gra"], "wersja": d["wersja"], "rodzaj": "na_zywo",
                     "data": dzis or datetime.date.today().isoformat(),
                     "jak": f"{OBRAZY[d['obraz']][1]}, zgłoszenie użytkownika",
                     "obraz": OBRAZ_WPIS[d["obraz"]], "napisy": d["napisy"], "zrodlo": "zgloszenie"}


def zapisz_sprawdzone(dowody, plik=None):
    """Same layout as the hand-written file: one record per line, so a Pull Request diff shows one line.
    plik=None -> SPRAWDZONE read at call time (a default bound at import would ignore a test's substitute)."""
    plik = plik or SPRAWDZONE
    linie = [f" {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}" for k, v in dowody.items()]
    with open(plik, "w", encoding="utf-8") as f:
        f.write("{\n" + ",\n".join(linie) + "\n}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Zgłoszenie wyniku gry (tylko za zgodą użytkownika).")
    ap.add_argument("paczka", nargs="?", help="paczka .zip (czytam z niej tylko manifest.json)")
    ap.add_argument("--gra", help="nazwa gry, gdy bez paczki")
    ap.add_argument("--wersja", help="wersja paczki, gdy bez paczki")
    ap.add_argument("--id", help="id paczki z manifest.json, gdy bez paczki")
    ap.add_argument("--wynik", required=True, choices=sorted(WYNIKI))
    ap.add_argument("--obraz", required=True, choices=sorted(OBRAZY))
    ap.add_argument("--napisy", required=True, help="ustawienia napisów w grze, które zadziałały")
    ap.add_argument("--uwagi", default="", help="krótko, bez danych osobowych")
    ap.add_argument("--tresc-do", metavar="PLIK",
                    help="zapisz treść zgłoszenia też do pliku (dla `gh pr create --body-file`)")
    tryb = ap.add_mutually_exclusive_group()
    tryb.add_argument("--wpis", action="store_true", help="wypisz rekord do sprawdzone.json (tylko „działa”)")
    tryb.add_argument("--dopisz", action="store_true", help="dopisz rekord do sprawdzone.json (ścieżka Pull Request)")
    a = ap.parse_args(argv)
    if a.dopisz and not os.path.exists(USTAWIENIA):  # a fresh fork clone has no settings: the key check would be blind
        blad("--dopisz bez pliku ustawień — ustaw LEKTOR_USTAWIENIA na ustawienia.json z folderu instalacji, "
             "inaczej nie sprawdzę, czy w tekście nie ma klucza pilota.")
    d = dane(a)
    print("=== TREŚĆ ZGŁOSZENIA — pokaż ją użytkownikowi dosłownie, zanim cokolwiek wyślesz ===")
    print(tresc(d))
    print("=== Zgłoszenie jest publiczne i widać je pod loginem GitHub osoby, która je wysyła. ===")
    if a.tresc_do:  # the PR body from a file: user text never goes through a shell's double quotes
        with open(a.tresc_do, "w", encoding="utf-8") as f:
            f.write(tresc(d) + "\n")
    if a.wpis or a.dopisz:
        k, v = wpis(d)
        print(f"\n=== REKORD DO sprawdzone.json (klucz = id paczki) ===\n{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}")
        if a.dopisz:
            with open(SPRAWDZONE, encoding="utf-8") as f:
                dowody = json.load(f)
            stary = dowody.get(k)
            if isinstance(stary, dict) and stary.get("wersja") == v["wersja"] and stary.get("rodzaj") == "na_zywo":
                blad(f"ta wersja paczki jest już sprawdzona na żywo ({stary.get('jak', '')}) — nic nie zmieniam; "
                     f"wynik wyślij formularzem, jeśli coś dodaje.")
            if k in dowody:  # a newer version replaces the record in place: the diff is one changed line
                dowody[k] = v
            else:  # a new game goes right under "_opis": the diff is one added line (no comma moves)
                reszta = [(x, y) for x, y in dowody.items() if x != "_opis"]
                dowody = dict(([("_opis", dowody["_opis"])] if "_opis" in dowody else []) + [(k, v)] + reszta)
            zapisz_sprawdzone(dowody)
            print("\nDopisane do sprawdzone.json" + (f" (zastąpiło wpis wersji {stary.get('wersja')})"
                                                  if isinstance(stary, dict) else "") + ".")
        return 0
    url = adres(d)
    print("\n=== ADRES FORMULARZA (otwórz dopiero po „tak” użytkownika) ===")
    print(url)
    if len(url) > DLUGI_ADRES:
        print(f"\n!!! Adres ma {len(url)} znaków — może się nie otworzyć; użyj zapasowego niżej.")
    # GitHub documents prefill for text fields only (not dropdowns); a refused link shows 404 / 414
    print(f"\n=== ZAPAS (gdy formularz się nie otworzy albo pola są puste): pusty formularz + wklej treść wyżej ===\n"
          f"https://github.com/{REPO}/issues/new?template={SZABLON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
