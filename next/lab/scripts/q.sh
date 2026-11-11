#!/bin/bash
# q.sh <args for qa133b-qmp.py>   (VM 133 input: click X Y W H | key q.. | type "text")
ssh mixel-proxmox python3 /root/qa133b-qmp.py "$@" >/dev/null
