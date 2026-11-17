#!/bin/bash
# linux-docker.sh [cargo args...]: runs cargo for the Linux app in the Docker image mixel-ui-linux-build against this worktree,
# default `test -p mixel_remote_linux`. Cache volumes: mixel-ui-registry, mixel-ui-git, mixel-ui-target. One heavy build at a time:
# the lock directory /tmp/mixel-heavy-build.lock is shared with the other heavy builds on this Mac.
#   next/tools/lab/linux-docker.sh                                              # tests
#   next/tools/lab/linux-docker.sh clippy -p mixel_remote_linux --tests -- -D warnings
set -eu
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
[ $# -eq 0 ] && set -- test -p mixel_remote_linux
until mkdir /tmp/mixel-heavy-build.lock 2>/dev/null; do sleep 15; done
trap 'rmdir /tmp/mixel-heavy-build.lock' EXIT
docker run --rm -v "$ROOT":/src -w /src/next -v mixel-ui-registry:/opt/cargo/registry -v mixel-ui-git:/opt/cargo/git -v mixel-ui-target:/target \
  -e CARGO_TARGET_DIR=/target -e MIXEL_ENGINE_APP_VERSION=2.1.0 -e MIXEL_DESKTOP_SERVICE_CONFIG=/src/next/deployment/public-service-config.json \
  mixel-ui-linux-build cargo "$@"
