#!/bin/bash
# Builds "GTA Lektor.app" (AppleScript applet with our own icon) pointing at THIS folder, plus the iPhone (PWA) icons.
# Usage: aplikacja/zbuduj.sh [target.app]   (default: /Applications/GTA Lektor.app)
cd "$(dirname "$0")" || exit 2
KATALOG="$(cd .. && pwd)"
APP="${1:-/Applications/GTA Lektor.app}"
PY="$KATALOG/.venv/bin/python"
[ -x "$PY" ] || { echo "Najpierw uruchom ./instaluj.sh (brak .venv)"; exit 2; }
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
sed "s|__KATALOG__|$KATALOG|g" "GTA Lektor.applescript" > "$T/aplet.applescript"
rm -rf "$APP"; osacompile -o "$APP" "$T/aplet.applescript" || exit 5
cp "$T/applet.icns" "$APP/Contents/Resources/applet.icns"
rm -f "$APP/Contents/Resources/Assets.car"   # otherwise the system shows the default applet icon
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$APP/Contents/Info.plist" 2>/dev/null
codesign --force --deep -s - "$APP" 2>/dev/null   # re-seal after swapping the icon
chmod +x start-panel.command ../"Lektor GTA6.command"
touch "$APP"; echo "zbudowano: $APP"
