#!/usr/bin/env bash
# PULSE bootstrap DMG builder — the CORRECT way to share PULSE.
#
# Problem it fixes: copying /Applications/Pulse.app into a DMG ships only
# the thin Electron shell. The real agent lives in:
#   ~/.pulse/pulse-agent  (git checkout)
#   ~/.pulse/tools        (uv, Python, Node via pm)
#   ~/.local/bin/pulse    (launcher)
# A friend who gets only the .app has no runtime -> missing dependencies.
#
# What this builds instead: a DMG containing a bootstrap installer that, on
# the friend's Mac, clones YOUR repo and runs the same staged install
# (prerequisites -> repository -> venv -> python-deps via pm ->
# products -> config). Dependencies install natively on their machine
# through the runtime, exactly like `curl ... install.sh | bash`.
#
# Usage:
#   PULSE_REPO_URL=https://github.com/Anxious-Research/PULSE.git \
#     bash scripts/build-pulse-bootstrap-dmg.sh [--out PULSE-Setup.dmg]
#
# Requirements: macOS with hdiutil + create-dmg (brew install create-dmg)
# or falls back to plain hdiutil.
set -e
REPO_URL="${PULSE_REPO_URL:-https://github.com/Anxious-Research/PULSE.git}"
OUT="${1:-}"
[ "${1:-}" = "--out" ] && OUT="${2:-}"
OUT="${OUT:-PULSE-Setup.dmg}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STAGE="$(mktemp -d /tmp/pulse-dmg-XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

echo "→ Staging bootstrap installer (repo: $REPO_URL)"

mkdir -p "$STAGE/PULSE-Setup"
# The installer script itself (staged, versioned with the repo)
cp "$REPO_ROOT/scripts/install.sh" "$STAGE/PULSE-Setup/install.sh"
chmod +x "$STAGE/PULSE-Setup/install.sh"

# Double-clickable macOS installer app bundle (runs install.sh in Terminal)
APP="$STAGE/PULSE-Setup/Install PULSE.app/Contents/MacOS"
mkdir -p "$APP"
cat > "$APP/Install PULSE" <<EOF
#!/usr/bin/env bash
REPO_URL="$REPO_URL"
TERM_SCRIPT=\$(mktemp /tmp/pulse-install-XXXXXX.command)
cat > "\$TERM_SCRIPT" <<INNER
#!/usr/bin/env bash
export PULSE_REPO_URL="\$REPO_URL"
echo "☤ PULSE Agent Installer (Anxious Research Lab)"
echo "   Source: \$REPO_URL"
echo ""
curl -fsSL "\$REPO_URL" >/dev/null 2>&1 || true
bash <(curl -fsSL "https://raw.githubusercontent.com/\$(echo \$REPO_URL | sed 's#.*github.com[:/]##;s#\\.git\$##')/main/scripts/install.sh") 2>/dev/null || {
  echo "Direct download failed — falling back to bundled install.sh"
  bash "\$(dirname "\$0")/../../../install.sh"
}
echo ""
echo "Done. Open a new terminal and run: pulse"
read -p "Press Enter to close..."
INNER
chmod +x "\$TERM_SCRIPT"
open "\$TERM_SCRIPT"
EOF
chmod +x "$APP/Install PULSE"

cat > "$STAGE/PULSE-Setup/README.txt" <<EOF
PULSE Agent — Anxious Research Lab
==================================

DO NOT drag Pulse.app alone. Install correctly:

  Option A (double-click): open "Install PULSE.app"
  Option B (terminal):
    curl -fsSL $REPO_URL/raw/main/scripts/install.sh | bash
    (or: bash /Volumes/PULSE-Setup/PULSE-Setup/install.sh)

The installer clones this repo, provisions uv + Python + Node + tools
via pm (hash-verified against uv.lock), builds the pulse command and
apps, then runs setup. Updates later with: pulse update

Published by Anxious Research Lab. Source: $REPO_URL
EOF

# Offline fallback: tarball of the current checkout (so install works
# even if git hosting is briefly unreachable — installer prefers git).
(cd "$REPO_ROOT" && git archive --format=tar.gz -o "$STAGE/PULSE-Setup/pulse-agent.tar.gz" HEAD 2>/dev/null || true)

DMG_OUT="$PWD/${OUT##*/}"
[ "$OUT" = "${OUT#/}" ] || DMG_OUT="$OUT"
rm -f "$DMG_OUT"

if command -v create-dmg >/dev/null 2>&1; then
  create-dmg --volname "PULSE Setup" --window-size 540 380 \
    --icon-size 96 --app-drop-link 450 185 \
    --eula LICENSE "$DMG_OUT" "$STAGE/PULSE-Setup" 2>/dev/null \
  || create-dmg --volname "PULSE Setup" "$DMG_OUT" "$STAGE/PULSE-Setup"
else
  hdiutil create -volname "PULSE Setup" -srcfolder "$STAGE/PULSE-Setup" \
    -ov -format UDZO "$DMG_OUT"
fi
echo "✓ DMG ready: $DMG_OUT"
echo "  Share this file. Friend double-clicks Install PULSE.app —"
echo "  dependencies install on THEIR machine via pm runtime."
