#!/bin/bash
# w.sh <shotname> [click X Y]...  : clicks on the Windows VM then screenshots
L=/Volumes/MAC_Mini_Ext/Project_Mixel/mixel-remote-client/_scratch/lab; n=$1; shift
while [ "$1" = click ]; do ssh mixel-proxmox python3 /root/qa133-qmp.py click $2 $3 >/dev/null; shift 3; sleep ${W:-2}; done
bash /Volumes/MAC_Mini_Ext/Project_Mixel/mixel-craft/_scratch/remote-audit-20261110/shot.sh $L/shots/$n.png; echo $L/shots/$n.png
