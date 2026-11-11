#!/bin/bash
# session.sh <dx>: sign both lab apps in and start an attended session Win11 -> Win10 with control.
# dx = Win11 window offset from x=112 (see a screenshot; 0, -26, -78 are common).
cd "$(dirname "$0")"; d=${1:-0}
./q.sh click 960 64 1024 768; sleep 3; ./q.sh click 364 483 1024 768; sleep 6; ./q.sh click 114 186 1024 768; sleep 2; ./q.sh click 361 637 1024 768; sleep 5
./q11.sh click $((1176+d)) 248 1280 800; sleep 3; ./q11.sh click $((474+d)) 483 1280 800; sleep 6; ./q11.sh click $((215+d)) 186 1280 800; sleep 2; ./q11.sh click $((483+d)) 655 1280 800; sleep 6
./q11.sh click $((1012+d)) 358 1280 800; ./q11.sh type "196516669"; ./q11.sh click $((885+d)) 574 1280 800; sleep 6
./q.sh click 287 366 1024 768; sleep 0.5; ./q.sh click 287 428 1024 768; sleep 1; ./q.sh click 903 669 1024 768; sleep 12
