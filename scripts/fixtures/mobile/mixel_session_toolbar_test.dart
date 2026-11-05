import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mixel_toolbar_qa/mixel_session_toolbar.dart';

void main() {
  for (final width in [240.0, 320.0, 600.0]) {
    testWidgets('all session actions reachable at width $width',
        (tester) async {
      final tapped = <int>[];
      var collapsed = false;
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: Align(
            alignment: Alignment.topLeft,
            child: SizedBox(
              width: width,
              child: BottomAppBar(
                child: MixelSessionToolbar(
                  controls: List.generate(
                      6,
                      (index) => IconButton(
                            key: ValueKey('action-$index'),
                            onPressed: () => tapped.add(index),
                            icon: const Icon(Icons.more_vert),
                          )),
                  trailing: IconButton(
                    key: const ValueKey('collapse'),
                    onPressed: () => collapsed = true,
                    icon: const Icon(Icons.expand_more),
                  ),
                ),
              ),
            ),
          ),
        ),
      ));
      expect(tester.takeException(), isNull);
      await tester.tap(find.byKey(const ValueKey('collapse')));
      expect(collapsed, isTrue);
      for (var index = 0; index < 6; index++) {
        final action = find.byKey(ValueKey('action-$index'));
        await tester.ensureVisible(action);
        await tester.pumpAndSettle();
        expect(tester.getSize(action).width, greaterThanOrEqualTo(48));
        expect(tester.getSize(action).height, greaterThanOrEqualTo(48));
        await tester.tap(action);
        expect(tester.takeException(), isNull);
      }
      expect(tapped, [0, 1, 2, 3, 4, 5]);
    });
  }
}
