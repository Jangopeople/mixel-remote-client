#!/bin/bash
# shotget.sh name : fetch C:\lab\<name>.png, downscale to 1000px wide into /tmp/wl/<name>.png and the evidence folder at full size
N=$1; scp -q qa-win-v210:C:/lab/$N.png /tmp/wl/$N.png; E=/Volumes/MAC_Mini_Ext/Project_Mixel/mixel-craft/releases/mixel-remote-2.1.0/evidence/windows; cp /tmp/wl/$N.png $E/; sips -Z ${2:-1000} /tmp/wl/$N.png --out /tmp/wl/${N}_s.png >/dev/null; echo /tmp/wl/${N}_s.png
