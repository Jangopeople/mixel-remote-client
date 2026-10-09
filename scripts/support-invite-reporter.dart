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
    this.delay,
    this.requestTimeout = const Duration(seconds: 7),
  });

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
  final Future<void> Function(Duration)? delay;
  final Duration requestTimeout;
  bool _stopped = false;
  bool _running = false;
  final _pendingCancellations = <void Function()>{};

  void stop() {
    _stopped = true;
    for (final cancel in _pendingCancellations.toList()) {
      cancel();
    }
  }

  Future<T> _untilStopped<T>(Future<T> future, {Duration? timeout}) {
    final result = Completer<T>();
    void cancel() {
      if (!result.isCompleted)
        result.completeError(const _SupportInviteStopped());
    }

    final timer = timeout == null
        ? null
        : Timer(timeout, () {
            if (!result.isCompleted) {
              result.completeError(
                TimeoutException('Support request timed out', timeout),
              );
            }
          });
    // Handle late native results and errors even after cancellation or timeout.
    future.then(
      (value) {
        if (!result.isCompleted) result.complete(value);
      },
      onError: (Object error, StackTrace stack) {
        if (!result.isCompleted) result.completeError(error, stack);
      },
    );
    _pendingCancellations.add(cancel);
    if (_stopped) cancel();
    return result.future.whenComplete(() {
      timer?.cancel();
      _pendingCancellations.remove(cancel);
    });
  }

  Future<T> _call<T>(Future<T> Function() callback) {
    if (_stopped) return Future<T>.error(const _SupportInviteStopped());
    // Native startup/IPC callbacks can hang as well as HTTP. Bound every read
    // and let a superseding handoff finish even while a native result is late.
    return _untilStopped(Future<T>.sync(callback), timeout: requestTimeout);
  }

  Future<void> _waitBeforeRetry(Duration duration) async {
    final customDelay = delay;
    if (customDelay != null) {
      await _untilStopped(Future<void>.sync(() => customDelay(duration)));
      return;
    }
    final elapsed = Completer<void>();
    final timer = Timer(duration, elapsed.complete);
    try {
      await _untilStopped(elapsed.future);
    } finally {
      timer.cancel();
    }
  }

  static bool validInvite(String token, String apiKey) =>
      RegExp(
            r'^inv_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
            caseSensitive: false,
          ).stringMatch(token) ==
          token &&
      apiKey.length >= 20 &&
      apiKey.length <= 4096 &&
      !RegExp(r'[^\x21-\x7e]').hasMatch(apiKey);

  Future<void> run(String token, String apiKey) async {
    if (_running || _stopped || !validInvite(token, apiKey)) return;
    _running = true;
    try {
      await _run(token, apiKey);
    } finally {
      _running = false;
    }
  }

  Future<void> _run(String token, String apiKey) async {
    String? previousId;
    var failures = 0;
    while (!_stopped) {
      var wait = const Duration(seconds: 2);
      try {
        final attended = await _call(armAttended);
        if (_stopped) return;
        final id = (await _call(readId)).trim();
        if (_stopped) return;
        final validId = RegExp(r'^[a-zA-Z0-9-]{6,32}$').hasMatch(id);
        final stableId = validId && previousId == id;
        previousId = validId ? id : null;
        if (attended && stableId && await _call(isOnline)) {
          if (_stopped) return;
          final random = Random.secure();
          final nonce = List<int>.generate(
            16,
            (_) => random.nextInt(256),
          ).map((byte) => byte.toRadixString(16).padLeft(2, '0')).join();
          final status = await _call(() => report(token, apiKey, id, nonce));
          // An expired/revoked/mismatched invite cannot be recovered by retrying.
          if (status == 401 ||
              status == 403 ||
              status == 404 ||
              status == 409 ||
              status == 410) {
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
      } on _SupportInviteStopped {
        return;
      } catch (_) {
        // Retry startup, relay and network races without logging bearer data.
        failures++;
        wait = Duration(seconds: failures >= 4 ? 30 : 2 << failures);
      }
      if (!_stopped) {
        try {
          await _waitBeforeRetry(wait);
        } on _SupportInviteStopped {
          return;
        }
      }
    }
  }
}

class _SupportInviteStopped {
  const _SupportInviteStopped();
}
