#!/bin/bash
# tech.sh <run> <label> <binary> <host-id> [minutes]: on a lab VM the lab binary (xfer/<binary>) runs ONCE as a headless technician
# (qa-live technician mode) against <host-id>: a throwaway HOME, D-Bus and keyring, QA session xfer/qa-session-<label>.json. Its stderr
# (the "QA connect +ms ..." marks, "QA first picture ...") goes to $LOG; used to compare connect times of two versions.
exec > "${LOG:-/root/lab-tech.log}" 2>&1
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
RUN=$1; LABEL=$2; BIN=$3; HOST=$4; MIN=${5:-6}
X=http://${LAB_BIND:-10.124.1.97}:${LAB_PORT:-8771}; P=/root/lab-tech-$RUN; D=${DNUM:-95}
rm -rf $P; mkdir -p $P/home $P/work; chmod 700 $P $P/home
curl -fsS -o $P/mixel-remote $X/$BIN && chmod 755 $P/mixel-remote || { echo "download failed"; echo LN_DONE; exit 1; }
curl -fsS -o $P/session.json $X/qa-session-$LABEL.json; chmod 600 $P/session.json
rm -f /tmp/.X$D-lock; Xvfb :$D -screen 0 1440x900x24 -nolisten tcp > $P/xvfb.log 2>&1 & XV=$!; sleep 2
echo -n $HOST > $P/work/host-id.txt
cd $P && DISPLAY=:$D HOME=$P/home XDG_RUNTIME_DIR=$P/home XDG_SESSION_TYPE=x11 dbus-run-session -- bash -c "printf throwaway | gnome-keyring-daemon --unlock --components=secrets >/dev/null 2>&1; sleep 1; timeout $((MIN*60)) $P/mixel-remote --qa-live $P/session.json $RUN $P/work technician"
rm -f $P/session.json; kill $XV 2>/dev/null
echo LN_DONE
