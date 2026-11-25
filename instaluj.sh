#!/bin/bash
# Lektor gier — one-time setup: Python environment (.venv), packages, the app "Lektor Gier na konsoli" in /Applications.
# Run in Terminal from this folder:  ./instaluj.sh
set -u
cd "$(dirname "$0")" || exit 1
echo "== Lektor gier: instalacja =="
[ "$(uname)" = "Darwin" ] || { echo "To narzędzie działa tylko na macOS."; exit 1; }

# 1. Python 3.10+ (Homebrew first; the system /usr/bin/python3 is often too old)
PY=""
for kand in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$kand" >/dev/null 2>&1 && "$kand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$(command -v "$kand")"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Brak Pythona 3.10 lub nowszego. Zainstaluj go (np. 'brew install python' albo python.org) i uruchom ten skrypt ponownie."
  exit 2
fi
echo "Python: $PY ($("$PY" --version 2>&1))"

# 2. Environment + packages
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv || { echo "Nie udało się utworzyć .venv"; exit 3; }
fi
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt || { echo "Instalacja pakietów nie powiodła się (szczegóły wyżej)."; exit 4; }
.venv/bin/python -I -c "import Vision, Quartz, cv2, rapidfuzz, sounddevice, numpy; print('Pakiety: OK')" || exit 4

# 3. Optional: ffmpeg (demo video window, list of capture cards)
command -v ffplay >/dev/null 2>&1 || command -v /opt/homebrew/bin/ffplay >/dev/null 2>&1 \
  || echo "(opcjonalnie) Brak ffmpeg — demo zagra bez okienka z filmem. Instalacja: brew install ffmpeg"

# 4. The app with an icon
aplikacja/zbuduj.sh "${LEKTOR_APP:-/Applications/Lektor Gier na konsoli.app}" || { echo "Budowa aplikacji nie powiodła się."; exit 5; }

# 5. Voice pack
mkdir -p paczki
if ls paczki/*.zip >/dev/null 2>&1; then
  echo "Paczka lektora: $(ls paczki/*.zip | head -1)"
else
  echo
  echo ">> Nie masz jeszcze żadnej gry. Pobierz paczkę lektora swojej gry (GameReader 2.0) z https://gamereader.pl"
  echo "   i wrzuć plik .zip do folderu gier: $(pwd)/paczki — gra pojawi się w panelu sama."
  open paczki 2>/dev/null
fi
echo
echo "Gotowe. Uruchom lektora ikonką „Lektor Gier na konsoli” w Aplikacjach (albo Launchpadzie). Pierwsze uruchomienie: zobacz README → „Pozwolenia macOS”."
