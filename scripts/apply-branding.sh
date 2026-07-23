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

# Escape a literal string for use as a sed BRE *pattern* (plus the '|'
# delimiter we use). Backslash first, then the rest.
_esc_pat () {
  local s=$1
  s=${s//\\/\\\\}; s=${s//./\\.}; s=${s//\*/\\*}; s=${s//\[/\\[}
  s=${s//^/\\^}; s=${s//\$/\\\$}; s=${s//|/\\|}
  printf '%s' "$s"
}
# Escape a literal string for use as a sed *replacement* (plus '|').
# Critically escapes '&' — otherwise sed expands it to the whole match,
# which silently corrupted replacements containing "&str", "&[&str]", etc.
_esc_rep () {
  local s=$1
  s=${s//\\/\\\\}; s=${s//&/\\&}; s=${s//|/\\|}
  printf '%s' "$s"
}
patch_string () {
  local file="$1" old="$2" new="$3"
  [[ -f "$RDREPO/$file" ]] || { echo "   ⚠ skip (no file): $file" >&2; return 0; }
  if grep -qF -- "$old" "$RDREPO/$file"; then
    sed -i.bak "s|$(_esc_pat "$old")|$(_esc_rep "$new")|g" "$RDREPO/$file"
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
sed -i.bak -E \
  "s|pub static ref APP_NAME: RwLock<String> = RwLock::new\(\"RustDesk\".to_owned\(\)\);|pub static ref APP_NAME: RwLock<String> = RwLock::new\(\"${APP_NAME}\".to_owned\(\)\);|" \
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
if ! grep -q "pub static ref APP_NAME: RwLock<String> = RwLock::new(\"${APP_NAME}\".to_owned());" "$CONFIG_RS"; then
  echo "❌ Failed to patch APP_NAME in $CONFIG_RS" >&2
  exit 1
fi
echo "   baked rendezvous server '$RENDEZVOUS_SERVER' + Mixel pub key + default APP_NAME '$APP_NAME' into hbb_common config.rs"

# 5b. Also seed DEFAULT_SETTINGS so the Settings → Network fields VISIBLY
#     show the Mixel server + key instead of appearing empty. The compiled
#     constants above make the client USE rs.mixel.ch, but the UI fields
#     only render the option map — empty fields made customers (and Michael,
#     2026-07-21) believe the build was unconfigured. DEFAULT_SETTINGS is
#     upstream's official mechanism for visible-but-overridable defaults:
#     get_option() falls back to it, and is_option_can_save() keeps user
#     configs clean when the value equals the default.
rel_server_esc=$(printf '%s' "$RELAY_SERVER" | sed -e 's/[&|]/\\&/g')
sed -i.bak -E \
  "s|pub static ref DEFAULT_SETTINGS: RwLock<HashMap<String, String>> = Default::default\(\);|pub static ref DEFAULT_SETTINGS: RwLock<HashMap<String, String>> = RwLock::new(HashMap::from([(\"custom-rendezvous-server\".to_owned(), \"${rs_server_esc}\".to_owned()), (\"relay-server\".to_owned(), \"${rel_server_esc}\".to_owned()), (\"key\".to_owned(), \"${rs_key_esc}\".to_owned())]));|" \
  "$CONFIG_RS"
rm -f "$CONFIG_RS.bak"
if ! grep -q "pub static ref DEFAULT_SETTINGS: RwLock<HashMap<String, String>> = RwLock::new(HashMap::from" "$CONFIG_RS"; then
  echo "❌ Failed to patch DEFAULT_SETTINGS in $CONFIG_RS (upstream format changed?)" >&2
  grep -n "DEFAULT_SETTINGS" "$CONFIG_RS" >&2 || true
  exit 1
fi
echo "   seeded DEFAULT_SETTINGS so Network settings visibly show '$RENDEZVOUS_SERVER' + key"

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

# 7. OS-level identity — make the shipped product Mixel-Remote only.
#    Leave internal link names alone (Cargo crate `rustdesk`, `librustdesk.*`).
#    Leave the web-bridge identity check `!= "RustDesk"` alone so
#    is_custom_client() keeps working.
echo "→ Rebranding OS / installer identity → $APP_NAME ($APP_NAME_KEBAB)"

# Windows PE version resources (Task Manager, Explorer Properties, SmartScreen).
WIN_RC="$RDREPO/flutter/windows/runner/Runner.rc"
if [[ -f "$WIN_RC" ]]; then
  sed -i.bak \
    -e "s|VALUE \"CompanyName\", \"Purslane Ltd\"|VALUE \"CompanyName\", \"$WIN_MANUFACTURER\"|" \
    -e "s|VALUE \"FileDescription\", \"RustDesk Remote Desktop\"|VALUE \"FileDescription\", \"$APP_DISPLAY_NAME\"|" \
    -e "s|VALUE \"InternalName\", \"rustdesk\"|VALUE \"InternalName\", \"$APP_NAME_KEBAB\"|" \
    -e "s|VALUE \"LegalCopyright\", \"Copyright © 2025 Purslane Ltd. All rights reserved.\"|VALUE \"LegalCopyright\", \"Copyright © 2026 $WIN_MANUFACTURER. All rights reserved.\"|" \
    -e "s|VALUE \"OriginalFilename\", \"rustdesk.exe\"|VALUE \"OriginalFilename\", \"${APP_NAME_KEBAB}.exe\"|" \
    -e "s|VALUE \"ProductName\", \"RustDesk\"|VALUE \"ProductName\", \"$APP_DISPLAY_NAME\"|" \
    "$WIN_RC"
  rm -f "$WIN_RC.bak"
  echo "   patched flutter/windows/runner/Runner.rc"
fi

# Windows window-title / single-instance fallback before get_app_name loads.
WIN_MAIN="$RDREPO/flutter/windows/runner/main.cpp"
if [[ -f "$WIN_MAIN" ]]; then
  sed -i.bak \
    -e "s|std::wstring app_name = L\"RustDesk\";|std::wstring app_name = L\"$APP_NAME\";|" \
    -e "s|RustDesk \[|${APP_NAME} [|g" \
    "$WIN_MAIN"
  rm -f "$WIN_MAIN.bak"
  echo "   patched flutter/windows/runner/main.cpp fallback app_name"
fi

# Flutter BINARY_NAME / APPLICATION_ID (exe + linux binary name users see).
for cmake in \
  "$RDREPO/flutter/windows/CMakeLists.txt" \
  "$RDREPO/flutter/linux/CMakeLists.txt"; do
  if [[ -f "$cmake" ]]; then
    sed -i.bak \
      -e "s|set(BINARY_NAME \"rustdesk\")|set(BINARY_NAME \"$APP_NAME_KEBAB\")|" \
      -e "s|set(APPLICATION_ID \"com.carriez.flutter_hbb\")|set(APPLICATION_ID \"$MACOS_BUNDLE_ID\")|" \
      "$cmake"
    rm -f "$cmake.bak"
    echo "   patched $(basename "$(dirname "$cmake")")/CMakeLists.txt BINARY_NAME"
  fi
done

# macOS URL scheme (deep links) — drop com.carriez.rustdesk / rustdesk://.
MAC_PLIST="$RDREPO/flutter/macos/Runner/Info.plist"
if [[ -f "$MAC_PLIST" ]]; then
  sed -i.bak \
    -e "s|<string>com.carriez.rustdesk</string>|<string>$MACOS_BUNDLE_ID</string>|" \
    -e "s|<string>rustdesk</string>|<string>$APP_NAME_KEBAB</string>|" \
    "$MAC_PLIST"
  rm -f "$MAC_PLIST.bak"
  echo "   patched flutter/macos/Runner/Info.plist URL scheme"
fi

# Linux desktop entries + systemd unit (launcher name, icons, Exec).
for desk in \
  "$RDREPO/res/rustdesk.desktop" \
  "$RDREPO/res/rustdesk-link.desktop"; do
  if [[ -f "$desk" ]]; then
    sed -i.bak \
      -e "s|^Name=RustDesk|Name=$APP_DISPLAY_NAME|" \
      -e "s|^Icon=rustdesk|Icon=$APP_NAME_KEBAB|" \
      -e "s|^Exec=rustdesk|Exec=$APP_NAME_KEBAB|" \
      -e "s|^TryExec=rustdesk|TryExec=$APP_NAME_KEBAB|" \
      -e "s|^StartupWMClass=rustdesk|StartupWMClass=$APP_NAME_KEBAB|" \
      -e "s|x-scheme-handler/rustdesk|x-scheme-handler/$APP_NAME_KEBAB|" \
      -e "s|Keywords=.*|Keywords=remote;support;mixel;desktop;|" \
      "$desk"
    rm -f "$desk.bak"
    echo "   patched $(basename "$desk")"
  fi
done

SERVICE="$RDREPO/res/rustdesk.service"
if [[ -f "$SERVICE" ]]; then
  sed -i.bak \
    -e "s|^Description=RustDesk|Description=$APP_NAME|" \
    -e "s|/usr/bin/rustdesk|/usr/bin/$APP_NAME_KEBAB|g" \
    -e "s|pkill -f \"rustdesk --\"|pkill -f \"$APP_NAME_KEBAB --\"|g" \
    -e "s|/run/rustdesk.pid|/run/$APP_NAME_KEBAB.pid|" \
    "$SERVICE"
  rm -f "$SERVICE.bak"
  echo "   patched res/rustdesk.service"
fi

# Debian maintainer scripts — install paths + service name users manage.
for debfile in \
  "$RDREPO/res/DEBIAN/postinst" \
  "$RDREPO/res/DEBIAN/prerm" \
  "$RDREPO/res/DEBIAN/preinst" \
  "$RDREPO/res/DEBIAN/postrm"; do
  if [[ -f "$debfile" ]]; then
    sed -i.bak \
      -e "s|/usr/share/rustdesk|/usr/share/$APP_NAME_KEBAB|g" \
      -e "s|/usr/bin/rustdesk|/usr/bin/$APP_NAME_KEBAB|g" \
      -e "s|/usr/share/$APP_NAME_KEBAB/rustdesk|/usr/share/$APP_NAME_KEBAB/$APP_NAME_KEBAB|g" \
      -e "s|/root/\.config/rustdesk|/root/.config/$APP_NAME_KEBAB|g" \
      -e "s|rustdesk\.service|$APP_NAME_KEBAB.service|g" \
      -e "s|service rustdesk |service $APP_NAME_KEBAB |g" \
      -e "s|systemctl enable rustdesk|systemctl enable $APP_NAME_KEBAB|g" \
      -e "s|systemctl start rustdesk|systemctl start $APP_NAME_KEBAB|g" \
      -e "s|systemctl stop rustdesk|systemctl stop $APP_NAME_KEBAB|g" \
      -e "s|systemctl disable rustdesk|systemctl disable $APP_NAME_KEBAB|g" \
      -e "s|rustdesk +--server|$APP_NAME_KEBAB +--server|g" \
      -e "s|stop rustdesk |stop $APP_NAME_KEBAB |g" \
      "$debfile"
    rm -f "$debfile.bak"
    echo "   patched res/DEBIAN/$(basename "$debfile")"
  fi
done

# Rename packaging assets so build.py / postinst agree on filenames.
if [[ -f "$SERVICE" ]]; then
  cp "$SERVICE" "$RDREPO/res/${APP_NAME_KEBAB}.service"
  echo "   wrote res/${APP_NAME_KEBAB}.service"
fi
if [[ -f "$RDREPO/res/pam.d/rustdesk.debian" ]]; then
  cp "$RDREPO/res/pam.d/rustdesk.debian" "$RDREPO/res/pam.d/${APP_NAME_KEBAB}.debian"
  echo "   wrote res/pam.d/${APP_NAME_KEBAB}.debian"
fi

# build.py packaging identity (deb control, install paths, dmg volname, outputs).
# Protect librustdesk.* so the native lib keeps linking.
BUILDPY="$RDREPO/build.py"
if [[ -f "$BUILDPY" ]]; then
  sed -i.bak \
    -e "s/librustdesk/__MIXEL_LIBRD__/g" \
    -e "s/hbb_name = 'rustdesk'/hbb_name = '$APP_NAME_KEBAB'/" \
    -e "s/Package: rustdesk/Package: $APP_NAME_KEBAB/" \
    -e "s/Maintainer: rustdesk <info@rustdesk.com>/Maintainer: $WIN_MANUFACTURER <support@mixel.ch>/" \
    -e "s|Homepage: https://rustdesk.com|Homepage: https://mixel.ch|" \
    -e "s|Description: A remote control software.|Description: $APP_DESCRIPTION|" \
    -e "s|/usr/share/rustdesk|/usr/share/$APP_NAME_KEBAB|g" \
    -e "s|/etc/rustdesk|/etc/$APP_NAME_KEBAB|g" \
    -e "s|apps/rustdesk.png|apps/$APP_NAME_KEBAB.png|g" \
    -e "s|apps/rustdesk.svg|apps/$APP_NAME_KEBAB.svg|g" \
    -e "s|applications/rustdesk.desktop|applications/$APP_NAME_KEBAB.desktop|g" \
    -e "s|applications/rustdesk-link.desktop|applications/$APP_NAME_KEBAB-link.desktop|g" \
    -e "s|pam.d/rustdesk|pam.d/$APP_NAME_KEBAB|g" \
    -e "s|tmpdeb/usr/bin/rustdesk|tmpdeb/usr/bin/$APP_NAME_KEBAB|g" \
    -e "s|res/rustdesk.service|res/$APP_NAME_KEBAB.service|g" \
    -e "s|rustdesk.deb|$APP_NAME_KEBAB.deb|g" \
    -e "s|rustdesk-%s.deb|$APP_NAME_KEBAB-%s.deb|g" \
    -e "s|RustDesk Installer|$APP_NAME Installer|g" \
    -e "s|rustdesk.dmg|$APP_NAME_KEBAB.dmg|g" \
    -e "s|rustdesk-{version}|$APP_NAME_KEBAB-{version}|g" \
    -e "s|bundle/deb/rustdesk|bundle/deb/$APP_NAME_KEBAB|g" \
    -e "s|/rustdesk\.exe|/$APP_NAME_KEBAB.exe|g" \
    -e "s/__MIXEL_LIBRD__/librustdesk/g" \
    "$BUILDPY"
  rm -f "$BUILDPY.bak"
  echo "   patched build.py packaging identity"

  # Support target-specific paths on macOS cross-compilation when CARGO_BUILD_TARGET is set
  export BUILDPY
  python3 << 'EOF'
import os
build_py = os.environ.get("BUILDPY")
with open(build_py, 'r') as f:
    code = f.read()

old_dylib = 'system2(\n        "cp target/release/liblibrustdesk.dylib target/release/librustdesk.dylib")'
new_dylib = '''_target = os.environ.get("CARGO_BUILD_TARGET")
    _prefix = f"target/{_target}/release" if _target else "target/release"
    shutil.copy2(f"{_prefix}/liblibrustdesk.dylib", f"{_prefix}/librustdesk.dylib")
    if _target:
        os.makedirs("target/release", exist_ok=True)
        shutil.copy2(f"{_prefix}/liblibrustdesk.dylib", "target/release/liblibrustdesk.dylib")
        shutil.copy2(f"{_prefix}/liblibrustdesk.dylib", "target/release/librustdesk.dylib")'''

code = code.replace(old_dylib, new_dylib)
code = code.replace(
    "'cp -rf ../target/release/service ",
    "f'cp -rf ../target/{os.environ.get(\"CARGO_BUILD_TARGET\") + \"/\" if os.environ.get(\"CARGO_BUILD_TARGET\") else \"\"}release/service "
)

with open(build_py, 'w') as f:
    f.write(code)
EOF
  echo "   patched build.py to support target-specific build directories"
fi

# RPM specs (best-effort; CI ships .deb today).
for rpm in "$RDREPO"/res/rpm*.spec; do
  [[ -f "$rpm" ]] || continue
  sed -i.bak \
    -e "s/librustdesk/__MIXEL_LIBRD__/g" \
    -e "s/rustdesk/$APP_NAME_KEBAB/g" \
    -e "s/RustDesk/$APP_NAME/g" \
    -e "s|https://rustdesk.com|https://mixel.ch|g" \
    -e "s/info@rustdesk.com/support@mixel.ch/g" \
    -e "s/__MIXEL_LIBRD__/librustdesk/g" \
    "$rpm"
  rm -f "$rpm.bak"
  echo "   patched $(basename "$rpm")"
done

# 2FA issuer shown in authenticator apps.
AUTH_2FA="$RDREPO/src/auth_2fa.rs"
if [[ -f "$AUTH_2FA" ]]; then
  sed -i.bak "s|const ISSUER: &str = \"RustDesk\";|const ISSUER: \&str = \"$APP_NAME\";|" "$AUTH_2FA"
  rm -f "$AUTH_2FA.bak"
  echo "   patched src/auth_2fa.rs ISSUER"
fi

# Windows printer driver display names.
PRINTER_CPP="$RDREPO/res/msi/CustomActions/RemotePrinter.cpp"
if [[ -f "$PRINTER_CPP" ]]; then
  sed -i.bak \
    -e "s|RustDesk Printer|$APP_NAME Printer|g" \
    -e "s|RustDeskPrinterDriver|MixelRemotePrinterDriver|g" \
    -e "s|RustDesk v4 Printer Driver|$APP_NAME v4 Printer Driver|g" \
    "$PRINTER_CPP"
  rm -f "$PRINTER_CPP.bak"
  echo "   patched RemotePrinter.cpp display names"
fi

# MSI Add/Remove Program links — point at Mixel, not github.com/rustdesk.
ARP="$RDREPO/res/msi/Package/Fragments/AddRemoveProperties.wxs"
if [[ -f "$ARP" ]]; then
  sed -i.bak "s|https://github.com/rustdesk/rustdesk|https://ism.mixel.ch|g" "$ARP"
  rm -f "$ARP.bak"
  echo "   patched MSI ARP URLs → ism.mixel.ch"
fi

# 7.5. Extra rebrand patches for privilege scripts, CLI details, and copyright strings.
echo "→ Applying extra rebrand patches (privilege scripts, CLI, copyright, etc.)"

# macOS privileges scripts — change com.carriez.RustDesk to com.carriez.rustdesk
# so that the runtime `correct_app_name` function successfully replaces it with $MACOS_BUNDLE_ID
echo "   patching macOS privileges scripts to use lowercase com.carriez.rustdesk"
for script in "$RDREPO"/src/platform/privileges_scripts/*; do
  if [[ -f "$script" ]]; then
    sed -i.bak -e "s/com.carriez.RustDesk/com.carriez.rustdesk/g" "$script"
    rm -f "$script.bak"
  fi
done

# macOS updater mount point
patch_string src/platform/macos.rs \
  "let mount_point = \"/Volumes/RustDeskUpdate\";" \
  "let mount_point = \"/Volumes/${APP_NAME_CAMEL}Update\";"

# Windows MessageBoxW caption
patch_string src/platform/windows.rs \
  "let caption = \"RustDesk Output\"" \
  "let caption = \"$APP_DISPLAY_NAME Output\""

# CLI help and info in main.rs
patch_string src/main.rs \
  "App::new(\"rustdesk\")" \
  "App::new(\"$APP_NAME_KEBAB\")"
patch_string src/main.rs \
  ".author(\"Purslane Ltd<info@rustdesk.com>\")" \
  ".author(\"$WIN_MANUFACTURER <support@mixel.ch>\")"
patch_string src/main.rs \
  ".about(\"RustDesk command line tool\")" \
  ".about(\"$APP_DISPLAY_NAME command line tool\")"

# UAC elevation prompt and plugin manager messages
patch_string src/plugin/manager.rs \
  "\"RustDesk wants to install then plugin\"" \
  "\"$APP_DISPLAY_NAME wants to install the plugin\""
patch_string src/plugin/manager.rs \
  "\"RustDesk wants to uninstall the plugin\"" \
  "\"$APP_DISPLAY_NAME wants to uninstall the plugin\""

# Linux virtual input device name
patch_string src/server/uinput.rs \
  ".name(\"RustDesk UInput Keyboard\")" \
  ".name(\"$APP_DISPLAY_NAME UInput Keyboard\")"

# Windows Privacy mode window name
patch_string src/privacy_mode/win_topmost_window.rs \
  "\"RustDeskPrivacyWindow\"" \
  "\"${APP_NAME}PrivacyWindow\""

# Linux system message notification title
PLATFORM_MOD_RS="libs/hbb_common/src/platform/mod.rs"
patch_string "$PLATFORM_MOD_RS" \
  "\"RustDesk\"," \
  "\"$APP_DISPLAY_NAME\","

# Flutter about page copyright
patch_string flutter/lib/desktop/pages/desktop_setting_page.dart \
  "Purslane Ltd." \
  "$WIN_MANUFACTURER"

# Old Sciter copyright in index.tis
patch_string src/ui/index.tis \
  "Copyright &copy; 2025 Purslane Ltd." \
  "Copyright \&copy; 2026 $WIN_MANUFACTURER."

# Xcode bundle identifier definition in pbxproj
patch_string flutter/macos/Runner.xcodeproj/project.pbxproj \
  "PRODUCT_BUNDLE_IDENTIFIER = com.carriez.rustdesk;" \
  "PRODUCT_BUNDLE_IDENTIFIER = $MACOS_BUNDLE_ID;"

# Cargo.toml metadata copyrights
patch_string Cargo.toml \
  "LegalCopyright = \"Copyright © 2025 Purslane Ltd. All rights reserved.\"" \
  "LegalCopyright = \"Copyright © 2026 $WIN_MANUFACTURER. All rights reserved.\""
patch_string libs/portable/Cargo.toml \
  "LegalCopyright = \"Copyright © 2025 Purslane Ltd. All rights reserved.\"" \
  "LegalCopyright = \"Copyright © 2026 $WIN_MANUFACTURER. All rights reserved.\""

# 7b. DEEP REBRAND — remove the remaining "rustdesk" traces a Windows admin
#     would see in %LOCALAPPDATA%, Task Manager, and the outer exe's file
#     Details tab. (Process name is already mixel-remote.exe via build.py's
#     `generate.py -e mixel-remote.exe` + CMake BINARY_NAME; config dir +
#     *.toml are already APP_NAME-derived. These are what's left.)
#
#     Portable self-extractor: the launcher unpacks the runtime into
#     %LOCALAPPDATA%\<APP_PREFIX> and, on Windows, spins a topmost helper
#     named RuntimeBroker_rustdesk.exe. Rebrand both.
patch_string libs/portable/src/main.rs \
  'const APP_PREFIX: &str = "rustdesk";' \
  "const APP_PREFIX: &str = \"$APP_NAME_KEBAB\";"
patch_string libs/portable/src/main.rs \
  'RuntimeBroker_rustdesk.exe' \
  "RuntimeBroker_${APP_NAME_KEBAB}.exe"
#     The privacy-mode (screen-blackout) impl CreateProcess-launches that
#     same injected helper by name — it MUST match the name the portable
#     packer copies to, or privacy mode silently fails on Windows. This
#     second reference was missed in the first deep-rebrand pass.
patch_string src/privacy_mode/win_topmost_window.rs \
  'RuntimeBroker_rustdesk.exe' \
  "RuntimeBroker_${APP_NAME_KEBAB}.exe"
#     Outer install exe's Windows version-resource (Details tab: Product
#     name / Original filename / Description). The crate name stays
#     `rustdesk-portable-packer` (internal, never shown).
patch_string libs/portable/Cargo.toml \
  'ProductName = "RustDesk"' \
  "ProductName = \"$APP_DISPLAY_NAME\""
patch_string libs/portable/Cargo.toml \
  'OriginalFilename = "rustdesk.exe"' \
  "OriginalFilename = \"${APP_NAME_KEBAB}.exe\""
patch_string libs/portable/Cargo.toml \
  'FileDescription = "RustDesk Remote Desktop"' \
  "FileDescription = \"$APP_DISPLAY_NAME\""
#     Fail loudly if the extract-dir constant didn't get rebranded — a
#     silent miss ships a %LOCALAPPDATA%\rustdesk folder again.
if grep -q 'const APP_PREFIX: &str = "rustdesk";' "$RDREPO/libs/portable/src/main.rs"; then
  echo "❌ APP_PREFIX still 'rustdesk' in libs/portable/src/main.rs (deep-rebrand miss)" >&2
  exit 1
fi
echo "   deep-rebrand: extract dir + RuntimeBroker helper + outer-exe file props -> $APP_NAME_KEBAB / $APP_DISPLAY_NAME"

# 7c. FULL REBRAND — rename the core native library file itself so nothing
#     inside the unpacked runtime folder says "rustdesk" either. The Rust
#     crate keeps [lib] name = "librustdesk" (so cargo still emits
#     librustdesk.{dll,so} and the exported symbols — rustdesk_core_main_args
#     etc., resolved by GetProcAddress/dlsym, NOT by filename — are
#     unchanged). We only rename the SHIPPED file and update the three
#     places that open it by name, in lockstep:
#       (1) the bundle copy/rename step  (2) the C++ runner LoadLibrary
#       (3) the Dart FFI DynamicLibrary.open
#     Miss any one and the app fails to load its core → the guards below
#     and the real-Windows launch gate catch it.
LIBNAME="lib${APP_NAME_KEBAB}"   # libmixel-remote

# --- Windows: CMake copies target/…/librustdesk.dll and RENAMEs it into the
#     bundle; rename the destination only (source stays librustdesk.dll).
patch_string flutter/windows/CMakeLists.txt \
  'COMPONENT Runtime RENAME librustdesk.dll)' \
  "COMPONENT Runtime RENAME ${LIBNAME}.dll)"
patch_string flutter/windows/runner/main.cpp \
  'LoadLibraryA("librustdesk.dll")' \
  "LoadLibraryA(\"${LIBNAME}.dll\")"
patch_string flutter/windows/runner/main.cpp \
  'Failed to load librustdesk.dll.' \
  "Failed to load ${LIBNAME}.dll."

# --- Linux: the .deb/.so keeps the cargo output name; rename in build.py's
#     strip step + the Dart loader. (build.py copies target/release/
#     librustdesk.so into the bundle lib dir under the same name; we rename
#     it there via a post-strip mv injected below is overkill — instead we
#     rename the Dart-opened filename AND the bundled file by patching the
#     flutter linux CMake install rename if present.)
patch_string flutter/lib/models/native_model.dart \
  "DynamicLibrary.open('librustdesk.dll')" \
  "DynamicLibrary.open('${LIBNAME}.dll')"

# --- Dart loader for Linux .so (two occurrences: android + desktop linux).
#     Only the desktop-linux path matters for the .deb; both are safe to
#     rename because the shipped file is renamed to match (see below).
patch_string flutter/lib/models/native_model.dart \
  "DynamicLibrary.open('librustdesk.so')" \
  "DynamicLibrary.open('${LIBNAME}.so')"

# --- Linux (mirror Windows exactly): the flutter linux CMake copies the
#     cargo .so into the bundle and RENAMEs it; rename the destination, and
#     update the GTK runner's dlopen (main.cc) that loads it by name. This is
#     the whole chain — no deb-staging mv needed. Verified: without the
#     main.cc patch the runner dlopen('librustdesk.so') fails and the app
#     won't start (caught by a real .deb launch test on the VPS).
patch_string flutter/linux/CMakeLists.txt \
  'COMPONENT Runtime RENAME librustdesk.so)' \
  "COMPONENT Runtime RENAME ${LIBNAME}.so)"
patch_string flutter/linux/main.cc \
  '#define RUSTDESK_LIB_PATH "librustdesk.so"' \
  "#define RUSTDESK_LIB_PATH \"${LIBNAME}.so\""
patch_string flutter/linux/main.cc \
  'Failed to load \"librustdesk.so\"' \
  "Failed to load \\\"${LIBNAME}.so\\\""

# Guards — a missed rename ships a rustdesk-named core or, worse, a runtime
# that can't find its library. Fail loudly at branding time.
if grep -q 'RENAME librustdesk.dll)' "$RDREPO/flutter/windows/CMakeLists.txt"; then
  echo "❌ Windows core dll still renamed to librustdesk.dll (full-rebrand miss)" >&2; exit 1
fi
if grep -q "DynamicLibrary.open('librustdesk.dll')" "$RDREPO/flutter/lib/models/native_model.dart" \
   || grep -q "DynamicLibrary.open('librustdesk.so')" "$RDREPO/flutter/lib/models/native_model.dart"; then
  echo "❌ Dart FFI still opens librustdesk.{dll,so} (full-rebrand miss)" >&2; exit 1
fi
echo "   full-rebrand: core library shipped as ${LIBNAME}.{dll,so} (crate name unchanged; symbols intact)"
#     NOTE (macOS): the .dylib is statically linked into the runner and lives
#     in Contents/Frameworks; renaming it entangles with Xcode linking and
#     the freshly-fixed notarization flow, so the mac dylib keeps its name.
#     It is the least-exposed file (inside the signed .app) and mac is not the
#     Store-blocked target (ToiToi = Windows).



# 8. Leak check — fail the build if key user-facing files still say RustDesk.
#    Deliberately ignores: web/bridge.dart identity check, comments, librustdesk,
#    internal type names, and the upstream clone directory name itself.
echo "→ Verifying no user-facing RustDesk identity remains"
leak=0
check_no_rustdesk () {
  local file="$1"
  local label="$2"
  [[ -f "$RDREPO/$file" ]] || return 0
  if grep -q 'RustDesk' "$RDREPO/$file"; then
    echo "❌ leak in $label ($file):" >&2
    grep -n 'RustDesk' "$RDREPO/$file" >&2 || true
    leak=1
  fi
}
check_no_rustdesk flutter/windows/runner/Runner.rc "Windows PE resources"
check_no_rustdesk flutter/windows/runner/main.cpp "Windows main.cpp"
check_no_rustdesk flutter/macos/Runner/Configs/AppInfo.xcconfig "macOS AppInfo"
check_no_rustdesk res/rustdesk.desktop "Linux desktop"
check_no_rustdesk res/rustdesk-link.desktop "Linux link desktop"
check_no_rustdesk res/rustdesk.service "systemd unit"
check_no_rustdesk src/auth_2fa.rs "2FA issuer"
check_no_rustdesk flutter/lib/desktop/widgets/tabbar_widget.dart "Flutter tabbar"

if grep -q 'ProductName.*RustDesk\|Name=RustDesk\|ISSUER.*RustDesk' \
  "$RDREPO/flutter/windows/runner/Runner.rc" \
  "$RDREPO/res/rustdesk.desktop" \
  "$RDREPO/src/auth_2fa.rs" 2>/dev/null; then
  leak=1
fi

# BINARY_NAME must be mixel-remote
if grep -q 'set(BINARY_NAME "rustdesk")' \
  "$RDREPO/flutter/windows/CMakeLists.txt" \
  "$RDREPO/flutter/linux/CMakeLists.txt" 2>/dev/null; then
  echo "❌ BINARY_NAME still rustdesk in CMakeLists.txt" >&2
  leak=1
fi

if [[ "$leak" -ne 0 ]]; then
  echo "❌ Branding leak check failed — refusing to ship a RustDesk-labelled build" >&2
  exit 1
fi
echo "   leak check passed"

echo "✓ Branding applied — product identity is $APP_NAME only."
