#!/bin/bash
# Double-click launcher for the GTA VI Polish voice-over reader (opens in Terminal).
cd "$(dirname "$0")" || exit 1
PY="./.venv/bin/python"
PACZKA="$(ls paczki/*.zip 2>/dev/null | head -1)"   # the GameReader 2.0 voice pack dropped into paczki/
DEMO="$(ls paczki/*.mp4 2>/dev/null | head -1)"     # optional demo video

if [ ! -f "$PACZKA" ]; then
  echo "Nie widzę paczki lektora — pobierz ją z gamereader.pl i wrzuć plik .zip do folderu paczki/."
  read -r -p "Enter zamyka okno."
  exit 1
fi

echo "=== Polski lektor GTA VI ==="
echo " 1) Gra zdalna — obraz gry na ekranie Maca (okno gry na PEŁNYM ekranie)"
echo " 2) Karta przechwytująca"
echo " 3) Demo na nagraniu — usłyszysz lektora od razu"
echo " 4) Podgląd: gdzie czytnik szuka napisów na ekranie"
read -r -p "Wybierz 1-4: " W

case "$W" in
  1)
    echo "Za 5 sekund startuję — przełącz się na okno gry (pełny ekran)."
    sleep 5
    # caffeinate: the Mac screen must not sleep while the reader watches it
    caffeinate -d -i "$PY" lektor.py --paczka "$PACZKA" --ekran
    ;;
  2)
    "$PY" lektor.py --lista-kamer
    read -r -p "Numer karty przechwytującej: " K
    caffeinate -d -i "$PY" lektor.py --paczka "$PACZKA" --kamera "$K"
    ;;
  3)
    [ -f "$DEMO" ] || { echo "Brak nagrania demo — wrzuć film .mp4 z napisami GTA VI do folderu paczki/."; read -r -p "Enter zamyka okno."; exit 1; }
    "$PY" lektor.py --paczka "$PACZKA" --wideo "$DEMO" --pokaz
    ;;
  4)
    echo "Za 5 sekund robię zrzut — przełącz się na okno gry (pełny ekran)."
    sleep 5
    "$PY" lektor.py --paczka "$PACZKA" --ekran --podglad
    ;;
  *)
    echo "Nie ma takiej opcji."
    ;;
esac
read -r -p "Enter zamyka okno."
