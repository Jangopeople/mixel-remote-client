#!/bin/bash
# w11connect.sh <dx> <remote id>: sign in the Win11 lab app (window offset dx from x=112) and connect unattended
cd "$(dirname "$0")"; d=$1
./q11.sh click $((1176+d)) 248 1280 800; sleep 3; ./q11.sh click $((474+d)) 483 1280 800; sleep 5
./q11.sh click $((215+d)) 186 1280 800; sleep 2; ./q11.sh click $((483+d)) 655 1280 800; sleep 6
./q11.sh click $((1012+d)) 358 1280 800; ./q11.sh type "$2"; ./q11.sh click $((832+d)) 536 1280 800; sleep 1; ./q11.sh click $((885+d)) 574 1280 800; sleep 12
