import 'dart:async';
import 'dart:math';

class MixelSupportCompatibilityNotice {
  MixelSupportCompatibilityNotice({DateTime Function()? now})
    : _now = now ?? DateTime.now;
  final DateTime Function() _now;
  DateTime? _unavailableSince;
  bool _shown = false;
  void showIfRequired(String state, bool Function() show) {
    if (state != 'service-update-required' && state != 'guard-unavailable') {
      _unavailableSince = null;
      return;
    }
    _unavailableSince ??= _now();
    if (_now().difference(_unavailableSince!) < const Duration(seconds: 30)) {
      return;
    }
    if (!_shown) _shown = show();
  }
}

// This controller is shared by the app and standalone regression tests.
// It only announces the customer's own device; it never starts a connection.
class MixelSupportInviteReporter {
  MixelSupportInviteReporter({
    required this.readId,
    required this.isOnline,
    required this.armAttended,
    required this.report,
    Future<void> Function(Duration)? delay,
    this.requestTimeout = const Duration(seconds: 7),
  }) : delay = delay ?? Future<void>.delayed;

  final Future<String> Function() readId;
  final Future<bool> Function() isOnline;
  final Future<bool> Function() armAttended;
  final Future<int> Function(
    String token,
    String apiKey,
    String id,
    String nonce,
  )
  report;
  final Future<void> Function(Duration) delay;
  final Duration requestTimeout;
  bool _stopped = false;

  void stop() => _stopped = true;

  static bool validInvite(String token, String apiKey) =>
      RegExp(
        r'^inv_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        caseSensitive: false,
      ).hasMatch(token) &&
      apiKey.length >= 20 &&
      apiKey.length <= 4096;

  Future<void> run(String token, String apiKey) async {
    if (!validInvite(token, apiKey)) return;
    String? previousId;
    var failures = 0;
    while (!_stopped) {
      var wait = const Duration(seconds: 2);
      try {
        final attended = await armAttended();
        if (_stopped) return;
        final id = (await readId()).trim();
        if (_stopped) return;
        final validId = RegExp(r'^[a-zA-Z0-9-]{6,32}$').hasMatch(id);
        final stableId = validId && previousId == id;
        previousId = validId ? id : null;
        if (attended && stableId && await isOnline()) {
          if (_stopped) return;
          final random = Random.secure();
          final nonce = List<int>.generate(
            16,
            (_) => random.nextInt(256),
          ).map((byte) => byte.toRadixString(16).padLeft(2, '0')).join();
          final status = await report(
            token,
            apiKey,
            id,
            nonce,
          ).timeout(requestTimeout);
          // An expired/revoked/mismatched invite cannot be recovered by retrying.
          if (status == 401 ||
              status == 403 ||
              status == 404 ||
              status == 409) {
            return;
          }
          if (status >= 200 && status < 300) {
            failures = 0;
            wait = const Duration(seconds: 10);
          } else {
            failures++;
            wait = Duration(seconds: failures >= 4 ? 30 : 2 << failures);
          }
        }
      } catch (_) {
        // Retry startup, relay and network races without logging bearer data.
        failures++;
        wait = Duration(seconds: failures >= 4 ? 30 : 2 << failures);
      }
      if (!_stopped) await delay(wait);
    }
  }
}
