#!/bin/bash
# Started by "GTA Lektor.app": runs the panel server in this Terminal window. Terminal already has the
# screen-recording consent, and a process started by the applet itself is killed by macOS when the applet quits.
cd "$(dirname "$0")/.." || exit 1
printf '\033]0;GTA Lektor — serwer panelu\007'
echo "GTA Lektor — panel działa. Nie zamykaj tego okna (możesz je zminimalizować)."
echo "Zamknięcie okna panelu w Chrome wyłączy lektora i ten serwer (chyba że połączony jest pilot w iPhonie)."
./.venv/bin/python serwer_panelu.py 2>&1 | tee -a "$HOME/Library/Logs/gta-lektor.log"
echo "Serwer zakończył pracę — to okno możesz zamknąć."
