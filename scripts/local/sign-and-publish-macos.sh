#!/usr/bin/env bash
# Sign + notarize an existing unsigned Mixel-Remote macOS .dmg, then upload
# the signed result to Cloudflare R2.
#
# Use this when you have an unsigned .dmg from CI (or a local rebuild) and
# want a properly signed/notarized artifact without rebuilding from scratch.
#
# Prerequisites on this Mac:
#   - "Developer ID Application" cert in Keychain (System or login keychain)
#   - Xcode CLI tools installed (xcrun, codesign, stapler)
#   - Homebrew + create-dmg (`brew install create-dmg`)
#   - wrangler authed against the Mixel CF account
#   - Notarization credentials saved as a Keychain profile named "mixel-notary"
#     One-time setup:
#       xcrun notarytool store-credentials mixel-notary \
#         --apple-id  michael@mixel.ch \
#         --team-id   5277F8NDH4 \
#         --password  <app-specific-password>
#     (App-specific password from https://account.apple.com → App-Specific Passwords)

set -euo pipefail

TEAM_ID="5277F8NDH4"
NOTARY_PROFILE="mixel-notary"
R2_BUCKET="mixel-remote-binaries"

DMG_IN="${1:-}"
if [[ -z "$DMG_IN" || ! -f "$DMG_IN" ]]; then
  echo "Usage: $0 <path/to/unsigned-mixel-remote-X.Y.Z-aarch64.dmg>" >&2
  exit 1
fi

DMG_NAME="$(basename "$DMG_IN")"
WORK="$(mktemp -d -t sign-mixel-remote)"
APP_NAME="Mixel-Remote.app"
echo "→ workdir: $WORK"

# 1. Verify tools
for tool in create-dmg xcrun codesign hdiutil; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "❌ missing tool: $tool" >&2
    [[ "$tool" == "create-dmg" ]] && echo "   install with: brew install create-dmg" >&2
    exit 1
  fi
done

# 2. Find the Developer ID Application identity for our team. Match on Team
#    ID rather than a fixed name string so this works whether the cert is
#    registered to "Mixel IT and Corporate Services GmbH" or an individual
#    developer name within the same team.
CODESIGN_HASH="$(security find-identity -v -p codesigning \
  | grep "Developer ID Application" \
  | grep "($TEAM_ID)" \
  | head -1 \
  | awk '{print $2}')"
if [[ -z "$CODESIGN_HASH" ]]; then
  echo "❌ no \"Developer ID Application\" identity for team $TEAM_ID in Keychain." >&2
  echo "   Available code-signing identities:" >&2
  security find-identity -v -p codesigning | sed 's/^/     /' >&2
  exit 1
fi
CODESIGN_ID="$(security find-identity -v -p codesigning \
  | grep "Developer ID Application" \
  | grep "($TEAM_ID)" \
  | head -1 \
  | sed -E 's/^[^"]*"([^"]+)".*$/\1/')"
echo "→ signing identity: $CODESIGN_ID"

# 3. Verify notary profile is saved
if ! xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1; then
  echo "❌ notary profile '$NOTARY_PROFILE' not in Keychain. Set it up once with:"
  cat >&2 <<EOF
   xcrun notarytool store-credentials $NOTARY_PROFILE \\
     --apple-id  michael@mixel.ch \\
     --team-id   5277F8NDH4 \\
     --password  <app-specific-password>
EOF
  exit 1
fi

# 4. Mount input dmg, copy out the .app
MOUNT="$WORK/mount"
mkdir -p "$MOUNT"
echo "→ mounting $DMG_IN"
hdiutil attach "$DMG_IN" -mountpoint "$MOUNT" -nobrowse -quiet
trap 'hdiutil detach "$MOUNT" -quiet 2>/dev/null || true' EXIT
cp -R "$MOUNT/$APP_NAME" "$WORK/$APP_NAME"
hdiutil detach "$MOUNT" -quiet

# 5. Strip any existing (broken) signatures and codesign the .app deeply
#    --options runtime is required for notarization (Hardened Runtime).
echo "→ codesigning $APP_NAME (deep)"
codesign --remove-signature "$WORK/$APP_NAME" 2>/dev/null || true
codesign \
  --force \
  --options runtime \
  --deep \
  --strict \
  --timestamp \
  --sign "$CODESIGN_HASH" \
  "$WORK/$APP_NAME" \
  -vvv

# 6. Verify the codesign before going further
codesign --verify --deep --strict --verbose=2 "$WORK/$APP_NAME"

# 7. Build the signed .dmg
DMG_OUT="$WORK/$DMG_NAME"
rm -f "$DMG_OUT"
echo "→ packaging signed dmg: $DMG_OUT"
create-dmg \
  --window-size 800 400 \
  --icon "$APP_NAME" 200 190 \
  --hide-extension "$APP_NAME" \
  --app-drop-link 600 185 \
  "$DMG_OUT" \
  "$WORK/$APP_NAME"

# 8. Codesign the dmg itself (also required for notarization)
codesign \
  --force \
  --options runtime \
  --timestamp \
  --sign "$CODESIGN_HASH" \
  "$DMG_OUT" \
  -vvv

# 9. Notarize. --wait blocks until Apple finishes (typically 1-5 minutes).
echo "→ submitting to Apple notarization (be patient — typically 1-5 min)"
xcrun notarytool submit "$DMG_OUT" \
  --keychain-profile "$NOTARY_PROFILE" \
  --wait

# 10. Staple the notarization ticket so it works offline forever after.
echo "→ stapling notarization ticket"
xcrun stapler staple "$DMG_OUT"
xcrun stapler validate "$DMG_OUT"

# 11. Final Gatekeeper acceptance check
echo "→ Gatekeeper verification"
spctl -a -t open --context context:primary-signature -v "$DMG_OUT"

# 12. Upload to R2 (overwrites the existing unsigned object)
echo "→ uploading to Cloudflare R2"
WRANGLER_DIR="$(cd "$(dirname "$0")"/../../.. && pwd)/mixel-ism/services/remote-support-mailer"
if [[ ! -d "$WRANGLER_DIR" ]]; then
  WRANGLER_DIR="$HOME/Projects/mixel-ism/services/remote-support-mailer"
fi
( cd "$WRANGLER_DIR" && npx wrangler r2 object put "$R2_BUCKET/$DMG_NAME" --file "$DMG_OUT" --remote )

echo
echo "✓ Done. Signed + notarized $DMG_NAME is live at:"
echo "    https://download.mixel.ch/$DMG_NAME"
echo
echo "  workdir (clean it up if you want): $WORK"
