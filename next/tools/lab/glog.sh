#!/bin/bash
# glog.sh <vm> [lines]: the status log and the command answers of the GTK lab app
"$(dirname "$0")/vx.sh" $1 "tail -n ${2:-5} /root/gui/status.log | cut -c1-400; echo --; tail -n 5 /root/gui/status.log.cmd.log 2>/dev/null | cut -c1-400"
