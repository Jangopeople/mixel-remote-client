// Appended to the exact original/generated WakelockManager by the Python runner.
// Only platform/UI boundaries are controlled here; the manager is not rewritten.
class UniqueKey {}

bool isDesktop = true;
bool keepAwake = true;
const kOptionKeepAwakeDuringOutgoingSessions =
    'keep-awake-during-outgoing-sessions';
bool mainGetLocalBoolOptionSync(String option) {
  if (option != kOptionKeepAwakeDuringOutgoingSessions) {
    throw StateError('Unexpected preference lookup');
  }
  return keepAwake;
}

final diagnostics = <String>[];
void debugPrint(String text) => diagnostics.add(text);

bool countRegistrations = false;
int registeredCallbacks = 0;
R Function(T) countUnaryCallback<R, T>(
    Zone self, ZoneDelegate parent, Zone zone, R Function(T) callback) {
  if (countRegistrations) registeredCallbacks++;
  return parent.registerUnaryCallback(zone, callback);
}

class WakelockPlus {
  static final calls = <bool>[];
  static final cookies = <int>{};
  static final gates = <bool, Completer<void>>{};
  static int? cookie;
  static int nextCookie = 0;
  static int inFlight = 0;
  static int maxInFlight = 0;
  static int failEnable = 0;
  static int failDisable = 0;
  static bool synchronousFailure = false;

  static Future<void> enable() => _invoke(true);
  static Future<void> disable() => _invoke(false);

  static Future<void> _invoke(bool enable) {
    calls.add(enable);
    if (synchronousFailure) throw StateError('Controlled synchronous failure');
    return _finish(enable);
  }

  static Future<void> _finish(bool enable) async {
    inFlight++;
    if (inFlight > maxInFlight) maxInFlight = inFlight;
    try {
      final gate = gates.remove(enable);
      if (gate != null) await gate.future;
      if (enable && failEnable > 0) {
        failEnable--;
        throw StateError('org.freedesktop.DBus.Error.ServiceUnknown');
      }
      if (!enable && failDisable > 0) {
        failDisable--;
        throw StateError('Controlled UnInhibit failure');
      }
      // The pinned Linux plugin assigns its cookie only after successful
      // Inhibit, and clears it only after successful UnInhibit. A second
      // overlapping Inhibit would orphan the previous cookie.
      if (enable) {
        cookie = ++nextCookie;
        cookies.add(cookie!);
      } else if (cookie != null) {
        cookies.remove(cookie);
        cookie = null;
      }
    } finally {
      inFlight--;
    }
  }
}

void check(bool condition, String reason) {
  if (!condition) throw StateError(reason);
}

Future<void> settle() async {
  for (var i = 0; i < 8; i++) {
    await Future<void>.delayed(Duration.zero);
  }
}

Future<void> scenario(String name, List<Object> zoneErrors) async {
  final first = UniqueKey();
  final second = UniqueKey();
  switch (name) {
    case 'provider-error':
      WakelockPlus.failEnable = 1;
      WakelockManager.enable(first);
      await settle();
      check(zoneErrors.isEmpty,
          'provider failure must not escape the session zone');
      check(!WakelockManager._enabled && WakelockPlus.cookies.isEmpty,
          'provider failure must not claim an acquired inhibit');
      check(diagnostics.length == 1,
          'provider failure needs a contained diagnostic');
      break;
    case 'provider-retry':
      WakelockPlus.failEnable = 1;
      WakelockManager.enable(first);
      await settle();
      WakelockManager.enable(first);
      await settle();
      check(WakelockPlus.calls.length == 2 && WakelockPlus.cookies.length == 1,
          'failed acquisition must remain retryable');
      check(WakelockManager._enabled,
          'successful retry must record actual state');
      WakelockManager.disable(first);
      await settle();
      break;
    case 'acquire-release-race':
      final acquisition = Completer<void>();
      WakelockPlus.gates[true] = acquisition;
      WakelockManager.enable(first);
      await settle();
      WakelockManager.disable(first);
      await settle();
      check(WakelockPlus.calls.length == 1 && WakelockPlus.maxInFlight == 1,
          'release must wait for acquisition');
      acquisition.complete();
      await settle();
      check(
          WakelockPlus.calls.length == 2 &&
              WakelockPlus.calls.last == false &&
              WakelockPlus.cookies.isEmpty &&
              !WakelockManager._enabled,
          'completed acquisition must be released after the final key closes');
      break;
    case 'release-error':
      WakelockManager.enable(first);
      await settle();
      WakelockPlus.failDisable = 1;
      WakelockManager.disable(first);
      await settle();
      check(WakelockManager._enabled && WakelockPlus.cookies.length == 1,
          'failed release must retain successful active state');
      WakelockManager.disable(first);
      await settle();
      check(WakelockPlus.cookies.isEmpty && !WakelockManager._enabled,
          'later release must retry the retained inhibit');
      break;
    case 'synchronous-error':
      WakelockPlus.synchronousFailure = true;
      var escaped = false;
      try {
        WakelockManager.enable(first);
      } catch (_) {
        escaped = true;
      }
      await settle();
      check(!escaped && zoneErrors.isEmpty,
          'synchronous platform failure must be contained');
      check(!WakelockManager._enabled,
          'synchronous failure must not mark active');
      WakelockPlus.synchronousFailure = false;
      WakelockManager.enable(first);
      await settle();
      check(WakelockPlus.cookies.length == 1,
          'queue must recover after synchronous failure');
      WakelockManager.disable(first);
      await settle();
      break;
    case 'desktop-reference-count':
      WakelockManager.enable(first);
      WakelockManager.enable(second);
      await settle();
      check(WakelockPlus.calls.length == 1, 'two keys must share one inhibit');
      WakelockManager.disable(first);
      await settle();
      check(WakelockPlus.calls.length == 1 && WakelockPlus.cookies.length == 1,
          'one remaining key must retain the inhibit');
      WakelockManager.disable(second);
      await settle();
      check(WakelockPlus.calls.length == 2 && WakelockPlus.cookies.isEmpty,
          'final key must release the inhibit');
      break;
    case 'duplicate-and-unknown-key':
      WakelockManager.enable(first);
      WakelockManager.enable(first);
      await settle();
      WakelockManager.disable(second);
      await settle();
      check(WakelockPlus.calls.length == 1 && WakelockPlus.cookies.length == 1,
          'duplicate and unknown keys must not change reference ownership');
      WakelockManager.disable(first);
      WakelockManager.disable(first);
      await settle();
      check(WakelockPlus.calls.length == 2 && WakelockPlus.cookies.isEmpty,
          'duplicate release must not release twice');
      break;
    case 'preference-and-server':
      keepAwake = false;
      WakelockManager.enable(first);
      await settle();
      check(WakelockPlus.calls.isEmpty,
          'disabled outgoing preference must be respected');
      WakelockManager.enable(second, isServer: true);
      await settle();
      check(WakelockPlus.cookies.length == 1,
          'server request must preserve its override');
      WakelockManager.disable(first);
      await settle();
      check(WakelockPlus.cookies.length == 1,
          'opted-out outgoing key must own no reference');
      WakelockManager.disable(second);
      await settle();
      check(WakelockPlus.cookies.isEmpty,
          'server reference must release normally');
      break;
    case 'mobile-last-request':
      isDesktop = false;
      WakelockManager.enable(first, isServer: true);
      WakelockManager.enable(second, isServer: true);
      await settle();
      check(WakelockPlus.cookies.length == 1,
          'mobile repeated enable must share active state');
      WakelockManager.disable(first);
      await settle();
      check(WakelockPlus.cookies.isEmpty,
          'mobile server behavior must preserve its latest on/off request');
      break;
    case 'pending-release-new-session':
      WakelockManager.enable(first);
      await settle();
      final release = Completer<void>();
      WakelockPlus.gates[false] = release;
      WakelockManager.disable(first);
      await settle();
      WakelockManager.enable(second);
      await settle();
      check(WakelockPlus.calls.length == 2 && WakelockPlus.maxInFlight == 1,
          'new acquisition must wait for an outstanding release');
      release.complete();
      await settle();
      check(
          WakelockPlus.calls.length == 3 &&
              WakelockPlus.cookies.length == 1 &&
              WakelockManager._enabled,
          'new session must own the next completed inhibit');
      WakelockManager.disable(second);
      await settle();
      check(WakelockPlus.cookies.isEmpty,
          'replacement session must release without leaks');
      break;
    case 'coalesced-before-start':
      WakelockManager.enable(first);
      WakelockManager.disable(first);
      await settle();
      check(WakelockPlus.calls.isEmpty && WakelockPlus.cookies.isEmpty,
          'already-closed request must not acquire an unnecessary inhibit');
      break;
    case 'coalesced-during-acquisition':
      final acquisition = Completer<void>();
      WakelockPlus.gates[true] = acquisition;
      WakelockManager.enable(first);
      await settle();
      WakelockManager.disable(first);
      WakelockManager.enable(second);
      acquisition.complete();
      await settle();
      check(WakelockPlus.calls.length == 1 && WakelockPlus.cookies.length == 1,
          'current remaining key must retain a just-completed acquisition');
      WakelockManager.disable(second);
      await settle();
      check(
          WakelockPlus.cookies.isEmpty, 'coalesced remaining key must release');
      break;
    case 'bounded-request-backlog':
      final acquisition = Completer<void>();
      WakelockPlus.gates[true] = acquisition;
      WakelockManager.enable(first);
      await settle();
      countRegistrations = true;
      for (var i = 0; i < 10000; i++) {
        switch (i % 4) {
          case 0:
          case 3:
            WakelockManager.enable(first);
            break;
          case 1:
            WakelockManager.enable(second);
            break;
          case 2:
            WakelockManager.disable(second);
            break;
        }
      }
      WakelockManager.disable(first);
      WakelockManager.disable(second);
      countRegistrations = false;
      stderr.writeln(
          'OBSERVED $registeredCallbacks queued callback registrations '
          'for 10002 requests behind one pending provider');
      check(registeredCallbacks <= 1,
          'pending provider must retain at most one follow-up callback');
      check(WakelockPlus.calls.length == 1 && WakelockPlus.maxInFlight == 1,
          'request backlog must not release before acquisition');
      acquisition.complete();
      await settle();
      check(WakelockPlus.calls.length == 2 && WakelockPlus.cookies.isEmpty,
          'request backlog must coalesce to the final closed state');
      WakelockManager.enable(second);
      await settle();
      WakelockManager.disable(second);
      await settle();
      check(WakelockPlus.calls.length == 4 && WakelockPlus.cookies.isEmpty,
          'bounded queue must remain usable after the request burst');
      break;
    default:
      throw StateError('Unknown scenario');
  }
  check(zoneErrors.isEmpty, 'no platform future may remain unhandled');
  check(WakelockPlus.inFlight == 0,
      'all observed platform operations must finish');
  check(
      WakelockPlus.maxInFlight <= 1, 'platform operations must be serialized');
}

Future<void> main(List<String> arguments) async {
  final done = Completer<void>();
  final zoneErrors = <Object>[];
  runZonedGuarded(() {
    () async {
      try {
        check(arguments.length == 1, 'one scenario required');
        await scenario(arguments.single, zoneErrors);
        stdout.writeln('PASS ${arguments.single}');
      } catch (error) {
        stderr.writeln('FAIL ${arguments.single}: $error');
        exitCode = 1;
      } finally {
        done.complete();
      }
    }();
  }, (Object error, StackTrace stack) {
    zoneErrors.add(error);
  },
      zoneSpecification:
          ZoneSpecification(registerUnaryCallback: countUnaryCallback));
  await done.future.timeout(const Duration(seconds: 5));
}
