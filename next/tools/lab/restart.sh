#!/bin/bash
# restart.sh <run> <mac-lab-app> [pause-seconds]: how long a computer takes to be reachable again after a restart. The Mac lab app
# (own engine folder, QA login qa-linux-<QA_NAME>@example.test, default ui1117a, never the installed app) enrols, goes online and
# is killed at once (SIGKILL: no goodbye, like a crash or an update replacing the program); [pause] seconds later (default 1) it
# starts again with the same identity and reports the time to "online". Output: run/<run>/first.txt, second.txt (+ -stderr).
set -u
L=$(cd "$(dirname "$0")" && pwd); R=$1; APP=$2; PAUSE=${3:-1}; NAME=${QA_NAME:-ui1117a}
O=$L/run/$R; rm -rf "$O"; mkdir -p "$O"; chmod 700 "$O"
"$L/mint.sh" $NAME 2 $R >/dev/null || { echo "mint failed"; exit 1; }
EXE="$APP/Contents/MacOS/desktop_app"
"$EXE" --live-restart "$L/xfer/qa-session-$R.json" $R "$O" first > "$O/first.txt" 2> "$O/first-stderr.txt"; echo "first exit $? (137 = killed as planned)"
sleep "$PAUSE"
"$EXE" --live-restart "$L/xfer/qa-session-$R.1.json" $R "$O" second > "$O/second.txt" 2> "$O/second-stderr.txt"; echo "second exit $?"
rm -f "$L/xfer/qa-session-$R.json" "$L/xfer/qa-session-$R.1.json"
cat "$O/first.txt" "$O/second.txt"; grep -h "registration\|QA registration" "$O/second-stderr.txt" | cut -c1-160
