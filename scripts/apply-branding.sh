#!/usr/bin/env bash
# Apply Mixel-Remote branding to a RustDesk 1.4.6 source checkout.
#
# Tested file paths against RustDesk v1.4.6. Bumping UPSTREAM_VERSION may
# require updating these paths.
#
# Usage:
#   $RDREPO   = path to upstream rustdesk checkout (default: ./rustdesk)
#   $BRANDING = path to branding dir (default: ./branding)

set -euo pipefail

RDREPO="${RDREPO:-./rustdesk}"
BRANDING="${BRANDING:-./branding}"

if [[ ! -d "$RDREPO" ]]; then
  echo "❌ RustDesk source not found at $RDREPO" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$BRANDING/branding.env"

echo "→ Applying branding: $APP_NAME ($MACOS_BUNDLE_ID) on top of RustDesk $UPSTREAM_VERSION"

# 1. custom.txt — RustDesk's build-time branding override file.
cp "$BRANDING/custom.txt" "$RDREPO/custom.txt"
echo "   wrote custom.txt"

# 2. Replace icons. Verified paths for RustDesk 1.4.6:
#    res/icon.png      → main 512x512
#    res/icon.ico      → Windows .ico
#    res/32x32.png     → Linux package
#    res/64x64.png
#    res/128x128.png
#    res/128x128@2x.png (i.e. 256x256)
#    flutter/macos/Runner/AppIcon.icns
#    flutter/windows/runner/resources/app_icon.ico

cp "$BRANDING/icon-512.png"  "$RDREPO/res/icon.png"
cp "$BRANDING/icon-32.png"   "$RDREPO/res/32x32.png"
cp "$BRANDING/icon-64.png"   "$RDREPO/res/64x64.png"
cp "$BRANDING/icon-128.png"  "$RDREPO/res/128x128.png"
cp "$BRANDING/icon-256.png"  "$RDREPO/res/128x128@2x.png"
echo "   wrote Linux icon set into res/"

# Windows .ico (multi-resolution) — builds both res/icon.ico and the
# flutter runner resource so both Windows build paths see the new icon.
if command -v magick >/dev/null 2>&1; then
  magick \
    "$BRANDING/icon-16.png" \
    "$BRANDING/icon-32.png" \
    "$BRANDING/icon-48.png" \
    "$BRANDING/icon-64.png" \
    "$BRANDING/icon-128.png" \
    "$BRANDING/icon-256.png" \
    "$RDREPO/res/icon.ico"
  cp "$RDREPO/res/icon.ico" "$RDREPO/flutter/windows/runner/resources/app_icon.ico"
  echo "   wrote Windows .ico (multi-size)"
else
  echo "   ⚠ ImageMagick (magick) not found; Windows .ico not regenerated" >&2
fi

# macOS .icns — built from a temporary iconset.
MACOS_ICNS="$RDREPO/flutter/macos/Runner/AppIcon.icns"
if [[ -d "$(dirname "$MACOS_ICNS")" ]]; then
  TMP_ICONSET="$(mktemp -d)/AppIcon.iconset"
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

  if command -v iconutil >/dev/null 2>&1; then
    iconutil -c icns "$TMP_ICONSET" -o "$MACOS_ICNS"
    echo "   wrote AppIcon.icns via iconutil"
  elif command -v magick >/dev/null 2>&1; then
    magick "$BRANDING/icon-1024.png" "$MACOS_ICNS"
    echo "   wrote AppIcon.icns via magick (single-resolution fallback)"
  else
    echo "   ⚠ Neither iconutil nor magick available; AppIcon.icns not updated" >&2
  fi
  rm -rf "$(dirname "$TMP_ICONSET")"
fi

# 3. Patch user-visible strings that custom.txt doesn't fully override.

patch_string () {
  local file="$1" old="$2" new="$3"
  [[ -f "$RDREPO/$file" ]] || { echo "   ⚠ skip (no file): $file" >&2; return 0; }
  if grep -q "$old" "$RDREPO/$file"; then
    sed -i.bak "s|$old|$new|g" "$RDREPO/$file"
    rm -f "$RDREPO/$file.bak"
    echo "   patched: $file"
  else
    echo "   ℹ no match in: $file (string '$old' not found)" >&2
  fi
}

# macOS xcconfig — note: actual upstream string is `com.carriez.flutterHbb`,
# not the underscored variant. Verified against res for 1.4.6.
patch_string flutter/macos/Runner/Configs/AppInfo.xcconfig \
  "PRODUCT_NAME = RustDesk" \
  "PRODUCT_NAME = $APP_NAME"
patch_string flutter/macos/Runner/Configs/AppInfo.xcconfig \
  "PRODUCT_BUNDLE_IDENTIFIER = com.carriez.flutterHbb" \
  "PRODUCT_BUNDLE_IDENTIFIER = $MACOS_BUNDLE_ID"
patch_string flutter/macos/Runner/Configs/AppInfo.xcconfig \
  "Copyright © 2025 Purslane Ltd. All rights reserved." \
  "Copyright © 2026 $WIN_MANUFACTURER. All rights reserved."

# build.py hardcodes RustDesk.app at lines 414 + 417 (the post-flutter
# `cp service` and `create-dmg` steps for macOS). Once xcconfig
# PRODUCT_NAME is renamed, flutter produces "$APP_NAME.app" and these
# build.py lines fail unless we substitute the path too.
patch_string build.py \
  "RustDesk.app" \
  "$APP_NAME.app"

# 4. Patch user-visible "RustDesk" strings the upstream build leaves
#    intact even with custom.txt. RustDesk has a runtime replace in
#    src/lang.rs that swaps "RustDesk" → app_name when tr() is called,
#    but it only fires for strings that come through tr() AND only when
#    is_rustdesk() returns false at the right moment. In practice we've
#    observed Windows builds shipping with "RustDesk" still visible in
#    the UAC tip, status bar, About menu, whiteboard title and tab bar.
#    Belt-and-braces approach: sed-replace at source-patch time so the
#    string literals already contain "$APP_NAME" before compilation.
#
#    Safe because:
#    - lang/*.rs literals never contain "RustDesk" as an identifier
#      (the lowercase `rustdesk` in keys like `verify_rustdesk_password_tip`
#      is preserved — case-sensitive sed).
#    - The Cargo crate name (`rustdesk` in Cargo.toml) is lowercase, untouched.
#    - The web bridge comparison `mainGetAppNameSync(hint) != "RustDesk"`
#      is in flutter/lib/web/bridge.dart, deliberately NOT patched — it's
#      a positive identity check that depends on the literal "RustDesk".

echo "→ Patching RustDesk → $APP_NAME in user-visible strings"
for langfile in "$RDREPO"/src/lang/*.rs; do
  if grep -q "RustDesk" "$langfile"; then
    sed -i.bak "s/RustDesk/$APP_NAME/g" "$langfile" && rm -f "$langfile.bak"
  fi
done
echo "   patched src/lang/*.rs (47 files)"

for wbfile in "$RDREPO"/src/whiteboard/windows.rs \
              "$RDREPO"/src/whiteboard/macos.rs \
              "$RDREPO"/src/whiteboard/linux.rs; do
  if [[ -f "$wbfile" ]] && grep -q "RustDesk whiteboard" "$wbfile"; then
    sed -i.bak "s/RustDesk whiteboard/$APP_NAME whiteboard/g" "$wbfile"
    rm -f "$wbfile.bak"
  fi
done
echo "   patched src/whiteboard/{windows,macos,linux}.rs (whiteboard title)"

# Flutter tab bar shows the literal string "RustDesk" next to the logo
# when the title-bar is configured to show it — common on Windows.
TABBAR="$RDREPO/flutter/lib/desktop/widgets/tabbar_widget.dart"
if [[ -f "$TABBAR" ]] && grep -q '"RustDesk"' "$TABBAR"; then
  sed -i.bak "s/\"RustDesk\"/\"$APP_NAME\"/g" "$TABBAR"
  rm -f "$TABBAR.bak"
  echo "   patched flutter tabbar widget"
fi

# Callers that pass literal "RustDesk" keys to translate() in Dart settings
# pages — must match the renamed keys in src/lang/*.rs so lookups succeed.
for caller in \
  "$RDREPO/flutter/lib/desktop/pages/desktop_setting_page.dart" \
  "$RDREPO/flutter/lib/mobile/pages/settings_page.dart"; do
  if [[ -f "$caller" ]] && grep -q "translate('[^']*RustDesk[^']*')" "$caller"; then
    sed -i.bak "s/translate('\\([^']*\\)RustDesk\\([^']*\\)')/translate('\\1$APP_NAME\\2')/g" "$caller"
    rm -f "$caller.bak"
    echo "   patched translate() callers in $(basename "$caller")"
  fi
done

# 5. Write a runtime config fallback. This isn't strictly necessary if
#    custom.txt + RENDEZVOUS_SERVER env vars work as advertised, but it's
#    cheap insurance: if a user wipes their data dir, the binary still
#    boots pointing at the correct relay.
mkdir -p "$RDREPO/.cargo"
cat > "$RDREPO/.cargo/config.toml" <<EOF
[env]
RENDEZVOUS_SERVER = "$RENDEZVOUS_SERVER"
RELAY_SERVER = "$RELAY_SERVER"
RS_PUB_KEY = "$RS_PUB_KEY"
EOF
echo "   wrote .cargo/config.toml with baked-in server env"

echo "✓ Branding applied."
