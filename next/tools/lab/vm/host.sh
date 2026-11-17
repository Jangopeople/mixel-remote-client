#!/bin/bash
# host.sh <run> <label> <binary> [minutes]: on a lab VM the Linux lab app (xfer/<binary>) runs as a computer to be
# helped: its own virtual 1920x1080 X display :98, a throwaway HOME, D-Bus and keyring, the QA session
# xfer/qa-session-<label>.json. It registers like a real host and accepts the request itself (qa-live host mode),
# so no person is needed. It stays up for [minutes] (default 30) or until /root/lab-host-<run>/stop appears.
exec > "${LOG:-/root/lab-host.log}" 2>&1
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
RUN=$1; LABEL=$2; BIN=$3; MIN=${4:-30}
X=http://${LAB_BIND:-10.124.1.97}:${LAB_PORT:-8771}; P=/root/lab-host-$RUN
command -v Xvfb >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q xvfb x11-xserver-utils >/dev/null
command -v xdotool >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q xdotool >/dev/null
rm -rf $P; mkdir -p $P/home $P/work; chmod 700 $P $P/home
curl -fsS -o $P/mixel-remote $X/$BIN && chmod 755 $P/mixel-remote || { echo "download failed"; echo LN_DONE; exit 1; }
curl -fsS -o $P/session.json $X/qa-session-$LABEL.json; chmod 600 $P/session.json
Xvfb :98 -screen 0 1920x1080x24 -nolisten tcp > $P/xvfb.log 2>&1 & XV=$!; sleep 2
DISPLAY=:98 xsetroot -solid "#2a6f97" 2>/dev/null
# a moving picture, so a technician can see that pictures keep arriving
(while [ ! -e $P/stop ]; do for c in "#2a6f97" "#97592a" "#2a9751"; do DISPLAY=:98 xsetroot -solid "$c" 2>/dev/null; sleep 1; done; done) &
cd $P && DISPLAY=:98 HOME=$P/home XDG_RUNTIME_DIR=$P/home XDG_SESSION_TYPE=x11 dbus-run-session -- bash -c "printf throwaway | gnome-keyring-daemon --unlock --components=secrets >/dev/null 2>&1; sleep 1; timeout $((MIN*60)) $P/mixel-remote --qa-live $P/session.json $RUN $P/work host x" 2>&1 | grep --line-buffered -v "^$\|Gtk-WARNING" | tail -n 400
rm -f $P/session.json; kill $XV 2>/dev/null
touch $P/stop; echo LN_DONE
