#!/bin/bash
# hosts.sh start|status|stop [hosts...]: the persistent lab hosts (ha, hb on one display each of VM 130/132; hc, hd second
# displays). Needs xfer.py running, the Linux lab binary mixel-remote-qa-$BIN in xfer/ and minted sessions
# xfer/qa-session-$LABEL.json (mint.sh <qa> 4 $LABEL: .json, .1, .2, .3). Hosts keep their identity (KEEP): "restart" starts one
# again with the next session file of $LABEL2 (mint.sh <qa> 4 $LABEL2) under the same ID.
#   BIN=t1 LABEL=h1 ./hosts.sh start        # ha hb hc hd
#   ./hosts.sh status                       # ID and last log lines
#   ./hosts.sh cmd ha "unattended off"      # lab command (see qa_live.rs lab_commands)
#   ./hosts.sh stop                         # stop files (the devices stay registered; remove them in the console)
set -u
L=$(cd "$(dirname "$0")" && pwd)
BIN=${BIN:-t1}; LABEL=${LABEL:-h1}
spec() { case $1 in ha) echo "130 h1 98 $LABEL";; hb) echo "132 h1 98 $LABEL.1";; hc) echo "130 h1 99 $LABEL.2";; hd) echo "132 h1 99 $LABEL.3";; esac; }
ACT=${1:-status}; shift || true
HOSTS=${*:-ha hb hc hd}
for H in $HOSTS; do
  read VM _ DN LBL <<< "$(spec $H)"
  case $ACT in
    start) ( cd $L; LOG=/root/lab-host-$H.log WAIT=15 TAILN=2 VMENV="LOOP=1 KEEP=1 UNATT=${UNATT:-1} MOTION=${MOTION:-1} DNUM=$DN" ./vm.sh $VM host.sh $H $LBL mixel-remote-qa-$BIN ${MINUTES:-600} >/dev/null 2>&1 & ) ;;
    restart) ( cd $L; LOG=/root/lab-host-$H.log WAIT=15 TAILN=2 VMENV="LOOP=1 KEEP=1 REUSE=1 UNATT=${UNATT:-1} MOTION=${MOTION:-1} DNUM=$DN" ./vm.sh $VM host.sh $H $LBL mixel-remote-qa-$BIN ${MINUTES:-600} >/dev/null 2>&1 & ) ;;
    status) echo "== $H (VM $VM)"; $L/vx.sh $VM "echo ID \$(cat /root/lab-host-$H/work/host-id.txt 2>/dev/null); tail -n ${N:-4} /root/lab-host-$H.log | cut -c1-220" ;;
    cmd) $L/vx.sh $VM "echo '$CMD' >> /root/lab-host-$H/work/cmd" ; shift ;;
    stop) $L/vx.sh $VM "touch /root/lab-host-$H/stop /root/lab-host-$H/work/stop" ;;
  esac
done
