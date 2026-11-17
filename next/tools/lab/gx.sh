#!/bin/bash
# gx.sh <vm> <xdotool-actions...>: runs xdotool with the actions on the lab GUI display :90, e.g. gx.sh 132 mousemove 77 177 click 1
VM=$1; shift; "$(dirname "$0")/vx.sh" $VM "export DISPLAY=:90; xdotool $*"
