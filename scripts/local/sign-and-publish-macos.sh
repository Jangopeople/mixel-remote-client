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

# 4. Mount input dmg, copy out the .app (whatever upstream called it)
MOUNT="$WORK/mount"
mkdir -p "$MOUNT"
echo "→ mounting $DMG_IN"
hdiutil attach "$DMG_IN" -mountpoint "$MOUNT" -nobrowse -quiet
trap 'hdiutil detach "$MOUNT" -quiet 2>/dev/null || true' EXIT

SRC_APP="$(find "$MOUNT" -maxdepth 2 -name "*.app" -type d | head -n1)"
if [[ -z "$SRC_APP" ]]; then
  echo "❌ no .app found in $DMG_IN" >&2
  exit 1
fi
SRC_APP_NAME="$(basename "$SRC_APP")"
echo "→ found $SRC_APP_NAME in dmg"

# Always rebrand to "Mixel-Remote.app" regardless of upstream name.
APP_NAME="Mixel-Remote.app"
cp -R "$SRC_APP" "$WORK/$APP_NAME"
hdiutil detach "$MOUNT" -quiet

# 5. REBRAND: swap AppIcon.icns + patch Info.plist (display name).
#    Branding assets ship in this repo's branding/ directory.
BRANDING="${BRANDING:-$(cd "$(dirname "$0")"/../../branding && pwd)}"
INFO_PLIST="$WORK/$APP_NAME/Contents/Info.plist"
ICNS_OUT="$WORK/$APP_NAME/Contents/Resources/AppIcon.icns"

if [[ ! -d "$BRANDING" || ! -f "$BRANDING/icon-1024.png" ]]; then
  echo "❌ branding dir or icon-1024.png not found at $BRANDING" >&2
  exit 1
fi

echo "→ generating AppIcon.icns from branding/icon-*.png"
TMP_ICONSET="$WORK/AppIcon.iconset"
mkdir -p "$TMP_ICONSET"
cp "$BRANDING/icon-16.png"   "$TMP_ICONSET/icon_16x16.png"
cp "$BRANDING/icon-32.png"   "$TMP_ICONSET/icon_16x16@2x.png"
cp "$BRANDING/icon-32.png"   "$TMP_ICONSET/icon_32x32.png"
cp "$BRANDING/icon-64.png"   "$TMP_ICONSET/icon_32x32@2x.png"
cp "$BRANDING/icon-128.png"  "$TMP_ICONSET/icon_128x128.png"
cp "$BRANDING/icon-256.png"  "$TMP_ICONSET/icon_128x128@2x.png"
cp "$BRANDING/icon-256.png"  "$TMP_ICONSET/icon_256x256.png"
cp "$BRANDING/icon-512.png"  "$TMP_ICONSET/icon_256x256@2x.png"
cp "$BRANDING/icon-512.png"  "$TMP_ICONSET/icon_512x512.png"
cp "$BRANDING/icon-1024.png" "$TMP_ICONSET/icon_512x512@2x.png"
iconutil -c icns "$TMP_ICONSET" -o "$ICNS_OUT"

# Find the existing AppIcon name from Info.plist (so we replace the right
# file even if upstream calls it something else).
EXISTING_ICON_NAME="$(/usr/libexec/PlistBuddy -c "Print :CFBundleIconFile" "$INFO_PLIST" 2>/dev/null || true)"
if [[ -n "$EXISTING_ICON_NAME" && "$EXISTING_ICON_NAME" != "AppIcon" ]]; then
  cp "$ICNS_OUT" "$WORK/$APP_NAME/Contents/Resources/$EXISTING_ICON_NAME.icns"
fi

echo "→ patching Info.plist (display name → Mixel-Remote)"
/usr/libexec/PlistBuddy -c "Set :CFBundleName Mixel-Remote"        "$INFO_PLIST" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Add :CFBundleName string Mixel-Remote" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Mixel-Remote"        "$INFO_PLIST" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string Mixel-Remote" "$INFO_PLIST"

# Localized display names (e.g., en.lproj/InfoPlist.strings) override
# Info.plist on some setups — overwrite them too.
for lp in "$WORK/$APP_NAME/Contents/Resources"/*.lproj; do
  if [[ -d "$lp" ]]; then
    for sf in "$lp/InfoPlist.strings" "$lp/InfoPlist.loctable"; do
      if [[ -f "$sf" ]]; then
        rm -f "$sf"
        echo "   removed localized name override: $(basename "$lp")/$(basename "$sf")"
      fi
    done
  fi
done

# 6. Strip any existing (now-broken because we modified) signatures and
#    codesign the .app deeply. --options runtime is required for notarization.
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

# 7. Verify the codesign before going further
codesign --verify --deep --strict --verbose=2 "$WORK/$APP_NAME"

# 7b. Notarize the .app DIRECTLY (not just the DMG).
#     Why: notarytool stores the ticket keyed by the submitted artifact's
#     hash. If we only notarize the DMG, the .app inside has no ticket of
#     its own — and once the user drags .app -> /Applications, the DMG's
#     staple is irrelevant. macOS then treats the extracted .app as
#     "needs online re-verification" on every launch, which destabilises
#     TCC permission grants (Screen Recording loops, Accessibility
#     loops). Symptom: user grants permission, reboot, prompt comes
#     back. Fix: notarize the .app, staple the .app, THEN package it
#     into a DMG so the .app the user installs is already stapled.
echo "→ submitting .app to Apple notarization (1-5 min)"
APP_ZIP="$WORK/${APP_NAME%.app}.zip"
ditto -c -k --keepParent "$WORK/$APP_NAME" "$APP_ZIP"
xcrun notarytool submit "$APP_ZIP" \
  --keychain-profile "$NOTARY_PROFILE" \
  --wait
rm -f "$APP_ZIP"

# 7c. Staple the .app — embeds the ticket into the bundle so macOS can
#     verify offline and TCC treats every launch as "trusted same app".
echo "→ stapling .app"
xcrun stapler staple "$WORK/$APP_NAME"
xcrun stapler validate "$WORK/$APP_NAME"

# 8. Build the signed+stapled .dmg (now containing the stapled .app)
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

# 9. Codesign the dmg itself (also required for notarization)
codesign \
  --force \
  --options runtime \
  --timestamp \
  --sign "$CODESIGN_HASH" \
  "$DMG_OUT" \
  -vvv

# 10. Notarize the DMG separately (different artifact, different hash).
echo "→ submitting DMG to Apple notarization (1-5 min)"
xcrun notarytool submit "$DMG_OUT" \
  --keychain-profile "$NOTARY_PROFILE" \
  --wait

# 11. Staple the DMG (defensive — makes mounted-DMG flow work offline too).
echo "→ stapling DMG"
xcrun stapler staple "$DMG_OUT"
xcrun stapler validate "$DMG_OUT"

# 11. Final Gatekeeper acceptance check
echo "→ Gatekeeper verification"
spctl -a -t open --context context:primary-signature -v "$DMG_OUT"

# 12. Upload to R2 — both under the canonical version-named filename
#     AND under the stable customer-facing name the /remote-support page
#     links to. Without the customer-facing copy, the page would still
#     serve the previous build and customers would never get the new one.
echo "→ uploading to Cloudflare R2"
WRANGLER_DIR="$(cd "$(dirname "$0")"/../../.. && pwd)/mixel-ism/services/remote-support-mailer"
if [[ ! -d "$WRANGLER_DIR" ]]; then
  WRANGLER_DIR="$HOME/Projects/mixel-ism/services/remote-support-mailer"
fi

# Customer-facing alias names by upstream architecture. apps/web/src/app/
# remote-support/page.tsx hard-codes these stable names + a ?v= query
# string for cache-busting. Bump the ?v= in that file when you re-run
# this script so end users invalidate their CF edge cache.
case "$DMG_NAME" in
  *aarch64*|*arm64*|*apple-silicon*) CUSTOMER_NAME="Mixel-Remote-Support-Apple-Silicon.dmg" ;;
  *x86_64*|*x64*|*intel*)            CUSTOMER_NAME="Mixel-Remote-Support-Intel.dmg" ;;
  *)                                  CUSTOMER_NAME="" ;;
esac

(
  cd "$WRANGLER_DIR"
  npx wrangler r2 object put "$R2_BUCKET/$DMG_NAME" --file "$DMG_OUT" --remote
  if [[ -n "$CUSTOMER_NAME" ]]; then
    echo "→ also uploading as customer-facing alias: $CUSTOMER_NAME"
    npx wrangler r2 object put "$R2_BUCKET/$CUSTOMER_NAME" --file "$DMG_OUT" --remote
  fi
)

echo
echo "✓ Done. Signed + notarized $DMG_NAME is live at:"
echo "    https://download.mixel.ch/$DMG_NAME"
if [[ -n "$CUSTOMER_NAME" ]]; then
  echo "    https://download.mixel.ch/$CUSTOMER_NAME  (the customer-facing URL the /remote-support page links to)"
  echo
  echo "  Reminder: bump the ?v= cache-buster in apps/web/src/app/remote-support/page.tsx so end users get the new DMG immediately."
fi
echo
echo "  workdir (clean it up if you want): $WORK"
