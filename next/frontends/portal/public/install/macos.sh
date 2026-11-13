#!/bin/sh
# Mixel Remote for Mac: install and add this Mac to your company with a deploy link.
#
#   curl -fsSL https://remote.mixel.ch/install/macos.sh | sudo sh
#
# It asks for the deploy token (paste it from the portal; it is not shown). The token is never on a
# command line, so it is neither in the shell history nor in the process list other users can read.
# An MDM (running as root) sets it in the environment instead, from its secret store:
#   export MIXEL_DEPLOY_TOKEN='<deploy token>'
#   curl -fsSL https://remote.mixel.ch/install/macos.sh | sh
# (from a non-root script: ... | sudo --preserve-env=MIXEL_DEPLOY_TOKEN sh; not sudo VAR=... sh, which
# puts the token in sudo's arguments).
#
# Run it in Terminal while the person who uses the Mac is signed in (or as root from an MDM while
# that person is signed in). What it does:
#   1. reads the current release from the Mixel Remote update channel, downloads it and checks its
#      size and SHA-256, that the app carries Mixel's Developer ID signature (team 5277F8NDH4) and
#      that the signed app is at least the minimum version below;
#   2. installs "Mixel Remote.app" in /Applications;
#   3. runs the app's `--signin-enroll` as the signed-in person with the token on its standard input,
#      without any window: it
#      creates the Mac's key in that person's login Keychain, enrols it for the company of the
#      deploy link and starts the always-on host for that account (a launch agent).
# Once per Mac, macOS asks the person to allow Mixel Remote under System Settings › Privacy &
# Security › Screen Recording and Accessibility (Accessibility can be pre-approved by an MDM).
# Running it again is harmless: an enrolled Mac keeps its Remote ID.
#
# Options: --app PATH     use this app bundle instead of downloading (it must be signed by Mixel)
#          --in-place     with --app: enrol with the app where it is, do not copy it to /Applications
#          --channel C    update channel (default: stable)
#
# Everything runs from main, called on the last line: if the download is cut off, sh only reads
# function definitions and nothing is executed.
set -eu

die() { printf 'Mixel Remote: %s\n' "$*" >&2; exit 1; }
say() { printf 'Mixel Remote: %s\n' "$*"; }
# Deploy links on the Mac work from this release on.
MIN_VERSION="0.1.22"
version_ok() { # $1 >= MIN_VERSION (numeric x.y.z)
  printf '%s %s\n' "$1" "$MIN_VERSION" | awk '{ split($1, a, /[.-]/); split($2, b, /[.-]/);
    for (i = 1; i <= 3; i++) { if ((a[i] + 0) > (b[i] + 0)) exit 0; if ((a[i] + 0) < (b[i] + 0)) exit 1 } exit 0 }'
}
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
TEAM="5277F8NDH4"
REQUIREMENT="anchor apple generic and certificate leaf[subject.OU] = \"$TEAM\""

main() {
  APP=""
  IN_PLACE=0
  CHANNEL="stable"
  while [ $# -gt 0 ]; do
    case "$1" in
      --app) [ $# -ge 2 ] || die "--app needs a path"; APP="$2"; shift 2 ;;
      --in-place) IN_PLACE=1; shift ;;
      --channel) [ $# -ge 2 ] || die "--channel needs a name"; CHANNEL="$2"; shift 2 ;;
      -*) die "unknown option $1" ;;
      *) die "$ARGUMENT_REFUSED" ;;
    esac
  done

  matches "$CHANNEL" '^[a-z0-9-]{1,20}$' || die "invalid channel"
  [ "$(uname -s)" = Darwin ] || die "this script is for macOS; use the Linux or Windows command for other computers"
  [ "$IN_PLACE" = 0 ] || [ -n "$APP" ] || die "--in-place needs --app"
  ROOT=0; [ "$(id -u)" = 0 ] && ROOT=1
  [ "$ROOT" = 1 ] || [ "$IN_PLACE" = 1 ] || die "run this as administrator: put sudo in front of sh (... | sudo sh)"

  # The person the Mac is set up for: the one signed in at the screen.
  CONSOLE_USER=$(/usr/bin/stat -f %Su /dev/console 2>/dev/null || true)
  if [ "$ROOT" = 1 ]; then
    case "$CONSOLE_USER" in ""|root|loginwindow|_mbsetupuser) die "nobody is signed in on this Mac: sign in first, then run the command again" ;; esac
    CONSOLE_UID=$(/usr/bin/id -u "$CONSOLE_USER")
  else
    CONSOLE_UID=$(/usr/bin/id -u)
  fi
  read_token

  WORK=$(/usr/bin/mktemp -d /tmp/mixel-remote-install.XXXXXX)
  MOUNT=""
  cleanup() { [ -z "$MOUNT" ] || /usr/bin/hdiutil detach -quiet "$MOUNT" >/dev/null 2>&1 || true; rm -rf "$WORK"; }
  trap cleanup EXIT INT TERM

  if [ -z "$APP" ]; then
    BASE="https://store-api.mixel.ch/storage/v1/object/public/remote-updates"
    say "checking the current release ($CHANNEL)..."
    /usr/bin/curl -fsSL --proto '=https' --max-filesize 8192 -o "$WORK/latest.json" "$BASE/$CHANNEL/macos-arm64/latest.json" \
      || die "the release information could not be downloaded"
    manifest_b64=$(tr -d '\n\r' < "$WORK/latest.json" | sed -n 's/.*"manifest"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9+/=]*\)".*/\1/p')
    [ -n "$manifest_b64" ] || die "the release information is invalid"
    printf '%s' "$manifest_b64" | /usr/bin/base64 -D > "$WORK/manifest.json" 2>/dev/null || die "the release information is invalid"
    field() { tr -d '\n\r' < "$WORK/manifest.json" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p"; }
    [ "$(field product)" = mixel-remote ] && [ "$(field platform)" = macos ] && [ "$(field channel)" = "$CHANNEL" ] \
      || die "the release information is for another product"
    FILE=$(field file); SHA=$(field sha256); SIZE=$(field size); VERSION=$(field version)
    if ! matches "$FILE" '^files/[A-Za-z0-9._/-]+\.dmg$' || matches "$FILE" '\.\.' \
        || ! matches "$SHA" '^[0-9a-f]{64}$' || ! matches "$SIZE" '^[0-9]{1,10}$'; then
      die "the release information is invalid"
    fi
    version_ok "$VERSION" || die "deploy links on the Mac need Mixel Remote $MIN_VERSION or newer; the current release is $VERSION. Try again after the next update."
    say "downloading Mixel Remote $VERSION..."
    /usr/bin/curl -fsSL --proto '=https' --max-filesize "$SIZE" -o "$WORK/mixel-remote.dmg" "$BASE/$FILE" || die "the app could not be downloaded"
    [ "$(wc -c < "$WORK/mixel-remote.dmg" | tr -d ' ')" = "$SIZE" ] || die "the download is incomplete"
    [ "$(/usr/bin/shasum -a 256 "$WORK/mixel-remote.dmg" | cut -d' ' -f1)" = "$SHA" ] || die "the download does not match the release"
    mkdir "$WORK/mnt"
    /usr/bin/hdiutil attach -quiet -nobrowse -readonly -noautoopen -mountpoint "$WORK/mnt" "$WORK/mixel-remote.dmg" || die "the disk image could not be opened"
    MOUNT="$WORK/mnt"
    APP="$MOUNT/Mixel Remote.app"
  fi
  [ -d "$APP" ] || die "app $APP not found"
  /usr/bin/codesign --verify --deep --strict -R="$REQUIREMENT" "$APP" 2>/dev/null || die "the app is not signed by Mixel (team $TEAM); nothing was installed"
  # The manifest above is not signed; the version inside the code-signed bundle is what counts.
  BUNDLE_VERSION=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$APP/Contents/Info.plist" 2>/dev/null || true)
  if ! matches "$BUNDLE_VERSION" '^[0-9]+\.[0-9]+\.[0-9]+' || ! version_ok "$BUNDLE_VERSION"; then
    die "deploy links on the Mac need Mixel Remote $MIN_VERSION or newer; this app is ${BUNDLE_VERSION:-of an unknown version}"
  fi

  if [ "$IN_PLACE" = 0 ]; then
    TARGET="/Applications/$(basename "$APP")"
    say "installing $TARGET..."
    rm -rf "$TARGET.new"
    /usr/bin/ditto "$APP" "$TARGET.new" || die "the app could not be copied to /Applications"
    rm -rf "$TARGET"
    mv "$TARGET.new" "$TARGET"
    # Owned like a drag-and-drop install by the person, so the app can update itself later.
    /usr/sbin/chown -R "$CONSOLE_USER":admin "$TARGET"
    /bin/chmod -R go-w "$TARGET"
    /usr/bin/codesign --verify --deep --strict -R="$REQUIREMENT" "$TARGET" 2>/dev/null || die "the installed app failed its signature check"
    APP="$TARGET"
  fi

  EXE=$(/usr/libexec/PlistBuddy -c "Print :CFBundleExecutable" "$APP/Contents/Info.plist" 2>/dev/null || true)
  matches "$EXE" '^[A-Za-z0-9._-]{1,64}$' || die "the app bundle is invalid"
  PROGRAM="$APP/Contents/MacOS/$EXE"

  say "adding this Mac to your company..."
  if [ "$ROOT" = 1 ]; then
    # In the person's own session, so the key goes into their login Keychain and the host starts there.
    # The token on standard input: printf is built into sh, so it is in no process's arguments.
    printf '%s\n' "$TOKEN" | /bin/launchctl asuser "$CONSOLE_UID" /usr/bin/sudo -u "$CONSOLE_USER" "$PROGRAM" --signin-enroll \
      || die "this Mac could not be added (see the message above)"
  else
    printf '%s\n' "$TOKEN" | "$PROGRAM" --signin-enroll || die "this Mac could not be added (see the message above)"
  fi
  say "done. The Mac is in Devices on https://remote.mixel.ch."
}

main "$@"
