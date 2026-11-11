#!/bin/bash
# hd-session.sh: Win10 at 1920x1080 (app maximized): sign both lab apps in, attended session Win11 -> Win10 with control.
cd "$(dirname "$0")"
./itask.sh qa-win10 6 "res.ps1 1920 1080" >/dev/null
./itask.sh qa-win10 5 max.ps1
./q.sh click 1854 57 1920 1080; sleep 3; ./q.sh click 607 486 1920 1080; sleep 7; ./q.sh click 105 177 1920 1080; sleep 2; ./q.sh click 601 633 1920 1080; sleep 5
./w11.sh hd0 >/dev/null; d=$(python3 -c "
from PIL import Image
im=Image.open('shots/hd0.png').convert('RGB')
for x in range(im.width):
    if min(im.getpixel((x,15)))>235: print(x-112); break")
./q11.sh click $((1176+d)) 248 1280 800; sleep 3; ./q11.sh click $((474+d)) 483 1280 800; sleep 6; ./q11.sh click $((215+d)) 186 1280 800; sleep 2; ./q11.sh click $((483+d)) 655 1280 800; sleep 6
./q11.sh click $((1012+d)) 358 1280 800; ./q11.sh type "196516669"; ./q11.sh click $((885+d)) 574 1280 800; sleep 7
./q.sh click 530 368 1920 1080; sleep 0.6; ./q.sh click 530 431 1920 1080; sleep 1; ./q.sh click 1536 671 1920 1080; sleep 12
echo dx=$d
