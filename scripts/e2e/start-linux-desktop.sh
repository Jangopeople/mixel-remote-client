#!/bin/bash
set -eu
mkdir -p /home/guest/.config /home/guest/.local/share /home/guest/.cache
Xvfb :99 -screen 0 1100x800x24 -ac -nolisten tcp >/proofs/xvfb.log 2>&1 &
for _attempt in $(seq 1 50); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.1; done
xdpyinfo >/dev/null 2>&1
exec dbus-run-session -- bash -s <<'SESSION'
set -eu
export DBUS_SESSION_BUS_ADDRESS
printf "%s\n" "$DBUS_SESSION_BUS_ADDRESS" > /proofs/dbus-address
openbox >/proofs/openbox.log 2>&1 &
python3 /fixture.py >/proofs/fixture.log 2>&1 &
touch /proofs/desktop-ready
exec sleep infinity
SESSION
