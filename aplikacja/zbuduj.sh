#!/bin/bash
# Builds "Lektor Gier na konsoli.app" (AppleScript applet with our own icon) pointing at THIS folder, plus the iPhone (PWA) icons.
# Usage: aplikacja/zbuduj.sh [target.app]   (default: /Applications/Lektor Gier na konsoli.app)
# An existing app that runs ANOTHER folder (e.g. the installation you play on, built from the main folder) is not
# replaced: exit 6. Moved the repo on purpose? LEKTOR_NADPISZ=1 aplikacja/zbuduj.sh
# Exit: 0 built · 2 no .venv · 3 icon · 4 iconutil · 5 osacompile (the old app stays untouched) · 6 refused (other folder)
APP="${1:-/Applications/Lektor Gier na konsoli.app}"
case "$APP" in /*) ;; *) APP="$PWD/$APP" ;; esac   # a relative target counts from where you ran the script
cd "$(dirname "$0")" || exit 2
KATALOG="$(cd .. && pwd)"
PY="$KATALOG/.venv/bin/python"
[ -x "$PY" ] || { echo "Najpierw uruchom ./instaluj.sh (brak .venv)"; exit 2; }
if [ -e "$APP" ] && [ "${LEKTOR_NADPISZ:-}" != "1" ]; then
  STARY="$(osadecompile "$APP/Contents/Resources/Scripts/main.scpt" 2>/dev/null | sed -n 's/^[[:space:]]*set startCmd to "\(.*\)\/aplikacja\/start-panel.command"$/\1/p')"
  if [ "$STARY" != "$KATALOG" ]; then
    echo "Nie nadpisuję: $APP uruchamia inny folder (${STARY:-nie umiem odczytać}), a ten skrypt leży w $KATALOG."
    echo "Celowo (przeniesiony folder programu)? Uruchom: LEKTOR_NADPISZ=1 aplikacja/zbuduj.sh"
    exit 6
  fi
fi
T="$(mktemp -d)"
"$PY" -I ikona.py "$T/ikona.png" || exit 3
mkdir -p "$T/i.iconset"
for r in 16 32 128 256 512; do
  sips -z $r $r "$T/ikona.png" --out "$T/i.iconset/icon_${r}x${r}.png" >/dev/null
  sips -z $((r*2)) $((r*2)) "$T/ikona.png" --out "$T/i.iconset/icon_${r}x${r}@2x.png" >/dev/null
done
iconutil -c icns "$T/i.iconset" -o "$T/applet.icns" || exit 4
# iPhone remote (PWA) icons: full-bleed and opaque — iOS rounds the corners itself
"$PY" -I ikona.py --pelne "$T/pelna.png" || exit 3
for r in 180 192 512; do sips -z $r $r "$T/pelna.png" --out "../panel/ikona-$r.png" >/dev/null; done
# the applet needs the absolute path of this folder (spaces are fine: AppleScript quotes it)
sed "s|__KATALOG__|$KATALOG|g" "Lektor Gier na konsoli.applescript" > "$T/aplet.applescript"
# built next to nothing first: a failed compile leaves the old app in place
NOWA="$T/nowa.app"
osacompile -o "$NOWA" "$T/aplet.applescript" || exit 5
cp "$T/applet.icns" "$NOWA/Contents/Resources/applet.icns"
rm -f "$NOWA/Contents/Resources/Assets.car"   # otherwise the system shows the default applet icon
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$NOWA/Contents/Info.plist" 2>/dev/null
codesign --force --deep -s - "$NOWA" 2>/dev/null   # re-seal after swapping the icon
mkdir -p "$(dirname "$APP")" || exit 5
[ -e "$APP" ] && { mv "$APP" "$T/stara.app" || exit 5; }   # the old app is kept until the new one is fully copied
if ! ditto "$NOWA" "$APP"; then
  rm -rf "$APP"; [ -e "$T/stara.app" ] && mv "$T/stara.app" "$APP"; echo "Kopiowanie nie powiodło się — stara aplikacja wróciła."; exit 5
fi
chmod +x start-panel.command ../"Lektor Gier na konsoli.command"
touch "$APP"; echo "zbudowano: $APP"
