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
      report: (_, __, ___) async {
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
        report: (receivedToken, receivedKey, id) async {
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
      report: (_, __, id) async {
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
        report: (_, __, ___) async {
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
        report: (_, __, ___) async {
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
        report: (_, __, ___) async {
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
        report: (_, __, ___) async {
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
  print('Result: $passed passed; 0 failed');
}
