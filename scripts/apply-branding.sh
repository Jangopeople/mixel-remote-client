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

# 5. Bake the Mixel relay server + public key into the binary as the
#    compile-time DEFAULT, so a freshly downloaded client already points at
#    rs.mixel.ch and needs zero manual "ID/Relay Server + Key" entry.
#
#    IMPORTANT — why the old approach was a no-op:
#    A previous version of this script wrote a `.cargo/config.toml` with
#    `[env] RENDEZVOUS_SERVER/RELAY_SERVER/RS_PUB_KEY`. Upstream RustDesk's
#    hbb_common (libs/hbb_common/src/config.rs) does NOT read any of those
#    environment variables at compile time — verified against the 1.4.6
#    submodule. The server + key defaults are hardcoded constants:
#        pub const RENDEZVOUS_SERVERS: &[&str] = &["rs-ny.rustdesk.com"];
#        pub const RS_PUB_KEY: &str = "OeVuKk5nlHiXp+APNn0Y3pC1Iwpwn44JGqrQCsWqmBw=";
#    So the env file did nothing and every downloaded build shipped with
#    RustDesk's own public server, forcing customers to type rs.mixel.ch +
#    our key by hand. The reliable, well-known self-host method is to patch
#    those two constants directly. The CI clones rustdesk with
#    --recurse-submodules, so this file exists when branding runs.
#
#    The regex keys on the constant NAME (not the upstream default value),
#    so it keeps working if upstream changes their default server or key.
#    RELAY_SERVER is intentionally not patched separately: RustDesk derives
#    the relay from whatever the rendezvous server advertises.
CONFIG_RS="$RDREPO/libs/hbb_common/src/config.rs"
if [[ ! -f "$CONFIG_RS" ]]; then
  echo "❌ hbb_common config.rs not found at $CONFIG_RS — submodule not checked out?" >&2
  echo "   (CI must clone rustdesk with --recurse-submodules)" >&2
  exit 1
fi

# Escape the values for use in a sed replacement (`&` and `|` are the only
# sed-special chars that can appear; our server/key don't contain `|`, but
# guard anyway). The replacement also has to emit literal `&` characters in
# `&[&str]`, which we write escaped as `\&`.
rs_server_esc=$(printf '%s' "$RENDEZVOUS_SERVER" | sed -e 's/[&|]/\\&/g')
rs_key_esc=$(printf '%s' "$RS_PUB_KEY" | sed -e 's/[&|]/\\&/g')

sed -i.bak -E \
  "s|pub const RENDEZVOUS_SERVERS: &\[&str\] = .*;|pub const RENDEZVOUS_SERVERS: \&[\&str] = \&[\"${rs_server_esc}\"];|" \
  "$CONFIG_RS"
sed -i.bak -E \
  "s|pub const RS_PUB_KEY: &str = \".*\";|pub const RS_PUB_KEY: \&str = \"${rs_key_esc}\";|" \
  "$CONFIG_RS"
rm -f "$CONFIG_RS.bak"

# Fail loudly if the patch didn't land — a silent miss would ship another
# unconfigured build and we'd not notice until a customer complains again.
if ! grep -q "pub const RENDEZVOUS_SERVERS: &\[&str\] = &\[\"${RENDEZVOUS_SERVER}\"\];" "$CONFIG_RS"; then
  echo "❌ Failed to patch RENDEZVOUS_SERVERS in $CONFIG_RS (upstream format changed?)" >&2
  grep -n "RENDEZVOUS_SERVERS" "$CONFIG_RS" >&2 || true
  exit 1
fi
if ! grep -q "pub const RS_PUB_KEY: &str = \"${RS_PUB_KEY}\";" "$CONFIG_RS"; then
  echo "❌ Failed to patch RS_PUB_KEY in $CONFIG_RS (upstream format changed?)" >&2
  grep -n "RS_PUB_KEY" "$CONFIG_RS" >&2 || true
  exit 1
fi
echo "   baked rendezvous server '$RENDEZVOUS_SERVER' + Mixel pub key into hbb_common config.rs"

# 6. Microsoft Store (MSIX) policy 10.1.5 — a Store build must not promote
#    acquiring software outside the Store. Hide the "install to system" /
#    upgrade cards and the auto-update/download card when running as a packaged
#    MSIX app, detected in pure Dart via the executable path containing
#    'windowsapps' (MSIX installs under ...\WindowsApps\...). The direct-download
#    Windows build runs from elsewhere and keeps these prompts. dart:io (Platform)
#    is already imported by this file upstream.
HOME_PAGE="$RDREPO/flutter/lib/desktop/pages/desktop_home_page.dart"
if [[ ! -f "$HOME_PAGE" ]]; then
  echo "❌ desktop_home_page.dart not found at $HOME_PAGE" >&2
  exit 1
fi
sed -i.bak "s|if (isWindows && !bind.isDisableInstallation()) {|if (isWindows \&\& !bind.isDisableInstallation() \&\& !Platform.resolvedExecutable.toLowerCase().contains('windowsapps')) {|" "$HOME_PAGE"
sed -i.bak "s|contains('rustdesk')) {|contains('rustdesk') \&\& !Platform.resolvedExecutable.toLowerCase().contains('windowsapps')) {|" "$HOME_PAGE"
rm -f "$HOME_PAGE.bak"
guards=$(grep -c "contains('windowsapps')" "$HOME_PAGE" || true)
if [[ "$guards" -lt 2 ]]; then
  echo "❌ MSIX install/update guard did not apply (got $guards/2 — upstream desktop_home_page.dart changed)" >&2
  exit 1
fi
echo "   patched desktop_home_page.dart (hide install/update prompts in MSIX build)"

echo "✓ Branding applied."
