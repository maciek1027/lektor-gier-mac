# AGENTS.md — manual dla AI (Claude Code, Codex i inne)

Użytkownik dał Ci to repo, żebyś **zainstalował i uruchomił lektora gier na jego Macu** (program i aplikacja
„Lektor Gier na konsoli”; dawna nazwa aplikacji: „GTA Lektor” — jeśli stoi w `/Applications`, nowa jej nie zastępuje,
użytkownik usuwa ją sam po pierwszym uruchomieniu nowej). Zwykle nie jest programistą:
rozmawiaj z nim po polsku, prostym językiem, jeden krok naraz. Wszystko, co da się zrobić poleceniem, robisz Ty.
Użytkownik robi tylko trzy rzeczy, których AI zrobić nie może: **klika zgody macOS**, **pobiera paczkę lektora**
i **włącza grę / napisy w grze**. Paczki lektora wybiera i pobiera człowiek — przyciskiem „Pobierz” w sekcji
„Biblioteka GameReadera” panelu (program sam ściąga i sprawdza paczkę) albo ręcznie ze strony. Ty paczek nie pobierasz.

## Co to jest (żebyś rozumiał, co instalujesz)

Polski lektor do gier na Macu, ułożony jak katalog: każda paczka lektora w folderze gier to kafelek z okładką.
Pierwsza gra sprawdzona na żywo na Xboxie: GTA VI; **na PlayStation 5 program nie był jeszcze sprawdzony**. Czytnik co ~250 ms
robi zrzut obszaru napisów (ekran Maca z grą zdalną konsoli — Xbox w Chrome albo PlayStation 5 w aplikacji Sony PS Remote Play —
albo obraz z karty przechwytującej), rozpoznaje tekst wbudowanym OCR macOS (Vision, `pl-PL`), dopasowuje go do
`subtitles.json` z paczki wybranej gry (format społeczności GameReader 2.0) i odtwarza gotowe nagranie `audio/<id>.opus` z tej paczki.
Nic nie jest syntezowane na żywo. Panel sterowania to strona HTML z małego serwera na Macu; iPhone może być pilotem
i głośnikiem lektora.

| Plik | Rola |
|---|---|
| `lektor.py` | silnik: paczka, dopasowanie, OCR, źródła obrazu, odtwarzanie (`--help`) |
| `serwer_panelu.py` | serwer panelu i pilota, port 8797 (stdlib `ThreadingHTTPServer`) |
| `panel/index.html` | cały interfejs (Mac i telefon w jednym pliku) |
| `aplikacja/zbuduj.sh` | buduje `/Applications/Lektor Gier na konsoli.app` + ikonki PWA (z `aplikacja/ikona-zrodlo.jpg` przez `ikona.py`) |
| `aplikacja/start-panel.command` | uruchamia serwer w oknie Terminala (aplikacja go woła) |
| `instaluj.sh` | cała instalacja: `.venv`, pakiety, aplikacja, folder `paczki/` |
| `Lektor Gier na konsoli.command` | stare menu w Terminalu (demo, karta, podgląd obszaru) |
| `paczki/` | folder gier: paczki `.zip` i opcjonalny film demo `.mp4` (poza gitem) |

## Instalacja — krok po kroku

1. **Sprawdź Maca:** `sw_vers -productVersion`, `uname -m`. Testowane na macOS 27 / Apple M4. Polski OCR wymaga
   nowego macOS; jeśli test w kroku 6 nic nie rozpozna na starszym systemie, powiedz to wprost.
2. **Python 3.10+:** `instaluj.sh` sam go szuka. Gdy brak — zaproponuj `brew install python` (jeśli jest Homebrew)
   albo instalator z python.org. Nie używaj systemowego `/usr/bin/python3`, jeśli jest starszy niż 3.10.
3. **Uruchom instalator:** `./instaluj.sh` (z folderu repo). Tworzy `.venv`, instaluje `requirements.txt`, sprawdza
   import, buduje aplikację „Lektor Gier na konsoli” w `/Applications`. Kod wyjścia ≠ 0 → przeczytaj komunikat, napraw, powtórz.
   Opcjonalnie `brew install ffmpeg` (okienko z filmem w demo, lista kart przechwytujących).
4. **Paczka lektora (robi użytkownik):** zapytaj, w jaką grę gra, i pokaż mu w panelu sekcję **Biblioteka
   GameReadera** (pod „Moje gry”): szukajka → „Pobierz” → gra sama trafia do „Moje gry” (program sprawdza rozmiar,
   cały ZIP i budowę paczki; nic nie zmienia w jej treści; na Macu obok otwiera się strona biblioteki GameReadera;
   gdy biblioteka w programie nie działa, panel sam pokazuje drogę ręczną). Gry oznaczone „Na razie nie na Macu” nie mają obszaru
   napisów — nie zadziałają. Paczka spoza biblioteki: pobranie ze strony https://gamereader.pl i wrzucenie `.zip`
   do folderu `paczki/` (`open paczki`; w panelu: kafelek „＋ Dodaj grę” → „Otwórz folder gier”). Pobieranie
   z programu wyłącza `LEKTOR_POBIERANIE=0` w `lokalne.env`. Nie rozpakowuj — program czyta prosto z ZIP-a. Paczka
   należy do jej autorów: nie wrzucaj jej do gita ani nigdzie indziej. Gra pojawia się w katalogu panelu sama
   (ok. 5 s). Sprawdzenie z wiersza poleceń:
   `.venv/bin/python -c "import lektor as L; p=L.Paczka(L.znajdz('.zip','LEKTOR_PACZKA')); print(len(p.ids))"`
   (np. paczka GTA VI v4: ok. 373 tys. kwestii). Przy każdej grze panel pokazuje, jak bardzo jest sprawdzona
   („Sprawdzona na Xboxie”, „Próba na nagraniu gry”, „Może gubić kwestie”…) — powtórz to użytkownikowi uczciwie.
5. **Pierwsze uruchomienie:** użytkownik klika „Lektor Gier na konsoli” w Aplikacjach. Otwiera się okno Terminala z serwerem
   (ma zostać otwarte, można zminimalizować) i panel w oknie Chrome. Wymaga **Google Chrome**.
6. **Test bez gry:** jeśli w `paczki/` jest film `.mp4` z napisami gry, przycisk „Demo” na ekranie gry zagra lektora
   (bez filmu: „Posłuchaj próbki” odtwarza kilka nagrań z paczki).
   Test z wiersza poleceń (bez dźwięku): `.venv/bin/python lektor.py --wideo paczki/<film>.mp4 --szybko --bez-dzwieku`
   — wypisuje rozpoznane kwestie (`🎙`). Zero kwestii przy filmie z napisami = problem z OCR albo obszarem napisów.

## Pozwolenia macOS (klika użytkownik — powiedz mu dokładnie, co zobaczy)

| Kiedy | Okienko macOS | Co kliknąć |
|---|---|---|
| pierwsze „Graj” (gra zdalna) | „Terminal” chce nagrywać ekran | Zezwól → Ustawienia systemowe → Prywatność i ochrona → **Nagrywanie ekranu i dźwięku z systemu** → włącz **Terminal**; potem zamknij panel i otwórz aplikację ponownie (zgoda działa po restarcie procesu) |
| pierwsze uruchomienie aplikacji | „Lektor Gier na konsoli” chce dostęp do folderu (np. Dokumenty / Pobrane) | Zezwól |
| aplikacja steruje Terminalem | „Lektor Gier na konsoli” chce sterować „Terminal” | OK |
| karta przechwytująca | „Terminal” chce dostęp do kamery | Zezwól (karta udaje kamerę) |
| PS5: pierwsze uruchomienie PS Remote Play | okienka aplikacji Sony | prowadzi aplikacja Sony; loguje się użytkownik — Ty nie wpisujesz hasła |
| PS5 przez kartę przechwytującą | (to nie okienko macOS) karta pokazuje czarny obraz | na konsoli: Ustawienia → System → HDMI → **Włącz HDCP** — wyłącz (robi użytkownik) |
| pilot w iPhonie przy włączonej zaporze | „Python” chce przyjmować połączenia przychodzące | Zezwól |

Zgodę na nagrywanie ekranu dostaje **Terminal**, bo to on uruchamia serwer (`start-panel.command`). Brak zgody
widać w panelu jako żółty pasek z przyciskiem „Otwórz ustawienia”. Nie zmieniaj ustawień systemu za użytkownika.

## Ustawienia w grze i na konsoli (powiedz użytkownikowi)

- Napisy w grze **włączone**; rozmiar i tło według autora paczki — ekran gry w panelu pokazuje jego słowa („Ustaw
  w grze”). Przykład GTA VI: rozmiar **mały** (obszar napisów w paczce jest dobrany pod mały rozmiar), autorzy zalecają
  tło napisów na 100 %. U autora programu OCR działał także bez tła — gdy lektor gubi kwestie na jasnych scenach,
  każ włączyć tło.
- Konsola gry zdalnej: panel → Ustawienia → Więcej opcji → **Konsola** (Xbox / PlayStation 5); w `ustawienia.json`
  pole `konsola` (`xbox` — domyślnie, także dla starego pliku bez tego pola — albo `playstation`). „Graj” (na Macu
  i z pilota) otwiera grę zdalną konsoli z ustawień; karta przechwytująca nie zależy od tego pola.
- Gra zdalna z Xboxa: Chrome → https://www.xbox.com/pl-PL/play/consoles, okno gry na **pełnym ekranie** Maca (czytnik
  zakłada obraz 16:9 na całym ekranie).
- Dźwięk gry na telewizorze przy grze zdalnej z Xboxa: Xbox przerzuca wtedy dźwięk na Maca. Pomaga: Xbox → Ustawienia →
  Ogólne → Głośność i wyjście audio → Dodatkowe opcje → **odznacz** „Wycisz dźwięk głośnika, gdy podłączony jest
  zestaw słuchawkowy”; w Chrome wycisz kartę Xbox (prawy klik → „Wycisz witrynę”).
- Gra zdalna z PlayStation 5: **nie Chrome**, tylko aplikacja Sony **PS Remote Play** (program szuka
  `/Applications/PS Remote Play.app` albo `~/Applications/PS Remote Play.app`). Brak aplikacji → „Graj” odmawia
  z komunikatem (program sprawdza tylko te dwie ścieżki); pobiera i instaluje ją użytkownik ze strony Sony, łączy z konsolą i loguje się sam. Okno aplikacji
  na **pełnym ekranie** Maca.
- PS5 przez kartę przechwytującą: wymaga wyłączenia HDCP na konsoli (Ustawienia → System → HDMI → Włącz HDCP:
  wyłącz) — inaczej karta widzi czarny obraz. Treści wymagające HDCP nie zadziałają wtedy na konsoli — po graniu włącz HDCP z powrotem.
- **Uczciwie:** na PS5 program nie był sprawdzony — obszary napisów w paczkach robiono pod grę na PC. Gubi kwestie
  albo milczy → najpierw pełny ekran i napisy w grze, potem podgląd obszaru (niżej, „Lektor milczy w grze”), a wynik
  zbierz do cegiełki (`--obraz inne`).

## Pilot w iPhonie (opcjonalnie)

Panel na Macu → Ustawienia → karta „📱 Pilot w iPhonie” → kod QR → aparat iPhone'a → Safari → Udostępnij → „Do ekranu
początkowego”. Telefon w tym samym Wi-Fi. Profil „iPhone” gra lektora z telefonu (też przy zablokowanym ekranie;
pilot ma zostać otwarty). Z sieci serwer wpuszcza tylko z tajnym kluczem (pole `klucz` w `ustawienia.json`) —
**nigdy go nie pokazuj, nie commituj, nie wklejaj do logów**; „Nowy kod” w panelu go unieważnia.

## Gdy coś nie działa

- **Log:** `~/Library/Logs/gta-lektor.log` (zawsze zacznij od niego). Linie `📱` = telefon, `🎙` = rozpoznana kwestia,
  `?` = napis bez dopasowania, `!` = ostrzeżenie.
- **Lektor milczy w grze:** `Lektor Gier na konsoli.command` → opcja 4 zapisuje `podglad.png` i `podglad_obszar.png` — w drugim
  muszą być napisy. Nie ma → gra nie na pełnym ekranie albo brak zgody na nagrywanie ekranu.
- **Port 8797 zajęty:** serwer już działa (`lsof -iTCP:8797 -sTCP:LISTEN`); otwórz `http://127.0.0.1:8797/`.
- **Biblioteka:** „Nie mogę pobrać listy gier” = brak internetu albo serwer GameReadera nie odpowiada (pokazuje
  wtedy ostatnią zapisaną listę z datą); „Podłącz dysk” = folder gier leży na odłączonym dysku; „Za mało miejsca”
  = paczka + 200 MB zapasu nie mieszczą się na dysku z grami. Niedokończone pobieranie to plik `*.zip.czesc` —
  program usuwa go sam przy następnym starcie albo pobieraniu (gdy leży nietknięty ponad 10 min).
- **Paczka:** katalog pusty albo komunikat „Nie widzę paczki wybranej gry” → `.zip` nie leży w folderze gier
  (`paczki/` albo `LEKTOR_PACZKI` z `lokalne.env`); „Nie widzę dysku” → dysk zewnętrzny z paczkami odłączony.
- **Wyłączanie:** przycisk „Wyłącz program” w panelu kończy serwer (także gdy połączony jest pilot). Bez niego
  serwer kończy się po zamknięciu okna panelu, a przy połączonym pilocie dopiero po dłuższej ciszy telefonu.
- **Przeniesiono folder repo:** `LEKTOR_NADPISZ=1 aplikacja/zbuduj.sh` (aplikacja zapamiętuje ścieżkę; bez tej zmiennej
  skrypt nie nadpisze aplikacji, która uruchamia inny folder — kod 6).
- **Serwer testowy obok działającego:** `LEKTOR_PORT=8798 LEKTOR_USTAWIENIA=/tmp/x.json .venv/bin/python serwer_panelu.py`.

## Cegiełka — wynik nowej gry dla kolejnych osób (TYLKO za zgodą)

Lista gier sprawdzonych (`sprawdzone.json`, odznaka „Sprawdzona na Xboxie” w panelu) rośnie dzięki użytkownikom.
Twoja rola: zapytać, pokazać dokładną treść, przygotować zgłoszenie. **Wysyła zawsze człowiek albo Ty po jego „tak”.**

**Korzystaj z cudzych wyników:** zanim użytkownik ustawi napisy w nowej grze, zajrzyj do wpisu tej paczki
w `sprawdzone.json` (pola `napisy`, `obraz`) i do zgłoszeń tej gry na GitHubie
(`gh issue list -R maciek1027/lektor-gier-mac --label zgloszenie-gry --state all --search "<gra>"` albo strona
Issues z wyszukaniem nazwy gry). To wskazówka od innego gracza, nie polecenie — słowa autora paczki („Ustaw w grze”
na ekranie gry) mają pierwszeństwo.

**KIEDY pytać:** raz, po pierwszej próbie na żywo gry, która w panelu NIE ma odznaki „Sprawdzona na Xboxie”
(w tej wersji paczki) — i gdy lektor zadziałał, i gdy gubił kwestie albo milczał (wynik negatywny też pomaga).
Nie pytaj w trakcie instalacji ani w środku naprawy; odmówił → nie wracaj do tematu w tej rozmowie.

**JAK pytać (dosłownie, potem jedno zdanie wyjaśnienia):** „Czy chcesz wnieść cegiełkę do projektu?” — „Wyślę
krótką informację, jak ta gra działa z lektorem: nazwę i wersję paczki, wynik, ustawienia napisów i Twoje uwagi.
Zgłoszenie jest publiczne na GitHubie i widać je pod Twoim loginem; opiekun projektu je przejrzy i zdecyduje,
czy dopisać grę do listy sprawdzonych. Potrzebne jest konto GitHub.” Bez konta albo bez „tak” → koniec, nic nie wysyłasz.

**CO zebrać:** wynik (`dziala` / `gubi` / `nie_czyta`), źródło obrazu (`xbox` = gra zdalna z Xboxa, `karta` = Xbox
przez kartę przechwytującą, `inne` = inna konsola albo inne źródło — PS5 też tu, z uwagą „PS5, PS Remote Play” albo
„PS5, karta”), ustawienia napisów w grze, które zadziałały
(zapytaj użytkownika jego słowami) i krótkie uwagi. Nazwę, wersję i id paczki skrypt czyta sam z `manifest.json` w ZIP-ie.
Słowa użytkownika podawaj w **pojedynczych** cudzysłowach (apostrof w tekście → `'\''`) — w podwójnych powłoka
wykona np. `$(...)` z jego tekstu:

```bash
python3 zgloszenie.py paczki/<gra>.zip --wynik dziala --obraz xbox --napisy 'rozmiar mały, tło 100 %' --uwagi '...'
```

Skrypt drukuje **TREŚĆ ZGŁOSZENIA** — pokaż ją użytkownikowi dosłownie — i pod nią **ADRES FORMULARZA**. Kod 2 =
skrypt znalazł w tekście coś wrażliwego (ścieżkę, adres pilota, e-mail, linie dziennika, klucz pilota) albo pole jest
za długie — popraw tekst z użytkownikiem, nie omijaj sprawdzenia.

**Droga A — formularz (domyślna, bez żadnych narzędzi):** po „tak” otwórz adres (`open "<adres>"`). GitHub pokaże
formularz „Zgłoszenie gry” z wypełnionymi polami; użytkownik loguje się, sprawdza, zaznacza pole „Sprawdziłem treść”
i sam klika przycisk wysłania (**„Create”** / „Submit new issue”). GitHub gwarantuje wypełnianie linkiem tylko pól
tekstowych — jeśli listy „Wynik próby” albo „Skąd obraz” są puste, powiedz, co wybrać. Strona 404 / 414, adres za
długi albo puste pola → adres **ZAPAS** ze skryptu (pusty formularz) i przepisanie treści.

**Droga B — Pull Request (tylko z `gh` zalogowanym na koncie użytkownika i tylko dla wyniku „Działa” na Xboxie):**
fork zakłada kopię repo NA KONCIE użytkownika — to osobne pytanie: „Założę kopię projektu na Twoim koncie GitHub
i wyślę z niej propozycję zmiany. Zgoda?”. Po „tak”:

```bash
INSTALACJA="$PWD"                       # folder, w którym działa lektor użytkownika (tu leży ustawienia.json)
cd "$(mktemp -d)" && gh repo fork maciek1027/lektor-gier-mac --clone --default-branch-only && cd lektor-gier-mac
git switch -c gra-<krotka-nazwa>
LEKTOR_USTAWIENIA="$INSTALACJA/ustawienia.json" python3 zgloszenie.py "$INSTALACJA/paczki/<gra>.zip" \
  --wynik dziala --obraz xbox --napisy '...' --dopisz --tresc-do ../tresc.txt
python3 -m unittest testy/test_zgloszenie.py
git diff                                # ma być DOKŁADNIE jedna dodana albo zmieniona linia w sprawdzone.json
# STOP: pokaż użytkownikowi TREŚĆ ZGŁOSZENIA i tę linię; wysyłasz dopiero po jego „tak”
git add sprawdzone.json && git commit -m "sprawdzone: <gra> <wersja> (zgłoszenie)"
git push -u origin HEAD
gh pr create --title "sprawdzone: <gra> <wersja>" --body-file ../tresc.txt
```

Klon idzie do folderu tymczasowego, nie do folderu instalacji. `LEKTOR_USTAWIENIA` jest konieczne: w świeżym klonie
nie ma `ustawienia.json`, więc bez tej zmiennej skrypt nie sprawdzi, czy w tekście nie ma klucza pilota (zmienna
wskazująca nieistniejący plik = kod 2). Opis Pull Requesta idzie z pliku (`--body-file`), nie przez cudzysłowy powłoki.
`gh pr create` w klonie forka celuje w repo projektu sam (gh ustawia je jako domyślne przy `fork --clone`).

Wynik negatywny (`gubi`, `nie_czyta`) i źródło `inne` idą **tylko formularzem** — skrypt odmówi `--dopisz`, bo panel
pokazałby przy takim wpisie „Sprawdzona na Xboxie”.

**Format wpisu `sprawdzone.json`** (klucz = `id` z `manifest.json`; jeden wpis na paczkę, nowsza wersja ZASTĘPUJE
starszą, bo odznaka liczy się tylko przy zgodnej wersji; jedna linia na wpis):

```json
"<id paczki>": {"nazwa": "Grand Theft Auto V", "wersja": "1.0.1", "rodzaj": "na_zywo", "data": "2026-11-25", "jak": "gra zdalna z Xboxa, zgłoszenie użytkownika", "obraz": "xbox_zdalnie", "napisy": "rozmiar mały, tło 100 %", "zrodlo": "zgloszenie"}
```

`rodzaj`: `na_zywo` (prawdziwa gra na konsoli) albo `nagranie` (próba opiekuna na nagraniu gry — nie dla zgłoszeń).
`jak` panel pokazuje wszystkim przy odznace — krótko, bez nazwisk i loginów. Opcjonalnie `zgloszenie` (numer zgłoszenia,
np. `"#12"`). Pilnuje tego `testy/test_zgloszenie.py` (uruchamia go też GitHub przy każdym Pull Requeście).

**NIGDY:** nie wysyłaj bez „tak” · nie wklejaj klucza pilota, ścieżek z dysku, dziennika, zrzutów ekranu, adresu IP,
imienia ani e-maila · nie dołączaj paczki ani jej plików · nie klikaj „Submit” za użytkownika w jego przeglądarce.

## Zasady dla Ciebie

- Nie commituj: `paczki/*` (poza `CZYTAJ.txt`), `ustawienia.json`, `podglad*.png`, `.venv/` — pilnuje `.gitignore`.
- Odtwarzanie na Macu idzie JEDNYM stale otwartym strumieniem (`lektor.Glos`) — nie zamieniaj go na `afplay` na
  kwestię: nagrania są przycięte na styk i otwieranie dźwięku od nowa zjada sylaby.
- Zmieniasz `lektor.py` → test regresji na filmie demo (liczba kwestii nie może spaść).
- Nie twierdź, że coś działa, jeśli tego nie uruchomiłeś: test z filmem, log, panel w przeglądarce.
