#!/usr/bin/env python3
"""Forward only validated Mac attended support intents from Tao's OS URL event."""
import argparse
import hashlib
import os
from pathlib import Path
import stat

INPUT_SHA256 = "8e7e5b375d4b3e4c4588f4dd6bc7544a7fa2eb0b9cf16c684a96169f0e7aef3d"
PRISTINE_SHA256 = "0f9410ed53f142e4dca83756bd29310f8cb9b019ab74e0f453df83592d597295"
MARKER = "MIXEL_MACOS_TRAY_SUPPORT_URL_V1"
HELPER = r"""// MIXEL_MACOS_TRAY_SUPPORT_URL_V1: runtime-only, support-only OS URL bridge.
#[cfg(all(target_os = "macos", feature = "flutter"))]
const MIXEL_TRAY_URL_EVENT_LIMIT: usize = 64;
#[cfg(all(target_os = "macos", feature = "flutter"))]
const MIXEL_TRAY_URL_WORKER_LIMIT: usize = 8;
#[cfg(all(target_os = "macos", feature = "flutter"))]
static MIXEL_TRAY_URL_WORKERS: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);

#[cfg(all(target_os = "macos", feature = "flutter"))]
struct MixelTrayUrlWorker;
#[cfg(all(target_os = "macos", feature = "flutter"))]
impl Drop for MixelTrayUrlWorker {
    fn drop(&mut self) {
        MIXEL_TRAY_URL_WORKERS.fetch_sub(1, std::sync::atomic::Ordering::AcqRel);
    }
}

#[cfg(all(target_os = "macos", feature = "flutter"))]
fn route_mixel_tray_support_urls(urls: &[url::Url]) {
    // Match the existing native queue's latest-valid-intent policy. Bound work
    // on Tao's main thread before allocating or invoking an IPC sender.
    let selected = urls.iter().rev().take(MIXEL_TRAY_URL_EVENT_LIMIT)
        .find(|url| crate::server::mixel_support_url::is_valid_support_url(url.as_str()));
    let Some(selected) = selected else { return; };
    let mut workers = MIXEL_TRAY_URL_WORKERS.load(std::sync::atomic::Ordering::Acquire);
    loop {
        if workers >= MIXEL_TRAY_URL_WORKER_LIMIT {
            log::info!("Mixel support URL tray route: busy");
            return;
        }
        match MIXEL_TRAY_URL_WORKERS.compare_exchange_weak(
            workers, workers + 1,
            std::sync::atomic::Ordering::AcqRel,
            std::sync::atomic::Ordering::Acquire,
        ) {
            Ok(_) => break,
            Err(actual) => workers = actual,
        }
    }
    let permit = MixelTrayUrlWorker;
    let intent = selected.as_str().to_owned();
    // handle_url_scheme retains its existing validated ACK and GUI fallback.
    // Attended requests wait for customer Accept; existing sessions are preserved.
    // Never block Tao or emit the URI, bearer, device or customer metadata.
    if std::thread::Builder::new().name("mixel-tray-url".to_owned())
        .spawn(move || {
            let _permit = permit;
            log::info!("Mixel support URL tray route: dispatch");
            crate::handle_url_scheme(intent);
        }).is_err() {
        // Failed spawn drops the closure/permit, releasing the worker slot.
        log::info!("Mixel support URL tray route: worker-unavailable");
    }
}

"""
ANCHOR = "fn make_tray() -> hbb_common::ResultType<()> {"
EVENT_ANCHOR = "        if let tao::event::Event::NewEvents(tao::event::StartCause::Init) = event {"
EVENT = r"""        #[cfg(all(target_os = "macos", feature = "flutter"))]
        if let tao::event::Event::Opened { urls } = &event {
            route_mixel_tray_support_urls(urls);
        }

"""

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def transform(text, reverse=False):
    changes = [(ANCHOR, HELPER + ANCHOR), (EVENT_ANCHOR, EVENT + EVENT_ANCHOR)]
    if reverse:
        changes.reverse()
    for old, new in changes:
        before, after = (new, old) if reverse else (old, new)
        if text.count(before) != 1:
            raise SystemExit("Pinned Mac tray URL anchor changed; refusing writes")
        text = text.replace(before, after, 1)
    return text

def apply(root, restore=False):
    path = root / "src/tray.rs"
    if path.is_symlink() or not path.is_file():
        raise SystemExit("Mac tray source must be a regular file")
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or path.parent.is_symlink():
        raise SystemExit("Mac tray source must be single-link regular source")
    raw = path.read_bytes()
    normalized = raw.replace(b"\r\n", b"\n")
    ending = b"\r\n" if b"\r\n" in raw else b"\n"
    if raw != normalized.replace(b"\n", ending):
        raise SystemExit("Mixed Mac tray source line endings")
    text = normalized.decode("utf-8")
    baseline = transform(text, True) if MARKER in text else text
    # Exact fresh source is only an early restore no-op; apply still requires
    # the one final branded native-warning profile above.
    if restore and MARKER not in text and digest(baseline.encode()) == PRISTINE_SHA256:
        return
    if digest(baseline.encode()) != INPUT_SHA256:
        raise SystemExit("Pinned complete Mac tray source differs; refusing writes")
    fixed = transform(baseline)
    if MARKER in text and fixed != text:
        raise SystemExit("Mac tray URL overlay changed; refusing writes")
    result = baseline if restore else fixed
    if result != text:
        path.write_bytes(result.encode().replace(b"\n", ending))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore",action="store_true")
    args=parser.parse_args()
    apply(Path(os.environ.get("RDREPO",Path(__file__).resolve().parent.parent/"rustdesk")),args.restore)
    print("PASS: Mac Tao URL route " + ("restored" if args.restore else "forwards bounded validated support intents through existing ACK; ordinary tray preserved"))

if __name__ == "__main__":
    main()
