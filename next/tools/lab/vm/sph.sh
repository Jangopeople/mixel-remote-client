#!/bin/bash
# host.sh <run> <label> <binary> [minutes]: on a lab VM the Linux lab app (xfer/<binary>) runs as a computer to be
# helped: its own virtual 1920x1080 X display, a throwaway HOME, D-Bus and keyring, the QA session
# xfer/qa-session-<label>.json. It registers like a real host and accepts the request itself (qa-live host mode),
# so no person is needed. It stays up for [minutes] (default 30) or until /root/lab-host-<run>/stop appears.
# Environment (set by the caller, passed on through the script arguments' environment):
#   LOOP=1   persistent: accepts one session after the other (attended or unattended) until <dir>/stop appears.
#            Commands: write lines to <dir>/cmd ("unattended on", "unattended off", "control off|on", "copy <text>").
#   KEEP=1   the device stays registered when the host stops; REUSE=1 starts again in the same directory with the
#            saved identity (a new QA session file is still needed to sign in): host restart checks.
#   UNATT=1  unattended access is set up at every start (admin setting, grant, switch).
#   MOTION=2 a real 1080p60 test picture (ffplay testsrc2, needs ffmpeg).
#   DNUM=98  X display number (several hosts on one VM need different ones); MOTION=1 the picture moves a lot.
exec > "${LOG:-/root/lab-host.log}" 2>&1
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
RUN=$1; LABEL=$2; BIN=$3; MIN=${4:-30}
X=http://${LAB_BIND:-10.124.1.97}:${LAB_PORT:-8771}; P=/root/lab-host-sp-$RUN; D=${DNUM:-98}
command -v Xvfb >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q xvfb x11-xserver-utils >/dev/null
command -v xdotool >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q xdotool >/dev/null
if [ "${REUSE:-0}" = 1 ] && [ -d $P/home ]; then rm -f $P/stop $P/work/stop; else rm -rf $P; mkdir -p $P/home $P/work; chmod 700 $P $P/home; fi
cp /root/sp/$BIN $P/mixel-remote && chmod 755 $P/mixel-remote || { echo "download failed"; echo LN_DONE; exit 1; }
curl -fsS -o $P/session.json $X/qa-session-$LABEL.json; chmod 600 $P/session.json
rm -f /tmp/.X$D-lock; Xvfb :$D -screen 0 1920x1080x24 -nolisten tcp > $P/xvfb.log 2>&1 & XV=$!; sleep 2
DISPLAY=:$D xsetroot -solid "#2a6f97" 2>/dev/null
# a moving picture, so a technician can see that pictures keep arriving
if [ "${MOTION:-0}" = 2 ]; then
  # a real moving 1080p60 picture (ffmpeg testsrc2 in an ffplay window): the engine's own rate shows, not the source's
  (while [ ! -e $P/stop ]; do DISPLAY=:$D ffplay -loglevel quiet -noborder -x 1920 -y 1080 -left 0 -top 0 -window_title lab-$RUN -f lavfi -i "testsrc2=size=1920x1080:rate=60" >/dev/null 2>&1; sleep 1; done) &
elif [ "${MOTION:-0}" = 1 ]; then
  (while [ ! -e $P/stop ]; do for c in "#2a6f97" "#97592a" "#2a9751" "#7a2a97" "#97912a"; do DISPLAY=:$D xsetroot -solid "$c" 2>/dev/null; sleep 0.12; done; done) &
else
  (while [ ! -e $P/stop ]; do for c in "#2a6f97" "#97592a" "#2a9751"; do DISPLAY=:$D xsetroot -solid "$c" 2>/dev/null; sleep 1; done; done) &
fi
cd $P && DISPLAY=:$D HOME=$P/home XDG_RUNTIME_DIR=$P/home XDG_SESSION_TYPE=x11 MIXEL_QA_HOST_LOOP=${LOOP:-0} MIXEL_QA_HOST_KEEP=${KEEP:-0} MIXEL_QA_HOST_UNATTENDED=${UNATT:-0} dbus-run-session -- bash -c "printf throwaway | gnome-keyring-daemon --unlock --components=secrets >/dev/null 2>&1; sleep 1; timeout $((MIN*60)) $P/mixel-remote --qa-live $P/session.json $RUN $P/work host x" 2>&1 | grep --line-buffered -v "^$\|Gtk-WARNING"
rm -f $P/session.json; kill $XV 2>/dev/null
touch $P/stop; echo LN_DONE
