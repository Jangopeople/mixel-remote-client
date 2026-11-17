#!/bin/bash
# macapp.sh start <session-file> | cmd "<line>" | stop: the Mac lab app (bundle ch.mixel.remote.labtest, never the installed app)
# in the background; QA session from mint.sh; commands through the lab command file (connect <id> [attended], end <id>, window <id>,
# tab <id>, show <id>). The PID is kept in /tmp/labmac/pid (only that PID is ever signalled).
set -u
APP="${LAB_APP:-/Volumes/MAC_Mini_Ext/Project_Mixel/mixel-remote-client/.worktrees/v210/next/apps/mixel-remote-apple/DerivedData/lab/Build/Products/Debug/Mixel Remote.app}"
mkdir -p /tmp/labmac; chmod 700 /tmp/labmac
case ${1:-} in
  start) ( umask 077; cp "$2" /tmp/labmac/session.json ); rm -f /tmp/labmac/cmd
         MIXEL_QA_SESSION_FILE=/tmp/labmac/session.json MIXEL_QA_COMMANDS=/tmp/labmac/cmd "$APP/Contents/MacOS/desktop_app" > /tmp/labmac/app.log 2>&1 &
         echo $! > /tmp/labmac/pid; echo "started $(cat /tmp/labmac/pid)";;
  cmd) echo "$2" >> /tmp/labmac/cmd;;
  stop) kill "$(cat /tmp/labmac/pid)"; echo stopped;;
esac
