# AGENTS.md — manual dla AI (Claude Code, Codex i inne)

Użytkownik dał Ci to repo, żebyś **zainstalował i uruchomił lektora gier na jego Macu** (program „Lektor Gier na
konsoli”; aplikacja w `/Applications` nazywa się na razie „GTA Lektor” — dawna nazwa, zostaje, bo wskazują na nią
ikonka w Docku i skrypty). Zwykle nie jest programistą:
rozmawiaj z nim po polsku, prostym językiem, jeden krok naraz. Wszystko, co da się zrobić poleceniem, robisz Ty.
Użytkownik robi tylko trzy rzeczy, których AI zrobić nie może: **klika zgody macOS**, **pobiera paczkę lektora**
i **włącza grę / napisy w grze**. Pobieranie paczek z poziomu programu jeszcze nie istnieje — paczki pobiera człowiek.

## Co to jest (żebyś rozumiał, co instalujesz)

Polski lektor do gier na Macu, ułożony jak katalog: każda paczka lektora w folderze gier to kafelek z okładką.
Pierwsza gra sprawdzona na żywo na Xboxie: GTA VI. Czytnik co ~250 ms robi zrzut obszaru napisów (ekran Maca z grą zdalną Xboxa
albo obraz z karty przechwytującej), rozpoznaje tekst wbudowanym OCR macOS (Vision, `pl-PL`), dopasowuje go do
`subtitles.json` z paczki wybranej gry (format społeczności GameReader 2.0) i odtwarza gotowe nagranie `audio/<id>.opus` z tej paczki.
Nic nie jest syntezowane na żywo. Panel sterowania to strona HTML z małego serwera na Macu; iPhone może być pilotem
i głośnikiem lektora.

| Plik | Rola |
|---|---|
| `lektor.py` | silnik: paczka, dopasowanie, OCR, źródła obrazu, odtwarzanie (`--help`) |
| `serwer_panelu.py` | serwer panelu i pilota, port 8797 (stdlib `ThreadingHTTPServer`) |
| `panel/index.html` | cały interfejs (Mac i telefon w jednym pliku) |
| `aplikacja/zbuduj.sh` | buduje `/Applications/GTA Lektor.app` + ikonki PWA |
| `aplikacja/start-panel.command` | uruchamia serwer w oknie Terminala (aplikacja go woła) |
| `instaluj.sh` | cała instalacja: `.venv`, pakiety, aplikacja, folder `paczki/` |
| `Lektor GTA6.command` | stare menu w Terminalu (demo, karta, podgląd obszaru) |
| `paczki/` | folder gier: paczki `.zip` i opcjonalny film demo `.mp4` (poza gitem) |

## Instalacja — krok po kroku

1. **Sprawdź Maca:** `sw_vers -productVersion`, `uname -m`. Testowane na macOS 27 / Apple M4. Polski OCR wymaga
   nowego macOS; jeśli test w kroku 6 nic nie rozpozna na starszym systemie, powiedz to wprost.
2. **Python 3.10+:** `instaluj.sh` sam go szuka. Gdy brak — zaproponuj `brew install python` (jeśli jest Homebrew)
   albo instalator z python.org. Nie używaj systemowego `/usr/bin/python3`, jeśli jest starszy niż 3.10.
3. **Uruchom instalator:** `./instaluj.sh` (z folderu repo). Tworzy `.venv`, instaluje `requirements.txt`, sprawdza
   import, buduje aplikację „GTA Lektor” w `/Applications`. Kod wyjścia ≠ 0 → przeczytaj komunikat, napraw, powtórz.
   Opcjonalnie `brew install ffmpeg` (okienko z filmem w demo, lista kart przechwytujących).
4. **Paczka lektora (robi użytkownik):** zapytaj, w jaką grę gra, i poproś o pobranie paczki tej gry dla
   GameReadera 2.0 ze strony https://gamereader.pl oraz wrzucenie `.zip` do folderu `paczki/` (`open paczki`;
   w panelu: kafelek „＋ Dodaj grę” → „Otwórz folder gier”). Nie rozpakowuj — program czyta prosto z ZIP-a. Paczka
   należy do jej autorów: nie wrzucaj jej do gita ani nigdzie indziej. Gra pojawia się w katalogu panelu sama
   (ok. 5 s). Sprawdzenie z wiersza poleceń:
   `.venv/bin/python -c "import lektor as L; p=L.Paczka(L.znajdz('.zip','LEKTOR_PACZKA')); print(len(p.ids))"`
   (np. paczka GTA VI v4: ok. 373 tys. kwestii). Przy każdej grze panel pokazuje, jak bardzo jest sprawdzona
   („Sprawdzona na Xboxie”, „Próba na nagraniu gry”, „Może gubić kwestie”…) — powtórz to użytkownikowi uczciwie.
5. **Pierwsze uruchomienie:** użytkownik klika „GTA Lektor” w Aplikacjach. Otwiera się okno Terminala z serwerem
   (ma zostać otwarte, można zminimalizować) i panel w oknie Chrome. Wymaga **Google Chrome**.
6. **Test bez gry:** jeśli w `paczki/` jest film `.mp4` z napisami gry, przycisk „Demo” na ekranie gry zagra lektora
   (bez filmu: „Posłuchaj próbki” odtwarza kilka nagrań z paczki).
   Test z wiersza poleceń (bez dźwięku): `.venv/bin/python lektor.py --wideo paczki/<film>.mp4 --szybko --bez-dzwieku`
   — wypisuje rozpoznane kwestie (`🎙`). Zero kwestii przy filmie z napisami = problem z OCR albo obszarem napisów.

## Pozwolenia macOS (klika użytkownik — powiedz mu dokładnie, co zobaczy)

| Kiedy | Okienko macOS | Co kliknąć |
|---|---|---|
| pierwsze „Graj” (gra zdalna) | „Terminal” chce nagrywać ekran | Zezwól → Ustawienia systemowe → Prywatność i ochrona → **Nagrywanie ekranu i dźwięku z systemu** → włącz **Terminal**; potem zamknij panel i otwórz aplikację ponownie (zgoda działa po restarcie procesu) |
| pierwsze uruchomienie aplikacji | „GTA Lektor” chce dostęp do folderu (np. Dokumenty / Pobrane) | Zezwól |
| aplikacja steruje Terminalem | „GTA Lektor” chce sterować „Terminal” | OK |
| karta przechwytująca | „Terminal” chce dostęp do kamery | Zezwól (karta udaje kamerę) |
| pilot w iPhonie przy włączonej zaporze | „Python” chce przyjmować połączenia przychodzące | Zezwól |

Zgodę na nagrywanie ekranu dostaje **Terminal**, bo to on uruchamia serwer (`start-panel.command`). Brak zgody
widać w panelu jako żółty pasek z przyciskiem „Otwórz ustawienia”. Nie zmieniaj ustawień systemu za użytkownika.

## Ustawienia w grze i na Xboxie (powiedz użytkownikowi)

- Napisy w grze **włączone**; rozmiar i tło według autora paczki — ekran gry w panelu pokazuje jego słowa („Ustaw
  w grze”). Przykład GTA VI: rozmiar **mały** (obszar napisów w paczce jest dobrany pod mały rozmiar), autorzy zalecają
  tło napisów na 100 %. U autora programu OCR działał także bez tła — gdy lektor gubi kwestie na jasnych scenach,
  każ włączyć tło.
- Gra zdalna: Chrome → https://www.xbox.com/pl-PL/play/consoles, okno gry na **pełnym ekranie** Maca (czytnik
  zakłada obraz 16:9 na całym ekranie).
- Dźwięk gry na telewizorze przy grze zdalnej: Xbox przerzuca wtedy dźwięk na Maca. Pomaga: Xbox → Ustawienia →
  Ogólne → Głośność i wyjście audio → Dodatkowe opcje → **odznacz** „Wycisz dźwięk głośnika, gdy podłączony jest
  zestaw słuchawkowy”; w Chrome wycisz kartę Xbox (prawy klik → „Wycisz witrynę”).

## Pilot w iPhonie (opcjonalnie)

Panel na Macu → Ustawienia → karta „📱 Pilot w iPhonie” → kod QR → aparat iPhone'a → Safari → Udostępnij → „Do ekranu
początkowego”. Telefon w tym samym Wi-Fi. Profil „iPhone” gra lektora z telefonu (też przy zablokowanym ekranie;
pilot ma zostać otwarty). Z sieci serwer wpuszcza tylko z tajnym kluczem (pole `klucz` w `ustawienia.json`) —
**nigdy go nie pokazuj, nie commituj, nie wklejaj do logów**; „Nowy kod” w panelu go unieważnia.

## Gdy coś nie działa

- **Log:** `~/Library/Logs/gta-lektor.log` (zawsze zacznij od niego). Linie `📱` = telefon, `🎙` = rozpoznana kwestia,
  `?` = napis bez dopasowania, `!` = ostrzeżenie.
- **Lektor milczy w grze:** `Lektor GTA6.command` → opcja 4 zapisuje `podglad.png` i `podglad_obszar.png` — w drugim
  muszą być napisy. Nie ma → gra nie na pełnym ekranie albo brak zgody na nagrywanie ekranu.
- **Port 8797 zajęty:** serwer już działa (`lsof -iTCP:8797 -sTCP:LISTEN`); otwórz `http://127.0.0.1:8797/`.
- **Paczka:** katalog pusty albo komunikat „Nie widzę paczki wybranej gry” → `.zip` nie leży w folderze gier
  (`paczki/` albo `LEKTOR_PACZKI` z `lokalne.env`); „Nie widzę dysku” → dysk zewnętrzny z paczkami odłączony.
- **Wyłączanie:** przycisk „Wyłącz program” w panelu kończy serwer (także gdy połączony jest pilot). Bez niego
  serwer kończy się po zamknięciu okna panelu, a przy połączonym pilocie dopiero po dłuższej ciszy telefonu.
- **Przeniesiono folder repo:** uruchom ponownie `aplikacja/zbuduj.sh` (aplikacja zapamiętuje ścieżkę).
- **Serwer testowy obok działającego:** `LEKTOR_PORT=8798 LEKTOR_USTAWIENIA=/tmp/x.json .venv/bin/python serwer_panelu.py`.

## Zasady dla Ciebie

- Nie commituj: `paczki/*` (poza `CZYTAJ.txt`), `ustawienia.json`, `podglad*.png`, `.venv/` — pilnuje `.gitignore`.
- Odtwarzanie na Macu idzie JEDNYM stale otwartym strumieniem (`lektor.Glos`) — nie zamieniaj go na `afplay` na
  kwestię: nagrania są przycięte na styk i otwieranie dźwięku od nowa zjada sylaby.
- Zmieniasz `lektor.py` → test regresji na filmie demo (liczba kwestii nie może spaść).
- Nie twierdź, że coś działa, jeśli tego nie uruchomiłeś: test z filmem, log, panel w przeglądarce.
