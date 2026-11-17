#!/bin/bash
# multi.sh <run> <linux-lab-binary> <mac-lab-app>: the two-sessions live check. Two Linux lab computers (VM 130 and VM 132,
# each on its own virtual display) are helped at the same time by this Mac's lab app (headless `--live-multi`, its own engine
# folder, QA login qa-linux-<QA_NAME>@example.test, default ui1117a): pictures of both, a pointer move to one only
# (this script reads both computers' pointers), ending one leaves the other. Needs xfer.py running and the lab binary
# in xfer/ (build it with push.sh + vm.sh <vm> qab.sh <label>). Output: run/<run>/ (mac.txt, host-a.txt, host-b.txt).
# Never touches the installed app: the Mac app is started by its lab path and stopped by its own PID.
set -u
L=$(cd "$(dirname "$0")" && pwd); R=$1; BIN=$2; APP=$3; NAME=${QA_NAME:-ui1117a}
O=$L/run/$R; rm -rf "$O"; mkdir -p "$O"; chmod 700 "$O"
"$L/mint.sh" $NAME 3 $R >/dev/null || { echo "mint failed"; exit 1; }
HA=/root/lab-host-$R-a; HB=/root/lab-host-$R-b
(LOG=/root/lab-host-$R-a.log WAIT=1500 "$L/vm.sh" 130 host.sh $R-a $R.1 $BIN 20 > "$O/host-a.txt" 2>&1 &)
(LOG=/root/lab-host-$R-b.log WAIT=1500 "$L/vm.sh" 132 host.sh $R-b $R.2 $BIN 20 > "$O/host-b.txt" 2>&1 &)
id() { "$L/vx.sh" $1 "cat /root/lab-host-$R-$2/work/host-id.txt 2>/dev/null"; }
A=""; B=""
for i in $(seq 1 120); do
  [ -z "$A" ] && A=$(id 130 a); [ -z "$B" ] && B=$(id 132 b)
  [ -n "$A" ] && [ -n "$B" ] && break; sleep 5
done
[ -n "$A" ] && [ -n "$B" ] || { echo "a lab computer did not come online (A='$A' B='$B')"; exit 1; }
echo "lab computers online: A=$A (VM 130) B=$B (VM 132)"
printf %s "$A" > "$O/host-id-a.txt"; printf %s "$B" > "$O/host-id-b.txt"
ptr() { "$L/vx.sh" $1 "DISPLAY=:98 xdotool getmouselocation | cut -d' ' -f1,2"; }
PA0=$(ptr 130); PB0=$(ptr 132); echo "pointers before: A [$PA0] B [$PB0]"
S=$L/xfer/qa-session-$R.json
"$APP/Contents/MacOS/desktop_app" --live-multi "$S" $R "$O" > "$O/mac.txt" 2> "$O/mac-stderr.txt" & MAC=$!
for i in $(seq 1 600); do [ -s "$O/input-a.txt" ] && break; kill -0 $MAC 2>/dev/null || break; sleep 1; done
if [ -s "$O/input-a.txt" ]; then
  sleep 4
  PA1=$(ptr 130); PB1=$(ptr 132); echo "pointers after the input to A: A [$PA1] B [$PB1]"
  if [ "$PA1" != "$PA0" ] && [ "$PB1" = "$PB0" ]; then ACK="A-moved B-still A=[$PA1] B=[$PB1] before B=[$PB0]"; else ACK="WRONG A=[$PA1] B=[$PB1] before A=[$PA0] B=[$PB0]"; fi
  printf %s "$ACK" > "$O/input-a.ack"
fi
wait $MAC; echo "mac exit $?"
rm -f "$S" "$S.1" "$S.2" "$L/xfer/qa-session-$R.1.json" "$L/xfer/qa-session-$R.2.json" "$L/xfer/qa-session-$R.json"
cat "$O/mac.txt"
for v in "130 a" "132 b"; do set -- $v; "$L/vx.sh" $1 "touch /root/lab-host-$R-$2/stop" >/dev/null; done
