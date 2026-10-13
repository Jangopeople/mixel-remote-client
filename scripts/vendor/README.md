# Pinned local URI plugin

`uni_links_desktop` contains the 13 runtime, package metadata and license files
from the existing locked dependency, version **0.1.7**. Original package archive:
`https://pub.dev/api/archives/uni_links_desktop-0.1.7.tar.gz`.
Archive SHA-256:
`692de81efc32ef72df56d428902afb5216d5f9e43d71c7b315d360acd7a1e115`.

Only `macos/Classes/UniLinksDesktopPlugin.swift` differs. The pinned plugin
consumes a new URL while its primary event listener is canceled, retains only
the first initial URL, and does not replay a later support URL when the listener
returns. The compiled original-plugin negative control reproduces that loss.

The correction retains at most one validated support intent: the latest one.
It delivers it once through the initial getter or the primary listener, in either
attachment order. An older cold support URI never supersedes a newer warm
intent. URI length, token, API key, authority and path checks follow the actual
generated Dart parser, including form decoding and duplicate-key ordering.
Ordinary URLs retain their original behavior. No connection starts here, and no
bearer value is logged. Customer consent remains in the native attended guard.

`patch-support-macos-uri.py` copies this package into the generated source tree
at `flutter/local_plugins/uni_links_desktop` and adds a path dependency override.
It validates the original locked version/source before writing. It never edits
the shared dependency cache. All 12 non-Mac files are checked byte-for-byte
against the pinned package in `test-support-macos-uri-routing.py`.

The source fixture uses generated Dart, an in-memory HTTP collector and the
pinned Swift plugin/Flutter selector fixture. It does not exercise the installed
app. `test-support-launch-macos.py` separately requires a fresh ordinary app,
then proves a new warm URI changes its empty guard to the runtime guard before
testing a distinct cold URI. Actual support-page detection and customer Accept
still require the signed candidate's real end-to-end connection check.
