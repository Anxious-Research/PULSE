#!/usr/bin/env bash
# Build a shareable PULSE DMG from the installed app.
#
# Install flow this produces: DMG download -> drag to /Applications ->
# open -> app connects to GitHub, pulls the latest code and installs
# dependencies via the runtime (pm: uv, Python, Node, tools).
#
# What it does:
#   1. Copies /Applications/Pulse.app to a build dir (never touches install)
#   2. Re-points every GitHub URL inside (app.asar + unpacked dist JS)
#      from the old org to YOUR repo, so first-run clone + raw script
#      downloads hit YOUR GitHub
#   3. Refreshes agent-payload/pulse-agent from current git HEAD
#      (your latest dev), drops stale __pycache__
#   4. Fixes the absolute pm-runtime python symlink -> relative
#   5. Ad-hoc codesigns, builds drag-to-/Applications DMG
#
# Usage:
#   bash scripts/build-pulse-app-dmg.sh [--out PULSE-Setup.dmg]
#
# Precondition: git HEAD must already be PUSHED to the repo URL below,
# else a friend's first-run clone pulls stale code.
set -e
OLD_ORG="AnxiousResearchLab/pulse-agent"
NEW_REPO="https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-.git"
NEW_ORG="Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-"
OUT="${2:-}"
[ "${1:-}" = "--out" ] && OUT="${2:-PULSE-Setup.dmg}"
OUT="${OUT:-$HOME/Desktop/PULSE-Setup.dmg}"

BUILD=/tmp/pulse-dmg-build STAGE=/tmp/pulse-dmg-stage
rm -rf "$BUILD" "$STAGE"; mkdir -p "$BUILD" "$STAGE"
echo "-> copying /Applications/Pulse.app (1GB, takes a bit)..."
cp -R "/Applications/Pulse.app" "$BUILD/Pulse.app"
APP="$BUILD/Pulse.app"

echo "-> patching GitHub URLs to $NEW_REPO ..."
npx --yes asar extract "$APP/Contents/Resources/app.asar" "$BUILD/asar-out" >/dev/null 2>&1
grep -rl "$OLD_ORG" "$BUILD/asar-out" | while IFS= read -r f; do
  perl -pi -e "s#\Q$OLD_ORG\E#$NEW_ORG#g" "$f"
  perl -pi -e 's#https://portal\.anxiousresearchlab\.com/help#'"$NEW_REPO/issues"'#g' "$f"
  perl -pi -e 's#https://discord\.gg/AnxiousResearchLab#'"$NEW_REPO/issues"'#g' "$f"
done
for f in "$APP/Contents/Resources/app.asar.unpacked/dist/electron-main.mjs" \
         "$APP/Contents/Resources/app.asar.unpacked/dist/assets/"*.js; do
  [ -f "$f" ] && perl -pi -e "s#\Q$OLD_ORG\E#$NEW_ORG#g" "$f"
  [ -f "$f" ] && perl -pi -e 's#https://portal\.anxiousresearchlab\.com[A-Za-z0-9/._?=-]*#'"$NEW_REPO/issues"'#g' "$f"
  [ -f "$f" ] && perl -pi -e 's#https://discord\.gg/AnxiousResearchLab#'"$NEW_REPO/issues"'#g' "$f"
  [ -f "$f" ] && perl -pi -e 's#discord\.gg/AnxiousResearchLab#'"$NEW_REPO/issues"'#g' "$f"
done
npx --yes asar pack "$BUILD/asar-out" "$APP/Contents/Resources/app.asar" >/dev/null 2>&1

echo "-> refreshing payload source from git HEAD..."
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
(cd "$REPO_ROOT" && git archive HEAD | tar -x -C "$APP/Contents/Resources/agent-payload/pulse-agent/")
PAY="$APP/Contents/Resources/agent-payload/pulse-agent"
find "$PAY" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$PAY" -name "*.pyc" -delete 2>/dev/null || true
rm -rf "$PAY/pulse_agent.egg-info"
for f in "$PAY/.venv/lib/python3.14/site-packages/pulse_agent-0.0.0.dist-info/METADATA"; do
  [ -f "$f" ] && perl -pi -e "s#AnxiousResearchLab/[Pp]ulse-[Aa]gent#$NEW_ORG#g" "$f"
done

echo "-> relativizing pm-runtime python symlink..."
PYLINK="$APP/Contents/Resources/agent-payload/pm-runtime/bin/python"
if [ -L "$PYLINK" ] && [[ "$(readlink "$PYLINK")" == /* ]]; then
  rm "$PYLINK"; ln -s ../../tools/python/bin/python3 "$PYLINK"
fi

echo "-> stamping UI manifest (ties the bundled UI to this commit)..."
UI_COMMIT="$(cd "$REPO_ROOT" && git rev-parse HEAD)"
printf '{"commit":"%s","built_at":"%s"}\n' "$UI_COMMIT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$APP/Contents/Resources/ui-manifest.json"

echo "-> codesigning (ad-hoc)..."
xattr -cr "$APP" 2>/dev/null || true
codesign --deep --force --sign - "$APP"
codesign --verify --deep "$APP" && echo "   signature OK"

echo "-> building DMG..."
cp -R "$APP" "$STAGE/"; ln -s /Applications "$STAGE/Applications"
rm -f "$OUT"
hdiutil create -volname "PULSE" -srcfolder "$STAGE" -ov -format UDZO "$OUT" | tail -1
echo "OK: $OUT"
echo "Share AFTER pushing git HEAD or friends get stale code:"
echo "   git push origin main"
