#!/bin/bash
# gshot.sh <vm> <name> [dest-dir]: screenshot of the lab GUI display :90 (gui.sh) -> dest-dir/<name>.png (default evidence/linux)
set -eu
L=$(cd "$(dirname "$0")" && pwd); VM=$1; N=$2
D=${3:-/Volumes/MAC_Mini_Ext/Project_Mixel/mixel-craft/releases/mixel-remote-2.1.0/evidence/linux}; mkdir -p "$D"
"$L/vx.sh" $VM "DISPLAY=:90 import -window root /root/gui/$N.png && curl -fsS -T /root/gui/$N.png http://${LAB_BIND:-10.124.1.97}:${LAB_PORT:-8771}/shot-$N.png >/dev/null && echo ok"
mv "$L/xfer/shot-$N.png" "$D/$N.png"; echo "$D/$N.png"
