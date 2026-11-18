#!/bin/bash
# qabl.sh <label>: like vm/qab.sh but NO upload (the line is shaped): the lab binary stays on the VM and is copied to /root/sp/mixel-remote-qa-<label>
exec > "${LOG:-/root/lab-qabl.log}" 2>&1
export HOME=/root PATH=/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
X=http://${LAB_BIND:-10.124.1.97}:${LAB_PORT:-8771}
LABEL=$1; S=/root/lab/src-$LABEL; rm -rf $S; mkdir -p $S /root/sp
curl -fsS $X/src-$LABEL.tgz | tar -xz -C $S || { echo "BUILD FAILED (download)"; echo LN_DONE; exit 1; }
cd $S/next
# LAB_SIGNAL: the connection service this lab build talks to (a second broker), instead of the production one
[ -n "${LAB_SIGNAL:-}" ] && sed -i "s#wss://rs.mixel.ch:9444/v1/ws#$LAB_SIGNAL#" deployment/public-service-config.json
export MIXEL_DESKTOP_SERVICE_CONFIG=$S/next/deployment/public-service-config.json MIXEL_ENGINE_APP_VERSION=2.1.0 CARGO_TARGET_DIR=/root/lab/target-sp
T0=$(date +%s)
if nice -n 19 cargo build --release -p mixel_remote_linux --features qa-session > /root/lab/qa-build-$LABEL.log 2>&1; then
  cp /root/lab/target-sp/release/mixel-remote /root/sp/mixel-remote-qa-$LABEL && echo "built $LABEL $(stat -c %s /root/sp/mixel-remote-qa-$LABEL) bytes after $(( $(date +%s)-T0 )) s"
else
  grep -E "^error" -A9 /root/lab/qa-build-$LABEL.log | head -40; echo "BUILD FAILED $LABEL"
fi
rm -rf $S
echo LN_DONE
