#!/bin/bash
# Started by "GTA Lektor.app": runs the panel server in this Terminal window. Terminal already has the
# screen-recording consent, and a process started by the applet itself is killed by macOS when the applet quits.
cd "$(dirname "$0")/.." || exit 1
printf '\033]0;GTA Lektor — serwer lektora gier\007'
echo "Lektor gier — panel działa. Nie zamykaj tego okna (możesz je zminimalizować)."
echo "Wyłączasz przyciskiem „Wyłącz program” w panelu (albo zamknięciem okna panelu, gdy nie jest połączony pilot w iPhonie)."
./.venv/bin/python serwer_panelu.py 2>&1 | tee -a "$HOME/Library/Logs/gta-lektor.log"
echo "Serwer zakończył pracę — to okno możesz zamknąć."
