#!/bin/bash
# Embeds the Mac sign-in screen host into a built Mixel Remote app, before the app is signed.
#   deployment/macos-prelogin-bundle.sh <Mixel-Remote.app> <Developer ID identity> [<provisioning profile>]
# The host is the same program under its own identifier (ch.mixel.remote.prelogin), which the
# login window host requires (device_identity::prelogin_bundle). Typing at the login window needs
# Apple's managed capability com.apple.developer.hid.virtual.device: pass the provisioning profile
# Apple issued for ch.mixel.remote.prelogin with it. Without it the host stays off (it logs why).
set -euo pipefail
app=$1 identity=$2 profile=${3:-}
host="$app/Contents/Resources/Prelogin/Mixel-Remote.app"
rm -rf "$host"; mkdir -p "$(dirname "$host")"
ditto "$app" "$host"
rm -rf "$host/Contents/Resources/Prelogin"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier ch.mixel.remote.prelogin" "$host/Contents/Info.plist"
entitlements=$(mktemp -t prelogin-entitlements).plist
cat > "$entitlements" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>com.apple.developer.hid.virtual.device</key><true/>
</dict></plist>
PLIST
if [ -n "$profile" ]; then
  cp "$profile" "$host/Contents/embedded.provisionprofile"
  codesign --force --options runtime --timestamp --entitlements "$entitlements" --sign "$identity" "$host"
else
  echo "No provisioning profile: the login host is signed without the virtual keyboard entitlement" >&2
  codesign --force --options runtime --timestamp --sign "$identity" "$host"
fi
rm -f "$entitlements"
codesign --verify --strict "$host"
echo "Embedded $host"
