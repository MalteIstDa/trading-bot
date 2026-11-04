#!/bin/sh
# Baut "Trading Bot.app" im Ordner trading-bot (nur Mac, braucht die Xcode Command Line Tools: swiftc).
# Danach: App in den Programme-Ordner oder ins Dock ziehen. Aendert nichts ausserhalb dieses Ordners.
set -e
cd "$(dirname "$0")/.."
REPO="$(pwd)"
APP="$REPO/Trading Bot.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
# Das Standard-SDK passt manchmal nicht zum Compiler ("this SDK is not supported by the compiler"), dann aeltere probieren
BIN="$APP/Contents/MacOS/TradingBot"
build() { swiftc "$@" -O -o "$BIN" macapp/main.swift -framework Cocoa -framework WebKit 2>/dev/null; }
if ! build; then
  for SDK in $(ls -d /Library/Developer/CommandLineTools/SDKs/MacOSX[0-9]*.[0-9]*.sdk 2>/dev/null | sort -rV); do
    build -sdk "$SDK" && break
  done
fi
[ -x "$BIN" ] || { echo "Bauen fehlgeschlagen. Fehlermeldung:"; swiftc -O -o "$BIN" macapp/main.swift -framework Cocoa -framework WebKit; exit 1; }
if command -v iconutil >/dev/null 2>&1; then
  SET="$(mktemp -d)/AppIcon.iconset"; mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z $s $s macapp/icon.png --out "$SET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) macapp/icon.png --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$APP/Contents/Resources/AppIcon.icns"
fi
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Trading Bot</string>
  <key>CFBundleDisplayName</key><string>Trading Bot</string>
  <key>CFBundleIdentifier</key><string>local.tradingbot.dashboard</string>
  <key>CFBundleExecutable</key><string>TradingBot</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>RepoPath</key><string>$REPO</string>
</dict></plist>
PLIST
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
echo "Fertig: $APP"
echo "Zum Starten doppelklicken oder: open \"$APP\""
