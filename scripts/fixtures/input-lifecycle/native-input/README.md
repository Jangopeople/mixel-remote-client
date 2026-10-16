# Portable native input component gate

Run from a freshly branded app with a new output directory:

```sh
python3 native_input_gate.py --source /path/to/just-branded-app --output /path/to/fresh-proof
```

The complete fixture directory must stay together. `--cargo` and `--rustc` can bind an installed toolchain; `--online` permits Cargo dependency downloads for an empty CI cache. Cargo is locked and offline by default. The inherited `CARGO_HOME` is preserved. `--prepare-only` validates and extracts without Cargo. Existing outputs refuse.

The gate validates eleven complete production source/protobuf identities before creating output. Uniform LF and CRLF checkouts are accepted against the same canonical LF source pins; the receipt separately records the actual raw size, hash and newline profile. Mixed newlines and source drift refuse. Thirty-six component files are reconstructed from those inputs and must match the reviewed fixture hashes before compilation.

The final R12 macOS expectations are 114 current passes, one passing original normal control, twenty required original/rejected-candidate failures, and a mixed run with 115 passes plus those same twenty failures. Required-negative and mixed Cargo exit101 is expected and checked; no ignored tests are allowed. Linux and Windows common-component expectations are 90 current passes, one normal pass, ten required negatives and mixed91/10. Their counts are derived from the frozen cfg inventory; actual Linux/Windows execution remains a separate CI gate.

Production selections include Session enqueue, IO permission/source/key/pointer handling, seven FFI entrypoints, native/UI conversion and release, modifier swapping, physical mouse modifiers, all three desktop receiver branch projections, current balanced Scale, real connection input queue and close/revoke cleanup, receiver button ownership and portable-helper exit paths. The native OS/layout/key-state/transport effects and registry/configuration are test seams. Real protobuf generation, rdev pure key tables, std_mpsc channels and flutter_rust_bridge1.80.1 executor are used. The tests do not launch the app, inject OS input, capture a desktop or connect a remote session.

Original snippets remain historical negative controls. `scale_server_selected.rs` deliberately retains the original persistent Scale receiver for backward-compatibility tests; the current server receiver is freshly extracted into `scale_balanced_selected.rs`. The final retained-sender queue test proves the release marker while senders remain alive and before queue-end cleanup.

The frozen R12 fixture differs only by Cargo.lock v4→v3, a mock LazyLock→OnceLock adapter compatible with Rust1.75, and the existing two macOS-only mock helper/monitoring guards. `modifier_swap.test-tail.rs` is the exact frozen test-only suffix used when recomposing the production swap methods. No production behavior is rewritten by these portability adapters. `PORTABLE-ADAPTERS.json` records the exact edits.

Passing this component gate is source regression evidence. Installer builds, signatures and live peer/OS input acceptance remain separate requirements.
