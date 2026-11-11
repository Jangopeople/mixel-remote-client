#!/bin/bash
# Screenshot of the Windows test VM (works on the UAC secure desktop too).
out="$1"; ssh mixel-proxmox 'N=$(hostname); pvesh create /nodes/$N/qemu/133/monitor -command "screendump /tmp/vm133.ppm" >/dev/null 2>&1'
scp -q mixel-proxmox:/tmp/vm133.ppm "${out%.png}.ppm" && sips -s format png "${out%.png}.ppm" --out "$out" >/dev/null && rm -f "${out%.png}.ppm"
