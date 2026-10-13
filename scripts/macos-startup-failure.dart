// This screen must work before the Rust bridge or global FFI exists.
import 'dart:io';

import 'package:desktop_multi_window/desktop_multi_window.dart';
import 'package:flutter/material.dart';
import 'package:window_manager/window_manager.dart';

List<String> mixelStartupFailureText(String locale, {bool secondary = false}) {
  final language = locale.toLowerCase().split(RegExp('[-_]')).first;
  switch (language) {
    case 'de':
      return ['Mixel Remote konnte nicht gestartet werden',
        secondary ? 'Dieses Fenster wurde nicht initialisiert. Schliessen Sie es und versuchen Sie es erneut. Andere Fenster bleiben geöffnet.' : 'Die sichere Verbindung wurde nicht initialisiert. Fernsupport ist nicht verfügbar. Schliessen Sie Mixel Remote und öffnen Sie die App erneut. Ihre Einstellungen bleiben erhalten.',
        secondary ? 'Fenster schliessen' : 'App schliessen'];
    case 'fr':
      return ['Mixel Remote n’a pas pu démarrer',
        secondary ? 'Cette fenêtre n’a pas été initialisée. Fermez-la et réessayez. Les autres fenêtres restent ouvertes.' : 'La connexion sécurisée n’a pas été initialisée. L’assistance à distance est indisponible. Fermez Mixel Remote et rouvrez l’application. Vos paramètres sont conservés.',
        secondary ? 'Fermer la fenêtre' : 'Fermer l’application'];
    case 'it':
      return ['Impossibile avviare Mixel Remote',
        secondary ? 'Questa finestra non è stata inizializzata. Chiudila e riprova. Le altre finestre rimangono aperte.' : 'La connessione sicura non è stata inizializzata. L’assistenza remota non è disponibile. Chiudi Mixel Remote e riapri l’app. Le impostazioni vengono conservate.',
        secondary ? 'Chiudi la finestra' : 'Chiudi l’app'];
    default:
      return ['Mixel Remote could not start',
        secondary ? 'This window did not initialize. Close it and try again. Other windows remain open.' : 'The secure connection did not initialize. Remote support is unavailable. Close Mixel Remote and reopen the app. Your settings are preserved.',
        secondary ? 'Close window' : 'Close app'];
  }
}

Future<void> showMixelStartupFailure({int? windowId}) async {
  runApp(MixelStartupFailureApp(windowId: windowId));
  // Window plugins do not require the Rust bridge. Never hide this screen or
  // retry initialization, which could alter identity or saved consent settings.
  try {
    if (windowId != null) {
      final window = WindowController.fromWindowId(windowId);
      await _mixelStartupWindowCall(() => window.setPreventClose(false));
      await _mixelStartupWindowCall(() => window.show());
      await _mixelStartupWindowCall(() => window.focus());
    } else {
      await _mixelStartupWindowCall(() => windowManager.ensureInitialized());
      await _mixelStartupWindowCall(() => windowManager.setPreventClose(false));
      // An optional title/size failure must not prevent foregrounding the UI.
      await _mixelStartupWindowCall(() => windowManager.setOpacity(1));
      await _mixelStartupWindowCall(() => windowManager.show());
      await _mixelStartupWindowCall(() => windowManager.focus());
      await _mixelStartupWindowCall(() => windowManager.setTitle('Mixel Remote'));
      await _mixelStartupWindowCall(() => windowManager.setSize(const Size(720, 380)));
    }
  } catch (_) {
    debugPrint('Mixel startup failure window could not be shown.');
  }
}

Future<void> _mixelStartupWindowCall(Future<void> Function() operation) async {
  try {
    await operation().timeout(const Duration(seconds: 2));
  } catch (_) {
    debugPrint('Mixel startup failure window action unavailable.');
  }
}

class MixelStartupFailureApp extends StatelessWidget {
  const MixelStartupFailureApp({super.key, this.windowId});
  final int? windowId;

  Future<void> _closeFailureWindow() async {
    if (windowId == null) {
      exit(1);
    }
    // A Flutter subwindow shares the host process with healthy windows.
    // Never exit that host for a failed secondary engine.
    final window = WindowController.fromWindowId(windowId!);
    await _mixelStartupWindowCall(() => window.setPreventClose(false));
    await _mixelStartupWindowCall(() => window.close());
  }

  @override
  Widget build(BuildContext context) {
    final text = mixelStartupFailureText(Platform.localeName, secondary: windowId != null);
    return MaterialApp(
      title: 'Mixel Remote',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xff496719))),
      home: Scaffold(
        body: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(32),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 620),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(Icons.error_outline, size: 40),
                    const SizedBox(height: 20),
                    Text(text[0], style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
                    const SizedBox(height: 16),
                    Text(text[1], style: const TextStyle(fontSize: 16, height: 1.4)),
                    const SizedBox(height: 24),
                    FilledButton(onPressed: _closeFailureWindow, child: Text(text[2])),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
