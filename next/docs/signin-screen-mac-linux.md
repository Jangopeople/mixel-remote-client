# Sign-in screen access on macOS and Linux

Same model as Windows (`windows_signin_host.rs`): a separately enrolled device record
"<name> (sign-in screen)" with its own Remote ID and a computer-wide key; only policy-approved
unattended sessions; the host runs only while nobody is signed in. Turned on by a Mixel team
member (MFA) in Settings › Always-on access.

## Linux (X11 sign-in screen)
- Works with an X11 greeter: GDM with `WaylandEnable=false` in `/etc/gdm3/custom.conf`
  (Debian: `daemon.conf`), LightDM, SDDM on X11. A Wayland greeter (GDM default on Ubuntu)
  cannot be shared by any program; the app says so (`wayland_greeter`).
- Turning it on asks twice for an administrator password (pkexec):
  1. `--signin-provision`: key `/var/lib/mixel-remote/machine-identity-v1` (root, 0600).
  2. `--signin-install`: settings `/var/lib/mixel-remote/signin-host.json` (0600), program copy
     `/usr/lib/mixel-remote-signin/mixel-remote`, unit `/etc/systemd/system/mixel-remote-signin.service`.
- The service (`--signin-service`, root) watches `loginctl`: a greeter session of type x11 on
  seat0 and no signed-in local user → it starts `--signin-host` on that display with the X
  server's `-auth` file; someone signs in → the host stops. Log: `/var/lib/mixel-remote/signin-host.log`.
- Turn off: Settings › Always-on access › Turn off (`--signin-remove`).

## macOS (login window)
- macOS runs programs at the login window only as launchd agents with
  `LimitLoadToSessionType=LoginWindow` (root, in the login window's graphical session); a
  LaunchDaemon has no screen. Installed: `/Library/LaunchAgents/ch.mixel.remote.prelogin.plist`,
  host bundle (root-owned) `/Library/Application Support/Mixel Remote/Host/Mixel-Remote.app`,
  settings `/Library/Application Support/Mixel Remote/prelogin.json` (root, 0600), key in the
  System Keychain.
- Build: `deployment/macos-prelogin-bundle.sh <app> "<Developer ID>" [<profile>]` embeds the
  host (`ch.mixel.remote.prelogin`) at `Contents/Resources/Prelogin/` before notarising; only
  apps that carry it offer the setting.
- One-time steps at the Mac (cannot be done remotely):
  1. Settings › Always-on access › Turn on: two macOS administrator prompts (bundle + key,
     then settings + agent).
  2. Sign out once; at the login window macOS asks to allow screen recording for the login host
     (or allow it in System Settings › Privacy & Security › Screen Recording).
  3. Typing the password at the login window needs Apple's managed capability
     `com.apple.developer.hid.virtual.device` for `ch.mixel.remote.prelogin` (request at
     developer.apple.com, then build with the provisioning profile). Without it the host logs
     "Login window keyboard blocked" and stays off.
