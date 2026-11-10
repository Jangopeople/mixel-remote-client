#!/usr/bin/env python3
"""Phase-1 usability fixes for the managed app frontend (idempotent, exact-match).
 1. After sign-in, a Mixel team account got no pointer to device setup (the link was shown to personal
    accounts only). Show "Set up this computer" for team accounts too and say what to do next.
 2. The "macOS permissions" panel was shown on Windows and Linux. Show it on macOS only.
 3. Mac-only wording on other platforms ("Make this Mac available", "My Mac").
 4. The consent hint kept asking to select Keyboard & mouse after it was selected.
 5. A refused connection (account not assigned to the device's company) said only "unavailable".
Usage: patch-phase1-ui.py [--check] <next-root>"""
import sys, pathlib
check='--check' in sys.argv; root=pathlib.Path([a for a in sys.argv[1:] if a!='--check'][0]); f=root/'frontends/desktop'; R='../../crates/desktop_app/src/'
EDITS=[
 ('main.ts',"deviceSettings.hidden = phase !== 'authenticated' || !account?.account?.personal;",
  "deviceSettings.hidden = phase !== 'authenticated' || !(account?.account?.personal || account?.account?.staff);\n    deviceSettings.textContent = account?.account?.staff ? 'Set up this computer' : 'Register a device';"),
 ('main.ts',"if (account) accountStatus.textContent = account.message + (account.account?.display_name ? ` · ${account.account.display_name}` : '');",
  "if (account) accountStatus.textContent = account.message + (account.account?.display_name ? ` · ${account.account.display_name}` : '') + (phase === 'authenticated' ? '. Next: choose “' + (account.account?.staff ? 'Set up this computer' : 'Register a device') + '” to give this computer its connection ID, then open Remote control.' : '');"),
 ('index.html','<div class="panel" data-native-host-only><h2>macOS permissions</h2>','<div class="panel mac-only" data-native-host-only><h2>macOS permissions</h2>'),
 ('main.ts',"signOut.textContent = phase === 'authenticated' ? 'Sign out' : 'Cancel sign-in';","signOut.textContent = phase === 'authenticated' ? 'Sign out' : 'Cancel sign-in';\n    (signIn.parentElement as HTMLElement).dataset.phase = phase ?? '';"),
 ('index.html','<a id="account-device-settings" class="button-secondary" href="#setup" hidden>','<a id="account-device-settings" class="button-primary" href="#setup" hidden>'),
 ('devices.ts','Make this Mac available before connecting.','Make this computer available before connecting.'),
 ('index.html','placeholder="My Mac"','placeholder="My computer"'),
 ('remote.ts',": 'Select Keyboard & mouse to allow control for this session.';",": allowControl.checked ? 'Keyboard and mouse control will be allowed for this session.' : 'Select Keyboard & mouse to allow control for this session.';"),
 (R+'remote_session.rs','"The remote device or connection service is unavailable. Ask the other person to make their computer available, then try again."','"The remote device is not available to you. Ask the other person to make their computer available. If it is online, your account may not be assigned to that device’s company: check Manage devices and company."'),
 ('style.css',None,'\nbody:not([data-remote-platform="macos"]) .mac-only{display:none}\n.account-actions{flex-wrap:wrap}.account-actions a.button-primary{display:inline-flex;align-items:center;justify-content:center;text-decoration:none;border:1px solid var(--brand);border-radius:6px;padding:9px 13px;min-height:36px;font-size:12px;font-weight:550;line-height:1.3}\n.account-actions[data-phase=authenticated] :is(#account-sign-in,#account-register-mixel,#account-sign-in-link){display:none}\n'),
]
bad=0
for name,old,new in EDITS:
    p=f/name; s=p.read_text()
    if new in s: continue
    if old is None:
        if not check: p.write_text(s+new)
        continue
    if s.count(old)!=1: print('MISMATCH',name,old[:60]); bad=1; continue
    if not check: p.write_text(s.replace(old,new))
print('check ok' if check and not bad else ('applied' if not bad else 'FAILED')); sys.exit(bad)
