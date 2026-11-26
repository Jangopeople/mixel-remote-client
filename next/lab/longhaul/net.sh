#!/bin/bash
# Two namespaces: "hostns" (10.9.0.1, the computer being helped) and "techns" (10.9.0.2, the technician),
# joined only through wan.py. a2b = host -> technician (video), b2a = technician -> host.
set -e
ip netns del hostns 2>/dev/null || true; ip netns del techns 2>/dev/null || true
pkill -f wan.py 2>/dev/null || true; rm -f /tmp/wan.ready; sleep 0.3
ip netns add hostns; ip netns add techns
python3 "$(dirname "$0")/wan.py" "$1" > /tmp/wan.log 2>&1 &
for i in $(seq 50); do [ -f /tmp/wan.ready ] && break; sleep 0.1; done
ip link set wana netns hostns; ip link set wanb netns techns
ip -n hostns addr add 10.9.0.1/24 dev wana; ip -n hostns link set wana up mtu 1400; ip -n hostns link set lo up
ip -n techns addr add 10.9.0.2/24 dev wanb; ip -n techns link set wanb up mtu 1400; ip -n techns link set lo up
echo "$1" > /tmp/wan.json
