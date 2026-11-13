#!/bin/sh
# Mixel Remote for Linux: install and add this computer to your company with a deploy link.
#
#   curl -fsSL https://remote.mixel.ch/install/linux.sh | sudo sh -s -- '<deploy token>'
#
# What it does (Ubuntu / Debian, 64-bit Intel/AMD):
#   1. reads the current release from the Mixel Remote update channel and checks its Ed25519
#      signature with the Mixel update key below, then the package's size and SHA-256;
#   2. installs the package with apt (dependencies included);
#   3. runs `mixel-remote --signin-enroll <token>`: the computer creates its own key (readable by
#      root only), enrols it for the company of the deploy link and starts the always-on service
#      (sign-in screen and X11 sessions; Wayland sessions need the person to approve sharing).
# Running it again is harmless: an enrolled computer keeps its Remote ID.
#
# Options: --deb FILE    install this package instead of downloading one (offline mirrors)
#          --channel C   update channel (default: stable)
set -eu

die() { printf 'Mixel Remote: %s\n' "$*" >&2; exit 1; }
# Deploy links on Linux work from this release on.
MIN_VERSION="0.1.22"
version_ok() { # $1 >= MIN_VERSION (numeric x.y.z)
  printf '%s %s\n' "$1" "$MIN_VERSION" | awk '{ split($1, a, /[.-]/); split($2, b, /[.-]/);
    for (i = 1; i <= 3; i++) { if ((a[i] + 0) > (b[i] + 0)) exit 0; if ((a[i] + 0) < (b[i] + 0)) exit 1 } exit 0 }'
}
say() { printf 'Mixel Remote: %s\n' "$*"; }

TOKEN=""
DEB=""
CHANNEL="stable"
while [ $# -gt 0 ]; do
  case "$1" in
    --deb) [ $# -ge 2 ] || die "--deb needs a file"; DEB="$2"; shift 2 ;;
    --channel) [ $# -ge 2 ] || die "--channel needs a name"; CHANNEL="$2"; shift 2 ;;
    -*) die "unknown option $1" ;;
    *) [ -z "$TOKEN" ] || die "only one deploy token"; TOKEN="$1"; shift ;;
  esac
done

printf '%s' "$TOKEN" | grep -Eq '^mrt_[A-Za-z0-9_-]{16,96}$' || die "the deploy token is missing or invalid (copy the whole command from the portal)"
printf '%s' "$CHANNEL" | grep -Eq '^[a-z0-9-]{1,20}$' || die "invalid channel"
[ "$(id -u)" = 0 ] || die "run this as administrator: put sudo in front of sh (... | sudo sh -s -- '<token>')"
[ "$(uname -s)" = Linux ] || die "this script is for Linux; use the Mac or Windows command for other computers"
case "$(uname -m)" in x86_64|amd64) ;; *) die "only 64-bit Intel/AMD computers are supported (this one is $(uname -m))" ;; esac
command -v apt-get >/dev/null 2>&1 && command -v dpkg >/dev/null 2>&1 || die "this script needs Ubuntu or Debian (apt and dpkg)"

WORK=$(mktemp -d /tmp/mixel-remote-install.XXXXXX)
trap 'rm -rf "$WORK"' EXIT INT TERM

if [ -z "$DEB" ]; then
  command -v curl >/dev/null 2>&1 || die "curl is needed"
  command -v openssl >/dev/null 2>&1 || die "openssl is needed to check the download (apt-get install openssl)"
  BASE="https://store-api.mixel.ch/storage/v1/object/public/remote-updates"
  say "checking the current release ($CHANNEL)..."
  curl -fsSL --proto '=https' --max-filesize 8192 -o "$WORK/latest.json" "$BASE/$CHANNEL/linux-x64/latest.json" \
    || die "the release information could not be downloaded"
  envelope=$(tr -d '\n\r' < "$WORK/latest.json")
  manifest_b64=$(printf '%s' "$envelope" | sed -n 's/.*"manifest"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9+/=]*\)".*/\1/p')
  signature_b64=$(printf '%s' "$envelope" | sed -n 's/.*"signature"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9+/=]*\)".*/\1/p')
  [ -n "$manifest_b64" ] && [ -n "$signature_b64" ] || die "the release information is invalid"
  printf '%s' "$manifest_b64" | openssl base64 -d -A > "$WORK/manifest.json" || die "the release information is invalid"
  printf '%s' "$signature_b64" | openssl base64 -d -A > "$WORK/manifest.sig" || die "the release information is invalid"
  # Public half of the Mixel Remote update signing key (the private key never leaves Mixel).
  cat > "$WORK/update-key.pem" <<'KEY'
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAVH4GxSv2qMt1stjW6y2ripUGjaNmq6T3moY/cFqTZUk=
-----END PUBLIC KEY-----
KEY
  openssl pkeyutl -verify -pubin -inkey "$WORK/update-key.pem" -rawin -in "$WORK/manifest.json" -sigfile "$WORK/manifest.sig" >/dev/null 2>&1 \
    || die "the release is not signed by Mixel (or openssl is older than 3.0); nothing was installed"
  field() { tr -d '\n\r' < "$WORK/manifest.json" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p"; }
  [ "$(field product)" = mixel-remote ] && [ "$(field platform)" = linux ] && [ "$(field arch)" = x64 ] && [ "$(field channel)" = "$CHANNEL" ] \
    || die "the release information is for another product"
  FILE=$(field file); SHA=$(field sha256); SIZE=$(field size); VERSION=$(field version)
  printf '%s' "$FILE" | grep -Eq '^files/[A-Za-z0-9._/-]+\.deb$' && ! printf '%s' "$FILE" | grep -q '\.\.' || die "the release information is invalid"
  printf '%s' "$SHA" | grep -Eq '^[0-9a-f]{64}$' && printf '%s' "$SIZE" | grep -Eq '^[0-9]{1,10}$' || die "the release information is invalid"
  version_ok "$VERSION" || die "deploy links on Linux need Mixel Remote $MIN_VERSION or newer; the current release is $VERSION. Try again after the next update."
  say "downloading Mixel Remote $VERSION..."
  DEB="$WORK/mixel-remote.deb"
  curl -fsSL --proto '=https' --max-filesize "$SIZE" -o "$DEB" "$BASE/$FILE" || die "the package could not be downloaded"
  [ "$(wc -c < "$DEB" | tr -d ' ')" = "$SIZE" ] || die "the download is incomplete"
  [ "$(sha256sum "$DEB" | cut -d' ' -f1)" = "$SHA" ] || die "the download does not match the signed release"
else
  [ -f "$DEB" ] || die "package $DEB not found"
  case "$DEB" in /*) ;; *) DEB="$(pwd)/$DEB" ;; esac
fi

say "installing..."
DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$DEB" >"$WORK/apt.log" 2>&1 || { tail -n 15 "$WORK/apt.log" >&2; die "the package could not be installed"; }
APP=""
for candidate in /usr/bin/mixel-remote /usr/bin/desktop_app; do
  [ -x "$candidate" ] && APP="$candidate" && break
done
[ -n "$APP" ] || die "Mixel Remote was installed but its program was not found"
"$APP" --validate-embedded-service >/dev/null 2>&1 \
  || die "Mixel Remote does not run on this system (it needs Ubuntu 24.04 or newer, or a comparable Debian); nothing was enrolled"

say "adding this computer to your company..."
"$APP" --signin-enroll "$TOKEN" || die "this computer could not be added (see the message above)"
sleep 6
if [ "$(cat /var/lib/mixel-remote/signin-host-state 2>/dev/null)" = wayland ]; then
  say "done. The computer is in Devices on https://remote.mixel.ch. Note: its screen uses Wayland, which cannot be shared"
  say "without someone approving it on screen. For always-on access use an X11 session (GDM: WaylandEnable=false in /etc/gdm3/custom.conf)."
else
  say "done. The computer is in Devices on https://remote.mixel.ch and reachable with always-on access."
fi
