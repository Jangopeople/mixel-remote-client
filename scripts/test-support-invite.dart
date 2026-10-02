import 'dart:async';

import 'support-invite-reporter.dart';

const token = 'inv_00000000-0000-0000-0000-000000000001';
const apiKey = 'public-test-key-000000000000';

void expect(bool condition, String message) {
  if (!condition) throw StateError(message);
}

Future<void> main() async {
  var passed = 0;
  Future<void> test(String name, Future<void> Function() body) async {
    await body();
    passed++;
    print('PASS: $name');
  }

  await test(
    'legacy service update notice displays once when UI is ready',
    () async {
      var now = DateTime(2026);
      final notice = MixelSupportCompatibilityNotice(now: () => now);
      var shown = 0;
      notice.showIfRequired('guard-unavailable', () {
        shown++;
        return true;
      });
      now = now.add(const Duration(seconds: 31));
      notice.showIfRequired('service-update-required', () => false);
      notice.showIfRequired('service-update-required', () {
        shown++;
        return true;
      });
      notice.showIfRequired('service-update-required', () {
        shown++;
        return true;
      });
      expect(
        shown == 1,
        'retry until visible then show compatibility error only once',
      );
    },
  );
  await test('malformed bearer never reaches ID or HTTP callbacks', () async {
    var callbacks = 0;
    final reporter = MixelSupportInviteReporter(
      readId: () async {
        callbacks++;
        return '123456';
      },
      isOnline: () async {
        callbacks++;
        return true;
      },
      armAttended: () async {
        callbacks++;
        return true;
      },
      report: (_, __, ___, ____) async {
        callbacks++;
        return 200;
      },
    );
    await reporter.run('inv_------------------------------------', apiKey);
    await reporter.run(token, 'short');
    expect(callbacks == 0, 'invalid credentials must not reach any callback');
  });

  await test(
    'startup generating ID and relay registration race retry before report',
    () async {
      final ids = ['', 'Generating ...', '123456', '123456', '123456'];
      var reads = 0;
      var onlineChecks = 0;
      var reports = 0;
      late MixelSupportInviteReporter reporter;
      reporter = MixelSupportInviteReporter(
        readId: () async => ids[reads++],
        isOnline: () async => ++onlineChecks >= 2,
        armAttended: () async => true,
        report: (receivedToken, receivedKey, id, nonce) async {
          expect(id == '123456', 'report only actual stable device ID');
          expect(reads == 5, 'wait for relay ready');
          expect(
            receivedToken == token && receivedKey == apiKey,
            'same invite',
          );
          reports++;
          reporter.stop();
          return 200;
        },
        delay: (_) async {},
      );
      await reporter.run(token, apiKey);
      expect(reports == 1, 'exactly one ready report');
    },
  );

  await test('changing startup ID is not bound until stable', () async {
    final ids = ['123456', '234567', '234567'];
    var reads = 0;
    late MixelSupportInviteReporter reporter;
    reporter = MixelSupportInviteReporter(
      readId: () async => ids[reads++],
      isOnline: () async => true,
      armAttended: () async => true,
      report: (_, __, id, nonce) async {
        expect(
          id == '234567' && reads == 3,
          'only stabilized ID can bind invite',
        );
        reporter.stop();
        return 200;
      },
      delay: (_) async {},
    );
    await reporter.run(token, apiKey);
  });

  await test(
    'unconfirmed installed-service guard blocks presence readiness',
    () async {
      var guards = 0;
      var reads = 0;
      late MixelSupportInviteReporter reporter;
      reporter = MixelSupportInviteReporter(
        readId: () async {
          reads++;
          return '123456';
        },
        isOnline: () async => true,
        armAttended: () async => ++guards >= 4,
        report: (_, __, ___, ____) async {
          expect(
            reads == 4 && guards == 4,
            'no report before service guard confirmed',
          );
          reporter.stop();
          return 200;
        },
        delay: (_) async {},
      );
      await reporter.run(token, apiKey);
    },
  );

  await test(
    'temporary HTTP failure retries and successful heartbeat continues',
    () async {
      var reports = 0;
      var activePosts = 0;
      var maximumActivePosts = 0;
      final delays = <int>[];
      late MixelSupportInviteReporter reporter;
      reporter = MixelSupportInviteReporter(
        readId: () async => '123456',
        isOnline: () async => true,
        armAttended: () async => true,
        report: (_, __, ___, ____) async {
          activePosts++;
          if (activePosts > maximumActivePosts)
            maximumActivePosts = activePosts;
          await Future<void>.delayed(Duration.zero);
          activePosts--;
          reports++;
          if (reports == 1)
            throw StateError('transient synthetic network failure');
          if (reports == 4) reporter.stop();
          return reports == 2 ? 503 : 200;
        },
        delay: (duration) async {
          delays.add(duration.inSeconds);
        },
      );
      await reporter.run(token, apiKey);
      expect(
        reports == 4,
        'continues after both exception and temporary server failure',
      );
      expect(
        delays.join(',') == '2,4,8,10',
        'bounded retry backoff then normal heartbeat',
      );
      expect(maximumActivePosts == 1, 'never overlap presence requests');
    },
  );

  await test('revoked or mismatched invite stops retries', () async {
    for (final status in [401, 403, 404, 409]) {
      var posts = 0;
      final reporter = MixelSupportInviteReporter(
        readId: () async => '123456',
        isOnline: () async => true,
        armAttended: () async => true,
        report: (_, __, ___, ____) async {
          posts++;
          return status;
        },
        delay: (_) async {},
      );
      await reporter.run(token, apiKey);
      expect(posts == 1, 'HTTP $status must stop invite report');
    }
  });

  await test(
    'superseded invite cancels before pending ID becomes available',
    () async {
      final idReady = Completer<String>();
      var posts = 0;
      final reporter = MixelSupportInviteReporter(
        readId: () => idReady.future,
        isOnline: () async => true,
        armAttended: () async => true,
        report: (_, __, ___, ____) async {
          posts++;
          return 200;
        },
        delay: (_) async {},
      );
      final run = reporter.run(token, apiKey);
      await Future<void>.delayed(Duration.zero);
      reporter.stop();
      idReady.complete('123456');
      await run;
      expect(posts == 0, 'old handoff must not report after replacement');
    },
  );
  await test(
    'late old invite response cannot poison replacement invite heartbeat',
    () async {
      final oldResponse = Completer<int>();
      final oldStarted = Completer<void>();
      final nonces = <String>[];
      var currentReports = 0;
      final old = MixelSupportInviteReporter(
        readId: () async => '123456',
        isOnline: () async => true,
        armAttended: () async => true,
        report: (_, __, ___, nonce) {
          nonces.add(nonce);
          oldStarted.complete();
          return oldResponse.future;
        },
        delay: (_) async {},
      );
      final oldRun = old.run(token, apiKey);
      await oldStarted.future;
      old.stop();
      late MixelSupportInviteReporter current;
      current = MixelSupportInviteReporter(
        readId: () async => '123456',
        isOnline: () async => true,
        armAttended: () async => true,
        report: (_, __, ___, nonce) async {
          nonces.add(nonce);
          currentReports++;
          if (currentReports == 1) oldResponse.complete(403);
          if (currentReports == 2) current.stop();
          return 200;
        },
        delay: (_) async {
          await Future<void>.delayed(Duration.zero);
        },
      );
      await current.run('inv_00000000-0000-0000-0000-000000000003', apiKey);
      await oldRun;
      expect(
        currentReports == 2,
        'old 403 must not stop successful current invite',
      );
      expect(
        nonces.toSet().length == 3,
        'each native HTTP response must have its own cache URL',
      );
      expect(
        nonces.every((nonce) => RegExp(r'^[0-9a-f]{32}$').hasMatch(nonce)),
        'opaque nonce contains no bearer',
      );
    },
  );

  await test(
    'timed out native request replay uses separate cache nonce',
    () async {
      final lateResponse = Completer<int>();
      final nonces = <String>[];
      var reports = 0;
      late MixelSupportInviteReporter reporter;
      reporter = MixelSupportInviteReporter(
        readId: () async => '123456',
        isOnline: () async => true,
        armAttended: () async => true,
        requestTimeout: const Duration(milliseconds: 10),
        report: (_, __, ___, nonce) {
          nonces.add(nonce);
          reports++;
          if (reports == 1) return lateResponse.future;
          lateResponse.complete(403);
          reporter.stop();
          return Future<int>.value(200);
        },
        delay: (_) async {},
      );
      await reporter.run(token, apiKey);
      expect(
        reports == 2 && nonces.toSet().length == 2,
        'timeout replay must not consume previous HTTP response',
      );
    },
  );
  print('Result: $passed passed; 0 failed');
}
