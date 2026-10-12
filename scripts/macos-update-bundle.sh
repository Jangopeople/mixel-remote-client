#!/bin/sh
# Embedded normal macOS updater. Runs only after the OS authorizes this app.
set -eu
umask 077
[ "$#" -eq 8 ] || exit 1
source_dir=$1
user=$2
cur_pid=$3
app_name=$4
bundle_id=$5
with_service=$6
daemon_body=$7
agent_body=$8
case "$app_name:$bundle_id:$with_service" in Mixel-Remote:ch.mixel.remote:0|Mixel-Remote:ch.mixel.remote:1) ;; *) exit 1 ;; esac
case "$cur_pid" in ''|*[!0-9]*) exit 1 ;; esac
target="/Applications/$app_name.app"
daemon="/Library/LaunchDaemons/${bundle_id}_service.plist"
agent="/Library/LaunchAgents/${bundle_id}_server.plist"
label="${bundle_id}_server"
[ -d "$source_dir" ] && [ ! -L "$source_dir" ] || exit 1
source_dir=$(cd "$source_dir" && /bin/pwd -P)
case "$source_dir/" in "$target/"|"$target/"*) exit 1 ;; esac
case "$target/" in "$source_dir/"*) exit 1 ;; esac
if [ -e "$target" ] || [ -L "$target" ]; then
  [ -d "$target" ] && [ ! -L "$target" ] || exit 1
  [ "$(/usr/bin/stat -f '%d:%i' "$source_dir")" != "$(/usr/bin/stat -f '%d:%i' "$target")" ] || exit 1
fi
uid=$(/usr/bin/id -u "$user")
case "$uid" in ''|0|*[!0-9]*) exit 1 ;; esac

critical='Contents/MacOS/Mixel-Remote
Contents/MacOS/service
Contents/Frameworks/liblibrustdesk.dylib
Contents/Frameworks/FlutterMacOS.framework/FlutterMacOS'
team_of() {
  /usr/bin/codesign -d --verbose=4 "$1" 2>&1 | /usr/bin/sed -n 's/^TeamIdentifier=//p'
}
validate_bundle() {
  /usr/bin/codesign --verify --deep --strict "$1" >/dev/null 2>&1 || return 1
  [ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$1/Contents/Info.plist")" = "$bundle_id" ] || return 1
  [ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$1/Contents/Info.plist")" = "$app_name" ] || return 1
  [ -x "$1/Contents/MacOS/$app_name" ] && [ -x "$1/Contents/MacOS/service" ] || return 1
  [ "$(team_of "$1")" = "$team" ] || return 1
}
hash_bundle() {
  (cd "$1" && printf '%s\n' "$critical" | while IFS= read -r file; do
    [ -f "$file" ] || exit 1
    /usr/bin/shasum -a 256 "$file" || exit 1
  done)
}
verify_hashes() { (cd "$1" && /usr/bin/shasum -a 256 -c "$2" >/dev/null 2>&1); }
team=$(team_of "$source_dir")
case "$team" in ''|*[!A-Z0-9]*) exit 1 ;; esac
[ "${#team}" -eq 10 ] || exit 1
validate_bundle "$source_dir"
if [ -d "$target" ]; then validate_bundle "$target"; fi

# The private parent remains root-owned; staged files retain normal app ownership.
work=$(/usr/bin/mktemp -d /Applications/.mixel-update.XXXXXXXX)
stage="$work/new.app"
backup="$work/old.app"
old_moved=0
new_moved=0
services_stopped=0
committed=0
had_daemon=0
had_agent=0
stop_services() {
  /bin/launchctl bootout "gui/$uid" "$agent" 2>/dev/null || /bin/launchctl bootout "user/$uid" "$agent" 2>/dev/null || /bin/launchctl unload -w "$agent" || true
  /bin/launchctl unload -w "$daemon" || true
}
start_services() {
  /bin/launchctl load -w "$daemon" || return 1
  /bin/launchctl bootstrap "gui/$uid" "$agent" 2>/dev/null || /bin/launchctl bootstrap "user/$uid" "$agent" 2>/dev/null || /bin/launchctl load -w "$agent" || return 1
  /bin/launchctl kickstart -k "gui/$uid/$label" 2>/dev/null || /bin/launchctl kickstart -k "user/$uid/$label" 2>/dev/null || return 1
}
restore_plists() {
  if [ "$had_daemon" -eq 1 ]; then /bin/cp -p "$work/daemon.old" "$daemon" || return 1; else /bin/rm -f "$daemon" || return 1; fi
  if [ "$had_agent" -eq 1 ]; then /bin/cp -p "$work/agent.old" "$agent" || return 1; else /bin/rm -f "$agent" || return 1; fi
}
cleanup() {
  result=$1
  trap - 0 HUP INT TERM
  if [ "$committed" -ne 1 ]; then
    rollback_ok=1
    if [ "$new_moved" -eq 1 ]; then
      [ "$with_service" -ne 1 ] || stop_services
      /bin/mv "$target" "$work/failed.app" || rollback_ok=0
    fi
    if [ "$old_moved" -eq 1 ]; then
      if [ ! -e "$target" ] && /bin/mv "$backup" "$target" && verify_hashes "$target" "$work/old.sha256"; then :; else rollback_ok=0; fi
    fi
    if [ "$services_stopped" -eq 1 ]; then
      restore_plists || rollback_ok=0
      if [ "$had_daemon" -eq 1 ] && [ "$had_agent" -eq 1 ]; then start_services || rollback_ok=0; fi
    fi
    if [ "$rollback_ok" -ne 1 ]; then
      echo 'Mixel update failed; rollback requires recovery and private backup was retained.' >&2
      exit 1
    fi
  fi
  /bin/rm -rf "$work" || exit 1
  exit "$result"
}
trap 'cleanup $?' 0
trap 'exit 1' HUP INT TERM

if [ "$with_service" -eq 1 ]; then
  [ -f "$daemon" ] && [ ! -L "$daemon" ] && [ -f "$agent" ] && [ ! -L "$agent" ] || exit 1
  /bin/cp -p "$daemon" "$work/daemon.old"
  had_daemon=1
  /bin/cp -p "$agent" "$work/agent.old"
  had_agent=1
fi
if [ -d "$target" ]; then hash_bundle "$target" > "$work/old.sha256"; fi
/usr/bin/ditto "$source_dir" "$stage"
/usr/sbin/chown -R "$user:staff" "$stage"
validate_bundle "$stage"
hash_bundle "$source_dir" > "$work/new.sha256"
verify_hashes "$stage" "$work/new.sha256"
if [ "$with_service" -eq 1 ]; then
  printf '%s\n' "$daemon_body" > "$work/daemon.new"
  printf '%s\n' "$agent_body" > "$work/agent.new"
  /usr/bin/plutil -lint "$work/daemon.new" >/dev/null
  /usr/bin/plutil -lint "$work/agent.new" >/dev/null
  /usr/sbin/chown root:wheel "$work/daemon.new" "$work/agent.new"
  /bin/chmod 644 "$work/daemon.new" "$work/agent.new"
fi

# No installed bundle or service is touched until the complete stage is verified.
if [ "$with_service" -eq 1 ]; then services_stopped=1; stop_services; fi
pids=$(/usr/bin/pgrep -x "$app_name" | /usr/bin/grep -vx "$cur_pid" || true)
if [ -n "$pids" ]; then printf '%s\n' "$pids" | /usr/bin/xargs /bin/kill -9 || true; fi
if [ -d "$target" ]; then /bin/mv "$target" "$backup"; old_moved=1; fi
/bin/mv "$stage" "$target"
new_moved=1
validate_bundle "$target"
verify_hashes "$target" "$work/new.sha256"
if [ "$with_service" -eq 1 ]; then
  /bin/mv "$work/daemon.new" "$daemon"
  /bin/mv "$work/agent.new" "$agent"
  start_services
fi
committed=1
