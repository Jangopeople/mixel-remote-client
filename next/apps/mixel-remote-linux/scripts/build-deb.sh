#!/bin/bash
# build-deb.sh <version> <source-root> <out-dir>: the Mixel Remote .deb (x64) from this crate.
# Build on Debian 12 (glibc 2.36): the program must need no glibc symbol newer than 2.35 (Ubuntu
# 22.04), and only GTK 4.6 / libadwaita 1.1 APIs are compiled in, so it runs on Debian 12+ and Ubuntu
# 22.04+. Same package name and identity as the 0.1.x app, so it updates it in place.
#   Needs: rustup toolchain from rust-toolchain / workspace, libgtk-4-dev libadwaita-1-dev
#   libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libasound2-dev libcups2-dev gettext dpkg-dev
set -euo pipefail
V=$1; SRC=$2; OUT=$3
GLIBC_FLOOR=2.35
APP=$SRC/next/apps/mixel-remote-linux
. /etc/os-release; [ "$ID" = debian ] && [ "$VERSION_ID" = 12 ] || { echo "build on Debian 12 only (this is $PRETTY_NAME)"; exit 1; }
mkdir -p "$OUT"
cd "$SRC/next"
export MIXEL_DESKTOP_SERVICE_CONFIG=$SRC/next/deployment/public-service-config.json MIXEL_ENGINE_APP_VERSION=$V
cargo build --locked --release -p mixel_remote_linux
B=${CARGO_TARGET_DIR:-$SRC/next/target}/release/mixel-remote
"$B" --version | grep -qx "Mixel Remote $V"
strings "$B" | grep -q MIXEL_QA_SESSION_FILE && { echo "lab hook in a release build"; exit 1; }

ROOT=$(mktemp -d); trap 'rm -rf "$ROOT"' EXIT
install -Dm755 "$B" "$ROOT/usr/bin/mixel-remote"
strip --strip-unneeded "$ROOT/usr/bin/mixel-remote"
# The 0.1.x program name: start-up entries and the updater of 0.1.x computers start this path.
ln -s mixel-remote "$ROOT/usr/bin/desktop_app"
install -Dm644 "$APP/data/ch.mixel.remote.desktop" "$ROOT/usr/share/applications/ch.mixel.remote.desktop"
install -Dm644 "$APP/data/ch.mixel.remote.metainfo.xml" "$ROOT/usr/share/metainfo/ch.mixel.remote.metainfo.xml"
for size in 16 32 48 64 128 256 512; do
  install -Dm644 "$SRC/branding/icon-$size.png" "$ROOT/usr/share/icons/hicolor/${size}x${size}/apps/ch.mixel.remote.png"
done
for lang in de fr it; do
  mkdir -p "$ROOT/usr/share/locale/$lang/LC_MESSAGES"
  msgfmt --check -o "$ROOT/usr/share/locale/$lang/LC_MESSAGES/mixel-remote.mo" "$APP/po/$lang.po"
done
install -Dm644 /dev/stdin "$ROOT/usr/share/doc/mixel-remote/copyright" <<'EOF'
Mixel Remote — © Mixel IT and Corporate Services. All rights reserved.
Terms: https://remote.mixel.ch/legal
EOF

# Libraries from the program itself (dpkg-shlibdeps), plus the tools the engine runs.
mkdir -p "$ROOT/DEBIAN" "$ROOT/debian"
printf 'Source: mixel-remote\n\nPackage: mixel-remote\n' > "$ROOT/debian/control"
LIBS=$(cd "$ROOT" && dpkg-shlibdeps -O usr/bin/mixel-remote 2>/dev/null | sed -n 's/^shlibs:Depends=//p')
rm -rf "$ROOT/debian"
TOOLS="libsecret-tools, xdg-utils, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good"
DEPENDS="$LIBS, $TOOLS"
SIZE=$(du -sk --exclude=DEBIAN "$ROOT" | cut -f1)
cat > "$ROOT/DEBIAN/control" <<EOF
Package: mixel-remote
Version: $V
Architecture: amd64
Maintainer: Mixel IT and Corporate Services <info@mixel.ch>
Installed-Size: $SIZE
Depends: $DEPENDS
Recommends: gstreamer1.0-pipewire, xdg-desktop-portal, cups-client, pkexec | policykit-1, gnome-keyring
Section: net
Priority: optional
Homepage: https://remote.mixel.ch
Description: Mixel Remote — remote support and access
 Connect to your company's computers and phones, or let a technician help on
 this computer, with consent for every session. Native GTK 4 app.
EOF
# preinst: the same packages, without versions (checked before anything is unpacked).
REQUIRED=$(printf '%s' "$DEPENDS" | tr ',' '\n' | sed 's/([^)]*)//g; s/ //g' | grep -v '^$' | tr '\n' ' ')
sed "s#@REQUIRED@#$REQUIRED#" "$APP/data/deb/preinst" > "$ROOT/DEBIAN/preinst"
install -m755 "$APP/data/deb/postinst" "$ROOT/DEBIAN/postinst"
chmod 755 "$ROOT/DEBIAN/preinst"
D="$OUT/mixel-remote_${V}_amd64.deb"
dpkg-deb --root-owner-group -Zxz --build "$ROOT" "$D" >/dev/null
dpkg-deb -f "$D" Package Version Depends

NEED=$(objdump -T "$ROOT/usr/bin/mixel-remote" | grep -o 'GLIBC_[0-9][0-9.]*' | sed 's/GLIBC_//' | sort -uV | tail -1)
{ echo "package: $(basename "$D")"; echo "built on: $PRETTY_NAME, $(ldd --version | sed -n 1p)"
  echo "highest GLIBC symbol version needed: $NEED (floor $GLIBC_FLOOR)"
  echo "GTK/adwaita symbols checked at start with LD_BIND_NOW on each test system"
  echo "NEEDED libraries:"; objdump -p "$ROOT/usr/bin/mixel-remote" | awk '/NEEDED/ {print "  " $2}'
  echo "Depends: $(dpkg-deb -f "$D" Depends)"; } > "$OUT/glibc.txt"
cat "$OUT/glibc.txt"
[ "$(printf '%s\n%s\n' "$NEED" "$GLIBC_FLOOR" | sort -V | tail -1)" = "$GLIBC_FLOOR" ] || { echo "needs glibc $NEED > $GLIBC_FLOOR"; exit 1; }
sha256sum "$D"
