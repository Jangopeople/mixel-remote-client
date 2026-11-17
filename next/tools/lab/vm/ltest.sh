#!/bin/bash
# ltest.sh <label> [package...]: on a lab VM, runs `cargo test` for the packages (default mixel_remote_linux) of xfer/src-<label>.tgz.
exec > "${LOG:-/root/lab-ltest.log}" 2>&1
export HOME=/root PATH=/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
X=http://${LAB_BIND:-10.124.1.97}:${LAB_PORT:-8771}
LABEL=$1; shift; PKGS=("$@"); [ ${#PKGS[@]} -eq 0 ] && PKGS=(mixel_remote_linux)
S=/root/lab/src-$LABEL; rm -rf $S; mkdir -p $S
curl -fsS $X/src-$LABEL.tgz | tar -xz -C $S || { echo "TEST FAILED (download)"; echo LN_DONE; exit 1; }
cd $S/next
export MIXEL_DESKTOP_SERVICE_CONFIG=$S/next/deployment/public-service-config.json MIXEL_ENGINE_APP_VERSION=2.1.0 CARGO_TARGET_DIR=/root/lab/target-test
ARGS=(); for p in "${PKGS[@]}"; do ARGS+=(-p "$p"); done
nice cargo test "${ARGS[@]}" 2>&1 | grep -E "^(test result|error|warning: unused|failures:|    [a-z_:]+$|test .* FAILED)" | head -60
echo "exit ${PIPESTATUS[0]}"
rm -rf $S
echo LN_DONE
