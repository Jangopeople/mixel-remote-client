#!/bin/bash
# gstop.sh <vm>: stops the lab GUI of gui.sh and its X display
"$(dirname "$0")/vx.sh" $1 "touch /root/gui/stop; sleep 7; pkill -f /root/gui/mixel-remote; pkill -f 'Xvfb :90'; true" >/dev/null 2>&1; echo stopped
