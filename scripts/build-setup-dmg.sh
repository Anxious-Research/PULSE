#!/usr/bin/env bash
# Build the PULSE native setup stub + drag-to-Applications DMG.
#
# What this is: PULSE's answer to Hermes-Setup — a tiny (~2MB) native app that
# downloads scripts/install.sh from OUR repo and runs its stage protocol
# (--manifest / --stage NAME --json) with live progress. All heavy work (uv,
# Python, Node, tools, checkout) happens on the user's machine via the pm
# runtime. The stub itself is clean-room Swift (setup-stub/), no third-party
# installer lineage.
#
# Baked at build time: repo URL (this checkout's origin), branch (main unless
# PULSE_BUILD_PIN_BRANCH), commit pin (PULSE_BUILD_PIN_COMMIT, usually empty
# so installs track the branch tip like Hermes does).
#
# Usage:  bash scripts/build-setup-dmg.sh [--out Install-PULSE.dmg]
set -e
OUT="${2:-}"
[ "${1:-}" = "--out" ] && OUT="${2:-}"
OUT="${OUT:-$HOME/Desktop/Install-PULSE.dmg}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BRANCH="${PULSE_BUILD_PIN_BRANCH:-main}"
PIN="${PULSE_BUILD_PIN_COMMIT:-}"
REPO_URL="$(cd "$REPO_ROOT" && git remote get-url origin)"
STAGE="$(mktemp -d /tmp/pulse-setup-build-XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

echo "-> stub config: repo=$REPO_URL branch=$BRANCH pin=${PIN:-none}"
SRC="$STAGE/pulse_setup.swift"
sed -e "s#__PULSE_REPO_URL__#$REPO_URL#" \
    -e "s#__PULSE_BRANCH__#$BRANCH#" \
    -e "s#__PULSE_PIN_COMMIT__#$PIN#" \
    "$REPO_ROOT/setup-stub/pulse_setup.swift" > "$SRC"

APP="$STAGE/PULSE-Setup.app/Contents"
mkdir -p "$APP/MacOS" "$APP/Resources"
echo "-> compiling (swiftc)..."
swiftc -parse-as-library -O -o "$APP/MacOS/PULSE-Setup" "$SRC" 2>&1 | grep -v "^$" | head -5 || true
[ -x "$APP/MacOS/PULSE-Setup" ] || { echo "compile failed" >&2; exit 1; }

cat > "$APP/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>English</string>
  <key>CFBundleDisplayName</key><string>PULSE Setup</string>
  <key>CFBundleExecutable</key><string>PULSE-Setup</string>
  <key>CFBundleIdentifier</key><string>com.anxious-research.pulse.setup</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>PULSE Setup</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>Copyright © 2026 Anxious Research</string>
</dict>
</plist>
EOF

echo "-> icon from orbital artwork..."
ICONSET="$STAGE/icon.iconset"; mkdir -p "$ICONSET"
ORBITAL="$REPO_ROOT/assets/pulse-icon-orbital.png"
for spec in "16:icon_16x16" "32:icon_16x16@2x" "32:icon_32x32" "64:icon_32x32@2x" "128:icon_128x128" "256:icon_128x128@2x" "256:icon_256x256" "512:icon_256x256@2x" "512:icon_512x512" "1024:icon_512x512@2x"; do
  px="${spec%%:*}"; name="${spec##*:}"
  sips -z "$px" "$px" "$ORBITAL" --out "$ICONSET/$name.png" >/dev/null 2>&1 || true
done
if iconutil -c icns "$ICONSET" -o "$APP/Resources/icon.icns" 2>/dev/null; then
  echo "   icon ok"
else
  echo "   icon skipped (iconutil unavailable)"
fi

echo "-> signing (ad-hoc)..."
xattr -cr "$STAGE/PULSE-Setup.app" 2>/dev/null || true
codesign --deep --force --sign - "$STAGE/PULSE-Setup.app"
codesign --verify --deep "$STAGE/PULSE-Setup.app" && echo "   signature OK"

echo "-> DMG (drag PULSE-Setup to Applications, like Hermes)..."
DMGROOT="$STAGE/dmg"; mkdir -p "$DMGROOT"
cp -R "$STAGE/PULSE-Setup.app" "$DMGROOT/"
ln -s /Applications "$DMGROOT/Applications"
rm -f "$OUT"
hdiutil create -volname "Install PULSE" -srcfolder "$DMGROOT" -ov -format UDZO "$OUT" | tail -1
du -sh "$OUT"
echo "OK: $OUT"
