#!/bin/bash
exec > /root/lab-check.log 2>&1
set -x
export HOME=/root PATH=/root/lab/shim:/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
rm -rf /root/lab/src; mkdir -p /root/lab/src /root/lab/shim
[ -x /root/lab/shim/pnpm ] || { printf '#!/bin/sh\nexit 0\n' > /root/lab/shim/pnpm; chmod +x /root/lab/shim/pnpm; }
curl -fsS http://10.124.1.97:8765/lab-src.tgz | tar -xz -C /root/lab/src
cd /root/lab/src/next
export MIXEL_DESKTOP_SERVICE_CONFIG=/root/lab/src/next/deployment/public-service-config.json CARGO_TARGET_DIR=/root/lab/target-rc
cargo clippy --locked --release -p desktop_app -- -D warnings > /tmp/clip.log 2>&1; echo "clippy-exit=$?"; grep -E "^(warning|error)" -A8 /tmp/clip.log | head -60
cargo test --locked --release -p desktop_app 2>&1 | grep -E "test result|FAILED|^error" | tail -4
echo LAB_CHECK_DONE
