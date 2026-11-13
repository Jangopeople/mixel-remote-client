#!/bin/sh
# Mixel Remote for Linux: install and add this computer to your company with a deploy link.
#
#   curl -fsSL https://remote.mixel.ch/install/linux.sh | sudo sh
#
# It asks for the deploy token (paste it from the portal; it is not shown). The token is never on a
# command line, so it is neither in the shell history nor in the process list other users can read.
# Deployment tools running as root set it in the environment instead, from their secret store:
#   export MIXEL_DEPLOY_TOKEN='<deploy token>'
#   curl -fsSL https://remote.mixel.ch/install/linux.sh | sh
# (from a non-root script: ... | sudo --preserve-env=MIXEL_DEPLOY_TOKEN sh; not sudo VAR=... sh, which
# puts the token in sudo's arguments).
#
# What it does (Ubuntu / Debian, 64-bit Intel/AMD):
#   1. reads the current release from the Mixel Remote update channel and checks its Ed25519
#      signature with the Mixel update key below, then the package's size and SHA-256;
#   2. installs the package with apt (dependencies included);
#   3. runs `mixel-remote --signin-enroll` with the token on its standard input: the computer
#      creates its own key (readable by root only), enrols it for the company of the deploy link
#      and starts the always-on service
#      (sign-in screen and X11 sessions; Wayland sessions need the person to approve sharing).
# Running it again is harmless: an enrolled computer keeps its Remote ID.
#
# Options: --deb FILE    install this package instead of downloading one (offline mirrors; the
#                        package is not checked, so only use one you got from Mixel)
#          --channel C   update channel (default: stable)
#
# Everything runs from main, called on the last line: if the download is cut off, sh only reads
# function definitions and nothing is executed.
set -eu

die() { printf 'Mixel Remote: %s\n' "$*" >&2; exit 1; }
# Deploy links on Linux work from this release on.
MIN_VERSION="0.1.22"
version_ok() { # $1 >= MIN_VERSION (numeric x.y.z)
  printf '%s %s\n' "$1" "$MIN_VERSION" | awk '{ split($1, a, /[.-]/); split($2, b, /[.-]/);
    for (i = 1; i <= 3; i++) { if ((a[i] + 0) > (b[i] + 0)) exit 0; if ((a[i] + 0) < (b[i] + 0)) exit 1 } exit 0 }'
}
say() { printf 'Mixel Remote: %s\n' "$*"; }
# $1 is one line matching the extended regex $2 (grep alone would accept any one matching line).
matches() {
  case "$1" in *"
"*) return 1 ;; esac
  printf '%s\n' "$1" | grep -Eq "$2"
}
# The deploy token: MIXEL_DEPLOY_TOKEN (deployment tools) or pasted at a prompt that does not show it.
# Never an argument: arguments are in the process list every user can read (ps) and stay in the
# shell history. The variable is removed right away, so nothing started from here inherits it.
read_token() {
  TOKEN=${MIXEL_DEPLOY_TOKEN:-}
  unset MIXEL_DEPLOY_TOKEN
  if [ -z "$TOKEN" ]; then
    ( : </dev/tty ) 2>/dev/null || die "no deploy token: run the command in a terminal (it asks for the token), or set MIXEL_DEPLOY_TOKEN"
    printf 'Mixel Remote: paste the deploy token and press Enter (it is not shown): ' >/dev/tty
    SAVED_TTY=$(stty -g </dev/tty 2>/dev/null || true)
    trap 'restore_tty; exit 130' INT TERM HUP
    stty -echo </dev/tty 2>/dev/null || true
    IFS= read -r TOKEN </dev/tty || TOKEN=""
    restore_tty
    trap - INT TERM HUP
    printf '\n' >/dev/tty
  fi
  # Pasting may add spaces or a carriage return; a second line stays and is refused.
  TOKEN=$(printf '%s' "$TOKEN" | tr -d ' \t\r')
  matches "$TOKEN" '^mrt_[A-Za-z0-9_-]{16,96}$' || die "the deploy token is missing or invalid (copy it from the deploy link in the portal)"
}
restore_tty() { [ -z "${SAVED_TTY:-}" ] || stty "$SAVED_TTY" </dev/tty 2>/dev/null || true; }
ARGUMENT_REFUSED="the deploy token is not passed on the command line (other users could read it in the process list): run the command without it and paste the token when asked, or set MIXEL_DEPLOY_TOKEN"

main() {
  DEB=""
  CHANNEL="stable"
  while [ $# -gt 0 ]; do
    case "$1" in
      --deb) [ $# -ge 2 ] || die "--deb needs a file"; DEB="$2"; shift 2 ;;
      --channel) [ $# -ge 2 ] || die "--channel needs a name"; CHANNEL="$2"; shift 2 ;;
      -*) die "unknown option $1" ;;
      *) die "$ARGUMENT_REFUSED" ;;
    esac
  done

  matches "$CHANNEL" '^[a-z0-9-]{1,20}$' || die "invalid channel"
  [ "$(id -u)" = 0 ] || die "run this as administrator: put sudo in front of sh (... | sudo sh)"
  [ "$(uname -s)" = Linux ] || die "this script is for Linux; use the Mac or Windows command for other computers"
  case "$(uname -m)" in x86_64|amd64) ;; *) die "only 64-bit Intel/AMD computers are supported (this one is $(uname -m))" ;; esac
  if ! command -v apt-get >/dev/null 2>&1 || ! command -v dpkg >/dev/null 2>&1; then
    die "this script needs Ubuntu or Debian (apt and dpkg)"
  fi
  read_token

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
    if ! matches "$FILE" '^files/[A-Za-z0-9._/-]+\.deb$' || matches "$FILE" '\.\.' \
        || ! matches "$SHA" '^[0-9a-f]{64}$' || ! matches "$SIZE" '^[0-9]{1,10}$'; then
      die "the release information is invalid"
    fi
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
    if [ -x "$candidate" ]; then APP="$candidate"; break; fi
  done
  [ -n "$APP" ] || die "Mixel Remote was installed but its program was not found"
  "$APP" --validate-embedded-service >/dev/null 2>&1 \
    || die "Mixel Remote does not run on this system (it needs Ubuntu 22.04 or newer, or Debian 12 or newer); nothing was enrolled"

  say "adding this computer to your company..."
  # On standard input: printf is built into sh, so the token is in no process's arguments.
  printf '%s\n' "$TOKEN" | "$APP" --signin-enroll || die "this computer could not be added (see the message above)"
  sleep 6
  if [ "$(cat /var/lib/mixel-remote/signin-host-state 2>/dev/null)" = wayland ]; then
    say "done. The computer is in Devices on https://remote.mixel.ch. Note: its screen uses Wayland, which cannot be shared"
    say "without someone approving it on screen. For always-on access use an X11 session (GDM: WaylandEnable=false in /etc/gdm3/custom.conf)."
  else
    say "done. The computer is in Devices on https://remote.mixel.ch and reachable with always-on access."
  fi
}

main "$@"
