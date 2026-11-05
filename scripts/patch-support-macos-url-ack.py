#!/usr/bin/env python3
"""Patch pinned Mac support-only forwarding; never execute a native URI."""
import os
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("RDREPO", SCRIPTS.parent / "rustdesk"))


def replace(text, before, after, name):
    if after in text:
        return text
    if text.count(before) != 1:
        raise SystemExit("Pinned Mac support source drift: " + name)
    return text.replace(before, after, 1)


def main():
    paths = {name: ROOT / name for name in (
        "src/server.rs", "src/ipc.rs", "src/flutter.rs", "src/ui_interface.rs",
        "flutter/lib/common.dart", "flutter/lib/models/native_model.dart",
        "flutter/lib/desktop/pages/desktop_home_page.dart",
    )}
    text = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
    text["src/server.rs"] = replace(text["src/server.rs"], "use crate::ipc::Data;\n", """use crate::ipc::Data;

#[cfg(all(target_os = "macos", feature = "flutter"))]
#[path = "mixel_support_url.rs"]
pub(crate) mod mixel_support_url;

#[cfg(all(target_os = "macos", feature = "flutter"))]
pub(crate) fn flush_mixel_support_url() {
    let mut queue = mixel_support_url::global_queue().lock().unwrap_or_else(|error| error.into_inner());
    queue.flush(std::time::Instant::now(), |url| {
        let data = HashMap::from([("name", "on_url_scheme_received"), ("url", url)]);
        let Ok(event) = serde_json::to_string(&data) else { return Some(false); };
        crate::flutter::push_global_event(crate::flutter::APP_TYPE_MAIN, event)
    });
}

#[cfg(all(target_os = "macos", feature = "flutter"))]
pub(crate) fn set_mixel_support_url_ready(value: &str) {
    {
        let mut queue = mixel_support_url::global_queue().lock().unwrap_or_else(|error| error.into_inner());
        queue.set_ready(value);
    }
    flush_mixel_support_url();
}

#[cfg(all(target_os = "macos", feature = "flutter"))]
async fn send_mixel_support_url_ack(conn: &mut crate::ipc::Connection, accepted: bool) {
    let value = if accepted { mixel_support_url::ACK_ACCEPTED } else { mixel_support_url::ACK_REJECTED };
    let ack = Data::Config((mixel_support_url::ACK_NAME.to_owned(), Some(value.to_owned())));
    match timeout(250, conn.send(&ack)).await {
        Ok(Ok(())) => log::info!("Mixel support URL handoff: ack-sent"),
        _ => log::warn!("Mixel support URL handoff: ack-send-failed"),
    }
}
""", "receiver module")
    text["src/server.rs"] = replace(text["src/server.rs"], """pub async fn start_ipc_url_server() {
    log::debug!("Start an ipc server for listening to url schemes");
""", """pub async fn start_ipc_url_server() {
    log::debug!("Start an ipc server for listening to url schemes");
    #[cfg(feature = "flutter")]
    let ack_workers = Arc::new(tokio::sync::Semaphore::new(mixel_support_url::MAX_ACK_WORKERS));
""", "bounded receiver workers")
    text["src/server.rs"] = replace(text["src/server.rs"], """                        Data::UrlLink(url) => {
                            let mut m = HashMap::new();
""", """                        Data::UrlLink(url) => {
                            if mixel_support_url::is_valid_support_url(&url) {
                                // Own consent before queuing, not after an asynchronous Dart callback.
                                if !hbb_common::password_security::hold_support_invite_attended_lease() {
                                    log::warn!("Mixel support URL handoff: guard-unavailable");
                                    send_mixel_support_url_ack(&mut conn, false).await;
                                    continue;
                                }
                                let Ok(permit) = ack_workers.clone().try_acquire_owned() else {
                                    log::warn!("Mixel support URL handoff: busy");
                                    send_mixel_support_url_ack(&mut conn, false).await;
                                    continue;
                                };
                                let offered = mixel_support_url::global_queue().lock()
                                    .unwrap_or_else(|error| error.into_inner())
                                    .offer(url, std::time::Instant::now());
                                let Some((id, receiver)) = offered else {
                                    send_mixel_support_url_ack(&mut conn, false).await;
                                    continue;
                                };
                                flush_mixel_support_url();
                                tokio::spawn(async move {
                                    let _permit = permit;
                                    let deadline = std::time::Instant::now() + mixel_support_url::PENDING_TTL;
                                    let accepted = loop {
                                        match receiver.try_recv() {
                                            Ok(result) => break result == mixel_support_url::Delivery::Accepted,
                                            Err(std::sync::mpsc::TryRecvError::Disconnected) => break false,
                                            Err(std::sync::mpsc::TryRecvError::Empty) => {}
                                        }
                                        if std::time::Instant::now() >= deadline {
                                            mixel_support_url::global_queue().lock()
                                                .unwrap_or_else(|error| error.into_inner()).cancel(id);
                                            break false;
                                        }
                                        // Retry a rejected sink without unbounded tasks or bearer storage.
                                        flush_mixel_support_url();
                                        tokio::time::sleep(Duration::from_millis(25)).await;
                                    };
                                    send_mixel_support_url_ack(&mut conn, accepted).await;
                                });
                                continue;
                            }
                            let mut m = HashMap::new();
""", "support-only receiver")
    text["src/ipc.rs"] = replace(text["src/ipc.rs"], """pub async fn send_url_scheme(url: String) -> ResultType<()> {
    connect(1_000, "_url")
""", """pub async fn send_url_scheme(url: String) -> ResultType<()> {
    #[cfg(all(target_os = "macos", feature = "flutter"))]
    if crate::server::mixel_support_url::is_valid_support_url(&url) {
        return timeout(4_000, async {
            let Ok(mut conn) = connect(1_000, "_url").await else {
                bail!("Mixel support URL handoff connection unavailable");
            };
            if conn.send(&Data::UrlLink(url)).await.is_err() {
                bail!("Mixel support URL handoff send failed");
            }
            let Ok(ack) = conn.next_timeout(2_000).await else {
                bail!("Mixel support URL handoff reply unavailable");
            };
            match ack {
                Some(Data::Config((name, value)))
                    if crate::server::mixel_support_url::accepted_ack(&name, value.as_deref()) => Ok(()),
                _ => bail!("Mixel support URL handoff was not acknowledged"),
            }
        }).await?;
    }
    connect(1_000, "_url")
""", "support-only bounded sender ACK")
    text["src/flutter.rs"] = replace(text["src/flutter.rs"], """pub fn start_global_event_stream(s: StreamSink<String>, app_type: String) -> ResultType<()> {
    let app_type_values = app_type.split(",").collect::<Vec<&str>>();
""", """pub fn start_global_event_stream(s: StreamSink<String>, app_type: String) -> ResultType<()> {
    #[cfg(target_os = "macos")]
    if let Some(generation) = crate::server::mixel_support_url::stream_generation(&app_type) {
        {
            // Match queue -> event-stream lock ordering used by delivery. A
            // replacement primary isolate cannot inherit stale callback state.
            let mut queue = crate::server::mixel_support_url::global_queue().lock()
                .unwrap_or_else(|error| error.into_inner());
            queue.begin_stream(generation);
            GLOBAL_EVENT_STREAM.write().unwrap().insert(APP_TYPE_MAIN.to_owned(), s);
        }
        crate::server::flush_mixel_support_url();
        return Ok(());
    }
    let app_type_values = app_type.split(",").collect::<Vec<&str>>();
""", "primary stream generation")
    text["src/flutter.rs"] = replace(text["src/flutter.rs"], """    Ok(())
}

pub fn stop_global_event_stream(app_type: String) {
    let _ = GLOBAL_EVENT_STREAM.write().unwrap().remove(&app_type);
}
""", """    drop(lock);
    #[cfg(target_os = "macos")]
    if app_type_values[0] == APP_TYPE_MAIN { crate::server::flush_mixel_support_url(); }
    Ok(())
}

pub fn stop_global_event_stream(app_type: String) {
    #[cfg(target_os = "macos")]
    if let Some(generation) = crate::server::mixel_support_url::stream_generation(&app_type) {
        let mut queue = crate::server::mixel_support_url::global_queue().lock()
            .unwrap_or_else(|error| error.into_inner());
        // A late old-isolate stop cannot remove or pause a replacement stream.
        if queue.stop_if_generation(generation) {
            GLOBAL_EVENT_STREAM.write().unwrap().remove(APP_TYPE_MAIN);
        }
        return;
    }
    #[cfg(target_os = "macos")]
    if app_type == APP_TYPE_MAIN {
        let mut queue = crate::server::mixel_support_url::global_queue().lock()
            .unwrap_or_else(|error| error.into_inner());
        // Existing unqualified stops have no identity for a qualified stream.
        // Keep legacy unqualified MAIN behavior when no new stream is active.
        if !queue.has_stream() {
            GLOBAL_EVENT_STREAM.write().unwrap().remove(APP_TYPE_MAIN);
            queue.stop();
        }
        return;
    }
    let _ = GLOBAL_EVENT_STREAM.write().unwrap().remove(&app_type);
}
""", "stream queue replay and cleanup")
    text["src/ui_interface.rs"] = replace(text["src/ui_interface.rs"], '    if &key == "stop-service" {\n', """
    #[cfg(all(target_os = "macos", feature = "flutter"))]
    if key == crate::server::mixel_support_url::READY_OPTION {
        // This local callback readiness command never enters IPC or saved OPTIONS.
        crate::server::set_mixel_support_url_ready(&value);
        return;
    }
    if &key == "stop-service" {
""", "runtime-only callback readiness")
    readiness = """// Mixel Mac support forwarding readiness. Local runtime state, never saved preferences.
bool _mixelSupportUrlCallbackReady = false;
bool _mixelSupportUrlStreamReady = false;
bool _mixelSupportUrlUiReady = false;
int _mixelSupportUrlReadyEpoch = 0;
String _mixelSupportUrlGeneration = '';
String get mixelSupportUrlGeneration => _mixelSupportUrlGeneration;
void Function(bool)? _mixelSupportUrlPendingMaintenance;
bool get mixelSupportUrlCanReceive => _mixelSupportUrlCallbackReady &&
  _mixelSupportUrlStreamReady && _mixelSupportUrlUiReady;
bool get mixelSupportUrlHasLiveUi => _mixelSupportUrlStreamReady && _mixelSupportUrlUiReady;
void setMixelSupportUrlPendingMaintenance(void Function(bool) maintenance) {
  if (!isMacOS || desktopType != DesktopType.main) return;
  _mixelSupportUrlPendingMaintenance = maintenance;
}
bool isMixelSupportUrlEvent(Map<String, dynamic> event) {
  final url = event['url'];
  if (event['name'] != 'on_url_scheme_received' || url is! String ||
      utf8.encode(url).length > 16384 || url.runes.any((value) => value < 32 || (value >= 127 && value <= 159)) ||
      RegExp(r'%(?![0-9a-fA-F]{2})').hasMatch(url)) return false;
  try {
    final uri = Uri.tryParse(url);
    return uri != null && urlLinkToCmdArgs(uri)?.first == '--support-invite';
  } catch (_) { return false; }
}
String mixelSupportUrlMainStreamType(String original) {
  if (!isMacOS || desktopType != DesktopType.main) return original;
  final random = Random.secure();
  _mixelSupportUrlGeneration = List<int>.generate(16, (_) => random.nextInt(256))
    .map((value) => value.toRadixString(16).padLeft(2, '0')).join();
  _mixelSupportUrlReadyEpoch = 0;
  return '$original,mixel-support-url:$_mixelSupportUrlGeneration';
}
void _publishMixelSupportUrlReady() {
  if (!isMacOS || desktopType != DesktopType.main) return;
  _mixelSupportUrlPendingMaintenance?.call(mixelSupportUrlHasLiveUi);
  if (_mixelSupportUrlGeneration.isEmpty) return;
  final ready = _mixelSupportUrlCallbackReady && _mixelSupportUrlStreamReady && _mixelSupportUrlUiReady;
  final epoch = ++_mixelSupportUrlReadyEpoch;
  unawaited(bind.mainSetOption(key: 'mixel-support-url-callback-ready-v1',
    value: '${ready ? 'Y' : 'N'}:$_mixelSupportUrlGeneration:$epoch').catchError((Object _) {
      debugPrint('Mixel support URL readiness update failed.');
    }));
}
void setMixelSupportUrlCallbackReady(bool ready) {
  if (!isMacOS || desktopType != DesktopType.main) return;
  _mixelSupportUrlCallbackReady = ready;
  _publishMixelSupportUrlReady();
}
void setMixelSupportUrlStreamReady(bool ready, {String? generation}) {
  if (!isMacOS || desktopType != DesktopType.main) return;
  if (generation != null && generation != _mixelSupportUrlGeneration) return;
  _mixelSupportUrlStreamReady = ready;
  _publishMixelSupportUrlReady();
}
void setMixelSupportUrlUiReady(bool ready) {
  if (!isMacOS || desktopType != DesktopType.main) return;
  _mixelSupportUrlUiReady = ready;
  _publishMixelSupportUrlReady();
}

"""
    marker = "// Mixel support invite handoff: presence only; customer accepts every connection.\n"
    text["flutter/lib/common.dart"] = replace(text["flutter/lib/common.dart"], marker, readiness + marker, "Dart readiness gates")
    native = "flutter/lib/models/native_model.dart"
    text[native] = replace(text[native], "  StreamEventHandler? _eventCallback;\n", """  StreamEventHandler? _eventCallback;
  Map<String, dynamic>? _mixelSupportUrlPendingEvent;
  Stopwatch? _mixelSupportUrlPendingAge;
  String? _mixelSupportUrlPendingGeneration;
""", "bounded Dart support intent state")
    text[native] = replace(text[native], "  void _startListenEvent(RustdeskImpl rustdeskImpl) {\n", """  void _clearMixelSupportUrlPending() {
    _mixelSupportUrlPendingAge?.stop();
    _mixelSupportUrlPendingAge = null;
    _mixelSupportUrlPendingEvent = null;
    _mixelSupportUrlPendingGeneration = null;
  }

  void _maintainMixelSupportUrlPending(bool liveUi) {
    final age = _mixelSupportUrlPendingAge;
    if (!liveUi || (age != null && age.elapsed >= const Duration(milliseconds: 1500)) ||
        (_mixelSupportUrlPendingGeneration != null && _mixelSupportUrlPendingGeneration != mixelSupportUrlGeneration)) {
      _clearMixelSupportUrlPending();
      return;
    }
    final event = _mixelSupportUrlPendingEvent;
    final callback = _eventCallback;
    if (event == null || callback == null || !mixelSupportUrlCanReceive) return;
    _clearMixelSupportUrlPending();
    () async {
      try { await callback(event); }
      catch (_) { debugPrint('Mixel support URL dispatch failed.'); }
    }();
  }

  bool _deferMixelSupportUrlEvent(Map<String, dynamic> event) {
    if (!isMixelSupportUrlEvent(event)) return false;
    if (_eventCallback != null && mixelSupportUrlCanReceive) {
      _clearMixelSupportUrlPending();
      return false;
    }
    if (!mixelSupportUrlHasLiveUi) {
      _clearMixelSupportUrlPending();
      return true;
    }
    // Exactly one validated support event survives a callback-clear race.
    // The existing MAIN UI poll reaps it; expiry blocks dispatch strictly at 1.5s.
    _mixelSupportUrlPendingEvent = {'name': 'on_url_scheme_received', 'url': event['url']};
    _mixelSupportUrlPendingAge = Stopwatch()..start();
    _mixelSupportUrlPendingGeneration = mixelSupportUrlGeneration;
    return true;
  }

  void _startListenEvent(RustdeskImpl rustdeskImpl) {
""", "Dart support-only callback gap retention")
    text[native] = replace(text[native], """          if (!await tryHandle(event)) {
            if (_eventCallback != null) {
""", """          if (isMacOS && isMain && isMixelSupportUrlEvent(event) &&
              supportGeneration != mixelSupportUrlGeneration) return;
          if (!await tryHandle(event)) {
            // Handler lookup is asynchronous: replacement can occur during it.
            if (isMacOS && isMain && isMixelSupportUrlEvent(event) &&
                supportGeneration != mixelSupportUrlGeneration) return;
            if (isMacOS && isMain && _deferMixelSupportUrlEvent(event)) return;
            if (_eventCallback != null) {
""", "retain support event after actual callback gap")
    text[native] = replace(text[native], """    var sink = rustdeskImpl.startGlobalEventStream(appType: appType);
    sink.listen((message) {
""", """    if (isMacOS && isMain) {
      _clearMixelSupportUrlPending();
      setMixelSupportUrlPendingMaintenance(_maintainMixelSupportUrlPending);
      setMixelSupportUrlStreamReady(false);
    }
    var sink = rustdeskImpl.startGlobalEventStream(appType:
      isMacOS && isMain ? mixelSupportUrlMainStreamType(appType) : appType);
    final supportGeneration = isMacOS && isMain ? mixelSupportUrlGeneration : null;
    void onEvent(String message) {
""", "MAIN stream startup pause")
    text[native] = replace(text[native], """          debugPrint('json.decode fail(): $e');
""", """          if (isMacOS && isMain && message.contains('on_url_scheme_received') &&
              message.toLowerCase().contains('mixel-remote://support')) {
            debugPrint('Mixel support URL dispatch failed.');
          } else {
            debugPrint('json.decode fail(): $e');
          }
""", "support-only redacted dispatch failure")
    text[native] = replace(text[native], """    });
  }

  void setEventCallback(StreamEventHandler fun) async {
    _eventCallback = fun;
  }
""", """    }
    if (isMacOS && isMain) {
      sink.listen(onEvent, onDone: () {
        setMixelSupportUrlStreamReady(false, generation: supportGeneration);
      }, onError: (Object _, StackTrace __) {
        setMixelSupportUrlStreamReady(false, generation: supportGeneration);
      });
      setMixelSupportUrlStreamReady(true, generation: supportGeneration);
    } else {
      // Preserve ordinary platforms' original zone error handling.
      sink.listen(onEvent);
    }
  }

  void setEventCallback(StreamEventHandler? fun) async {
    _eventCallback = fun;
    if (isMacOS && isMain) setMixelSupportUrlCallbackReady(fun != null);
  }
""", "callback attachment and closed stream gates")
    home = "flutter/lib/desktop/pages/desktop_home_page.dart"
    text[home] = replace(text[home], "    _uniLinksSubscription = listenUniLinks();\n", """    _uniLinksSubscription = listenUniLinks();
    setMixelSupportUrlUiReady(true);
""", "MAIN UI attachment")
    text[home] = replace(text[home], """    _updateTimer = periodic_immediate(const Duration(seconds: 1), () async {
""", """    _updateTimer = periodic_immediate(const Duration(seconds: 1), () async {
      // Reconcile asynchronous stream registration using the existing UI poll.
      setMixelSupportUrlUiReady(true);
""", "bounded existing UI readiness refresh")
    text[home] = replace(text[home], """  void dispose() {
    _uniLinksSubscription?.cancel();
""", """  void dispose() {
    setMixelSupportUrlUiReady(false);
    _uniLinksSubscription?.cancel();
""", "MAIN UI disposal")
    # Resolve and validate every pinned context before the first write.
    for name, path in paths.items():
        path.write_text(text[name], encoding="utf-8", newline="\n")
    (ROOT / "src/mixel_support_url.rs").write_text(
        (SCRIPTS / "support-macos-url-queue.rs").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    print("Patched bounded Mac support-only forwarding ACK, callback readiness and queue cleanup")


if __name__ == "__main__":
    main()
