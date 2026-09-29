#!/usr/bin/env bash
# Publish the prebuilt desktop UI bundle to the rolling `desktop-ui` GitHub Release.
#
# This is PULSE's prebuilt channel (Hermes uploads its installers to GitHub
# Releases the same way): the Electron UI cannot be rebuilt from this checkout,
# so `pulse update` fetches the bundle from here instead of building it.
# Release contents (public download, no auth needed for installs):
#   pulse-desktop-ui.tar.gz   app.asar + app.asar.unpacked/ + ui-manifest.json
#   ui-manifest.json          {"commit","built_at","sha256"}
#
# Usage:
#   GITHUB_TOKEN=<token-with-contents-write> bash scripts/publish-desktop-ui.sh [--app /path/to/Pulse.app]
#
# The token needs Contents read+write on this repo only. Friends installing or
# updating never need it — release downloads are public.
set -e
APP="${2:-}"
[ "${1:-}" = "--app" ] && APP="${2:-}"
APP="${APP:-/Applications/Pulse.app}"
[ -d "$APP" ] || { echo "app not found: $APP" >&2; exit 1; }
[ -n "${GITHUB_TOKEN:-}" ] || { echo "GITHUB_TOKEN is required" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OWNER_REPO="$(cd "$REPO_ROOT" && git remote get-url origin | sed -E 's#.*github.com[/:]##; s#\.git$##')"
API="https://api.github.com/repos/$OWNER_REPO/releases"
AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN" -H Accept:application/vnd.github+json -H X-GitHub-Api-Version:2022-11-28)
TAG="desktop-ui"

WORK="$(mktemp -d /tmp/pulse-ui-pub-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
RES="$APP/Contents/Resources"
cp "$RES/app.asar" "$WORK/"
cp -R "$RES/app.asar.unpacked" "$WORK/"
if [ -f "$RES/ui-manifest.json" ]; then
  cp "$RES/ui-manifest.json" "$WORK/"
else
  printf '{"commit":"%s","built_at":"%s"}\n' \
    "$(cd "$REPO_ROOT" && git rev-parse HEAD)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$WORK/ui-manifest.json"
fi
(cd "$WORK" && tar -czf pulse-desktop-ui.tar.gz app.asar app.asar.unpacked ui-manifest.json)
SHA="$(sha256sum "$WORK/pulse-desktop-ui.tar.gz" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$WORK/pulse-desktop-ui.tar.gz" | cut -d' ' -f1)"
python3 - "$WORK/ui-manifest.json" "$SHA" <<'EOF'
import json, sys
p, sha = sys.argv[1], sys.argv[2]
m = json.load(open(p)); m["sha256"] = sha
json.dump(m, open(p, "w"), indent=2)
print(open(p).read())
EOF

REL_ID="$(curl -fsSL "${AUTH[@]}" "$API/tags/$TAG" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))' 2>/dev/null || true)"
if [ -z "$REL_ID" ]; then
  echo "-> creating rolling release $TAG"
  REL_ID="$(curl -fsSL -X POST "${AUTH[@]}" "$API" \
    -d "{\"tag_name\":\"$TAG\",\"name\":\"PULSE desktop UI (rolling)\",\"body\":\"Prebuilt desktop UI bundle for pulse update. Moves with every UI publish.\",\"draft\":false,\"prerelease\":false}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
else
  echo "-> reusing release $TAG (id $REL_ID)"
fi
# Replace same-name assets so the fixed download URLs always serve fresh bytes.
EXISTING="$(curl -fsSL "${AUTH[@]}" "$API/$REL_ID/assets?per_page=100" | python3 -c 'import json,sys
for a in json.load(sys.stdin): print(a["id"], a["name"])')"
echo "$EXISTING" | while read -r aid aname; do
  case "$aname" in pulse-desktop-ui.tar.gz|ui-manifest.json)
    echo "-> deleting old asset $aname"; curl -fsSL -X DELETE "${AUTH[@]}" "$API/assets/$aid" -o /dev/null ;;
  esac
done
UPLOAD_BASE="$(curl -fsSL "${AUTH[@]}" "$API/$REL_ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["upload_url"].split("{")[0])')"
for asset in pulse-desktop-ui.tar.gz ui-manifest.json; do
  echo "-> uploading $asset"
  ctype="application/gzip"; [ "$asset" != pulse-desktop-ui.tar.gz ] && ctype="application/json"
  curl -fsSL -X POST -H "Authorization: Bearer $GITHUB_TOKEN" -H "Content-Type: $ctype" \
    "$UPLOAD_BASE?name=$asset" --data-binary "@$WORK/$asset" -o /dev/null
done
echo "OK: https://github.com/$OWNER_REPO/releases/tag/$TAG"
