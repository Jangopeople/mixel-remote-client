#!/bin/bash
# qab.sh <label>: on a lab VM (Debian 12), builds the Linux app of xfer/src-<label>.tgz as a LAB build (feature
# qa-session) and uploads it as mixel-remote-qa-<label> to the file server. The build cache stays in /root/lab/target-qa.
exec > "${LOG:-/root/lab-qab.log}" 2>&1
export HOME=/root PATH=/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
X=http://${LAB_BIND:-10.124.1.97}:${LAB_PORT:-8771}
LABEL=$1; S=/root/lab/src-$LABEL; rm -rf $S; mkdir -p $S
curl -fsS $X/src-$LABEL.tgz | tar -xz -C $S || { echo "BUILD FAILED (download)"; echo LN_DONE; exit 1; }
cd $S/next
export MIXEL_DESKTOP_SERVICE_CONFIG=$S/next/deployment/public-service-config.json MIXEL_ENGINE_APP_VERSION=2.1.0 CARGO_TARGET_DIR=/root/lab/target-qa
T0=$(date +%s)
if nice cargo build --release -p mixel_remote_linux --features qa-session > /root/lab/qa-build-$LABEL.log 2>&1; then
  curl -fsS -T /root/lab/target-qa/release/mixel-remote $X/mixel-remote-qa-$LABEL && echo "uploaded $LABEL $(stat -c %s /root/lab/target-qa/release/mixel-remote) bytes after $(( $(date +%s)-T0 )) s"
else
  grep -E "^error" -A9 /root/lab/qa-build-$LABEL.log | head -40; echo "BUILD FAILED $LABEL"
fi
rm -rf $S
echo LN_DONE
