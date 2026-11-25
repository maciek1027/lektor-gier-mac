# Jak pomóc — podziel się wynikiem swojej gry

Lektor działa z paczkami społeczności [GameReader](https://gamereader.pl), ale każda gra zachowuje się trochę
inaczej. Najcenniejsza pomoc to informacja: **czy lektor zadziałał w Twojej grze i z jakimi ustawieniami napisów.**
Dzięki temu następna osoba od razu wie, czego się spodziewać, a gra może dostać w panelu odznakę
„Sprawdzona na Xboxie”.

## Najprościej: niech zrobi to Twoje AI

Jeśli instalowałeś lektora z Claude Code, Codexem albo innym AI — po pierwszej próbie nowej gry zapyta Cię:
**„Czy chcesz wnieść cegiełkę do projektu?”**. Pokaże dokładną treść zgłoszenia i otworzy gotowy formularz.
Ty tylko sprawdzasz i klikasz wysłanie. Bez Twojego „tak” nic nie wychodzi.

## Samodzielnie

Otwórz [formularz zgłoszenia gry](https://github.com/maciek1027/lektor-gier-mac/issues/new?template=zgloszenie-gry.yml)
(potrzebne konto GitHub) i wypełnij:

- nazwę i wersję paczki (wersja jest w pliku `manifest.json` w paczce),
- wynik: działa / gubi kwestie / nie czyta,
- skąd program brał obraz: gra zdalna z Xboxa na Macu, Xbox przez kartę przechwytującą albo inna konsola,
- ustawienia napisów w grze, które zadziałały (np. rozmiar mały, tło 100 %),
- uwagi, jeśli chcesz.

Wynik „nie działa” też się przydaje — mówi, którą grę trzeba poprawić.

## Czego NIE wysyłać

Zgłoszenie jest **publiczne** i podpisane Twoim loginem GitHub. Nie wklejaj: klucza pilota z iPhone'a (ani adresu
pilota), ścieżek z dysku, dziennika programu, zrzutów ekranu z pulpitem, imienia, e-maila. **Nie załączaj paczek
lektora** — należą do ich autorów i nie trafiają do tego repo.

## Dla zaawansowanych: Pull Request

Masz wynik „działa” na Xboxie i umiesz z `git`/`gh`? Dopisz grę do `sprawdzone.json` w swoim forku i wyślij Pull
Request. Wpis przygotuje skrypt (czyta z paczki tylko nazwę, wersję i id):

```bash
LEKTOR_USTAWIENIA=<twoja instalacja>/ustawienia.json \
  python3 zgloszenie.py <paczka>.zip --wynik dziala --obraz xbox --napisy 'rozmiar mały, tło 100 %' --dopisz
python3 -m unittest testy/test_zgloszenie.py
```

(`LEKTOR_USTAWIENIA` pozwala skryptowi sprawdzić, że w tekście nie ma klucza pilota — w forku tego pliku nie ma.)
Pull Request ma zmieniać **jedną linię** w `sprawdzone.json` i nic poza tym.

Format wpisu i pełna procedura: [`AGENTS.md`](AGENTS.md) → „Cegiełka”. GitHub sprawdzi format pliku sam.

## Co dzieje się dalej

Opiekun projektu dostaje powiadomienie, czyta zgłoszenie i decyduje: dopisuje grę do listy sprawdzonych, prosi
o szczegóły albo zostawia wynik jako wskazówkę dla innych. Nowa odznaka pojawia się u wszystkich po aktualizacji
programu.
