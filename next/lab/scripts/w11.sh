#!/bin/bash
L=/Volumes/MAC_Mini_Ext/Project_Mixel/mixel-remote-client/_scratch/lab; n=$1; shift
while [ "$1" = click ]; do ssh mixel-proxmox python3 /root/qa134-qmp.py click $2 $3 1280 800 >/dev/null; shift 3; sleep ${W:-2}; done
ssh mixel-proxmox 'N=$(hostname); pvesh create /nodes/$N/qemu/134/monitor -command "screendump /tmp/vm134.ppm" >/dev/null 2>&1'; scp -q mixel-proxmox:/tmp/vm134.ppm $L/shots/$n.ppm && sips -s format png $L/shots/$n.ppm --out $L/shots/$n.png >/dev/null && rm $L/shots/$n.ppm; echo $L/shots/$n.png
