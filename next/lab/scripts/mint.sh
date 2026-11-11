#!/bin/bash
# mint.sh <host>: fresh lab sign-in file for a VM (qa-win10 | qa-win11)
cd "$(dirname "$0")"
ssh mixel-vps 'sudo python3 /tmp/lab-session.py mint 1 /tmp/labs >/dev/null && sudo cat /tmp/labs/session-1.json && sudo rm -rf /tmp/labs' > s.json && chmod 600 s.json && scp -q s.json "$1:qa-lab/session.json" && rm s.json && echo minted
