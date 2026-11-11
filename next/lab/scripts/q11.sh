#!/bin/bash
# q11.sh <args for qa134-qmp.py>  (VM 134 input, 1280x800 for clicks)
ssh mixel-proxmox python3 /root/qa134-qmp.py "$@" >/dev/null
