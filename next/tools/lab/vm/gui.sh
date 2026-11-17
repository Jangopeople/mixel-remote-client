#!/bin/bash
# gui.sh <label> <binary> [minutes]: on a lab VM the Linux lab app (xfer/<binary>) runs as a technician on its own virtual
# 1440x900 X display :90 with a throwaway HOME, D-Bus and keyring, signed in through the QA session file xfer/qa-session-<label>.json
# (the app reads MIXEL_QA_SESSION_FILE for "Sign in with Mixel ID"). Drive it with vx.sh: `DISPLAY=:90 xdotool ...`,
# screenshots with `DISPLAY=:90 import -window root /root/gui/x.png`. Stops after [minutes] or when /root/gui/stop appears.
# Extra environment: KEEP=1 keeps the throwaway HOME (the registered identity) between runs; THEME=dark starts GTK with Adwaita:dark; LANGV=de sets LANG.
exec > "${LOG:-/root/lab-gui.log}" 2>&1
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
LABEL=$1; BIN=$2; MIN=${3:-120}
X=http://${LAB_BIND:-10.124.1.97}:${LAB_PORT:-8771}; P=/root/gui
for c in Xvfb xdotool import openbox; do command -v $c >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q xvfb xdotool imagemagick x11-xserver-utils openbox >/dev/null; done
if [ "${KEEP:-0}" = 1 ] && [ -d $P/home ]; then rm -f $P/stop $P/cmd; else rm -rf $P; mkdir -p $P/home; chmod 700 $P $P/home; fi
curl -fsS -o $P/mixel-remote $X/$BIN && chmod 755 $P/mixel-remote || { echo "download failed"; echo LN_DONE; exit 1; }
curl -fsS -o $P/session.json $X/qa-session-$LABEL.json; chmod 600 $P/session.json
rm -f /tmp/.X90-lock; Xvfb :90 -screen 0 1440x900x24 -nolisten tcp > $P/xvfb.log 2>&1 & XV=$!; sleep 2
DISPLAY=:90 xsetroot -solid "#444444" 2>/dev/null
DISPLAY=:90 openbox > $P/openbox.log 2>&1 & OB=$!
cd $P && env GSK_RENDERER=${GSKR:-cairo} LIBGL_ALWAYS_SOFTWARE=1 DISPLAY=:90 HOME=$P/home XDG_RUNTIME_DIR=$P/home XDG_SESSION_TYPE=x11 LANG=${LANGV:-en_US.UTF-8} ${THEME:+GTK_THEME=Adwaita:$THEME} MIXEL_QA_SESSION_FILE=$P/session.json MIXEL_QA_STATUSLOG=$P/status.log MIXEL_QA_COMMANDS=$P/cmd dbus-run-session -- bash -c "printf throwaway | gnome-keyring-daemon --unlock --components=secrets >/dev/null 2>&1; sleep 1; timeout $((MIN*60)) $P/mixel-remote" 2>&1 | grep --line-buffered -v "^$\|Gtk-WARNING" &
APP=$!
while [ ! -e $P/stop ]; do sleep 5; done
pkill -P $APP 2>/dev/null; kill $OB $XV 2>/dev/null
echo LN_DONE
