#!/bin/bash
# Installs a dedicated LoginWindow agent. Run as root with reviewed local files.
set -euo pipefail
umask 077

if [[ "$(/usr/bin/id -u)" != 0 || $# != 2 ]]; then
    printf 'Usage: sudo %s signed-app private-enrollment-json\n' "$0" >&2
    exit 2
fi
source_app=$1
enrollment=$2
base='/Library/Application Support/Mixel Remote'
host="$base/Host"
app="$host/Mixel-Remote.app"
settings="$base/prelogin.json"
agent='/Library/LaunchAgents/ch.mixel.remote.prelogin.plist'
executable="$app/Contents/MacOS/Mixel-Remote"
requirement='=anchor apple generic and certificate leaf[subject.OU] = "5277F8NDH4" and (identifier "ch.mixel.remote.prelogin" or identifier "ch.mixel.remote.prelogin.qa")'

for path in "$base" "$host" /Library/LaunchAgents; do
    if [[ -e "$path" || -L "$path" ]]; then
        [[ -d "$path" && ! -L "$path" ]]
        [[ "$(/usr/bin/stat -f %u "$path")" == 0 ]]
        mode=$(/usr/bin/stat -f %Lp "$path")
        (( (8#$mode & 022) == 0 ))
    else
        /usr/bin/install -d -o root -g wheel -m 755 "$path"
    fi
done
for path in "$app" "$settings" "$agent"; do
    if [[ -e "$path" || -L "$path" ]]; then
        printf 'Login host path already exists: %s\n' "$path" >&2
        exit 2
    fi
done
[[ -f "$enrollment" && ! -L "$enrollment" ]]
stage=$(/usr/bin/mktemp -d "$host/.install.XXXXXX")
app_installed=0
settings_installed=0
agent_installed=0
cleanup() {
    status=$?
    if [[ $status != 0 ]]; then
        if [[ $agent_installed == 1 ]]; then /bin/rm -f "$agent"; fi
        if [[ $settings_installed == 1 ]]; then /bin/rm -f "$settings"; fi
        if [[ $app_installed == 1 ]]; then /bin/rm -rf "$app"; fi
    fi
    /bin/rm -rf "$stage"
}
trap cleanup EXIT

/usr/bin/ditto "$source_app" "$stage/Mixel-Remote.app"
/usr/bin/codesign --verify --deep --strict -R "$requirement" "$stage/Mixel-Remote.app"
signature=$(/usr/bin/codesign --display --verbose=2 "$stage/Mixel-Remote.app" 2>&1)
[[ "$signature" == *'Authority=Developer ID Application: '* ]]
/usr/sbin/chown -R root:wheel "$stage/Mixel-Remote.app"
/bin/chmod -R go-w "$stage/Mixel-Remote.app"
/usr/bin/install -m 600 "$enrollment" "$stage/prelogin.json"
"$stage/Mixel-Remote.app/Contents/MacOS/Mixel-Remote" --validate-prelogin-settings "$stage/prelogin.json"

cat > "$stage/agent.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>ch.mixel.remote.prelogin</string>
    <key>ProgramArguments</key>
    <array><string>$executable</string><string>--prelogin-host</string></array>
    <key>LimitLoadToSessionType</key>
    <string>LoginWindow</string>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$base/prelogin.log</string>
    <key>StandardErrorPath</key>
    <string>$base/prelogin.log</string>
</dict>
</plist>
PLIST
/usr/bin/plutil -lint "$stage/agent.plist" >/dev/null
/bin/mv -n "$stage/Mixel-Remote.app" "$app"
[[ ! -e "$stage/Mixel-Remote.app" ]]
app_installed=1
/bin/ln "$stage/prelogin.json" "$settings"
settings_installed=1
/bin/chmod 644 "$stage/agent.plist"
/bin/ln "$stage/agent.plist" "$agent"
agent_installed=1
printf 'Installed %s for the next macOS login window.\n' "$agent"
