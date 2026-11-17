#!/bin/bash
# mint.sh <qa-login-name> <count> <label>: throwaway QA sessions for the lab (login qa-linux-<name>@example.test, never a
# real person's account). Writes xfer/qa-session-<label>.json, xfer/qa-session-<label>.1.json, ... (0600, single use:
# mint again for every launch). The tokens are read at run time on the VPS and never printed or committed.
set -eu
L=$(cd "$(dirname "$0")" && pwd); NAME=$1; COUNT=$2; LABEL=$3
mkdir -p "$L/xfer"
R=/tmp/qa-mint-$LABEL.json
ssh -p 2222 mixel-vps "sudo python3 /tmp/qa-linux.py mint $NAME $R $COUNT && sudo chown \$(id -un) $R*" >/dev/null
for n in $(seq 0 $((COUNT-1))); do
  SUF=""; [ $n -gt 0 ] && SUF=".$n"
  ( umask 077; scp -q -P 2222 mixel-vps:$R$SUF "$L/xfer/qa-session-$LABEL$SUF.json" )
  chmod 600 "$L/xfer/qa-session-$LABEL$SUF.json"
done
ssh -p 2222 mixel-vps "rm -f $R $R.*" >/dev/null
echo "minted $COUNT session(s) for qa-linux-$NAME@example.test as xfer/qa-session-$LABEL*.json"
