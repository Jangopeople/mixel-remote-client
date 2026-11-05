import 'package:flutter/material.dart';

/// Keep the collapse control fixed and all session actions reachable on phones.
class MixelSessionToolbar extends StatelessWidget {
  const MixelSessionToolbar({
    super.key,
    required this.controls,
    required this.trailing,
  });

  final List<Widget> controls;
  final Widget trailing;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: SingleChildScrollView(
            key: const ValueKey('mixel-session-toolbar-scroll'),
            scrollDirection: Axis.horizontal,
            child: Row(mainAxisSize: MainAxisSize.min, children: controls),
          ),
        ),
        trailing,
      ],
    );
  }
}
