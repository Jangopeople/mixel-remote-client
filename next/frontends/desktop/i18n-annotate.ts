// German, French and Italian texts of drawing on the remote screen (annotate.ts), keyed by the
// English text. Added to the app's tables (i18n-strings.ts); i18n.ts looks texts up when it
// translates, so this file may load after it. Texts already in the tables are kept.
(() => {
  type Table = {texts: Record<string, string>; patterns: [string, string][]};
  const all = (window as unknown as {MIXEL_I18N?: Record<string, Table>}).MIXEL_I18N;
  if (!all) return;
  const own: Record<string, Record<string, string>> = {
    de: {
      'Draw: off': 'Zeichnen: aus',
      'Draw: on': 'Zeichnen: ein',
      'Draw on the remote screen. The person at the computer sees it; nothing is clicked.': 'Auf dem entfernten Bildschirm zeichnen. Die Person am Computer sieht es; nichts wird angeklickt.',
      'Drawing tools': 'Zeichenwerkzeuge',
      Pen: 'Stift',
      Highlighter: 'Leuchtstift',
      Arrow: 'Pfeil',
      Red: 'Rot',
      Yellow: 'Gelb',
      Green: 'Grün',
      Blue: 'Blau',
      White: 'Weiss',
      'Line width': 'Strichbreite',
      'Size: 1': 'Grösse: 1',
      'Size: 2': 'Grösse: 2',
      'Size: 3': 'Grösse: 3',
      'Size: 4': 'Grösse: 4',
      Undo: 'Rückgängig',
      Clear: 'Löschen',
    },
    fr: {
      'Draw: off': 'Dessiner : désactivé',
      'Draw: on': 'Dessiner : activé',
      'Draw on the remote screen. The person at the computer sees it; nothing is clicked.': 'Dessiner sur l’écran distant. La personne devant l’ordinateur le voit ; rien n’est cliqué.',
      'Drawing tools': 'Outils de dessin',
      Pen: 'Stylo',
      Highlighter: 'Surligneur',
      Arrow: 'Flèche',
      Red: 'Rouge',
      Yellow: 'Jaune',
      Green: 'Vert',
      Blue: 'Bleu',
      White: 'Blanc',
      'Line width': 'Épaisseur du trait',
      'Size: 1': 'Taille : 1',
      'Size: 2': 'Taille : 2',
      'Size: 3': 'Taille : 3',
      'Size: 4': 'Taille : 4',
      Undo: 'Annuler',
      Clear: 'Effacer',
    },
    it: {
      'Draw: off': 'Disegna: disattivo',
      'Draw: on': 'Disegna: attivo',
      'Draw on the remote screen. The person at the computer sees it; nothing is clicked.': 'Disegna sullo schermo remoto. La persona al computer lo vede; non viene cliccato nulla.',
      'Drawing tools': 'Strumenti di disegno',
      Pen: 'Penna',
      Highlighter: 'Evidenziatore',
      Arrow: 'Freccia',
      Red: 'Rosso',
      Yellow: 'Giallo',
      Green: 'Verde',
      Blue: 'Blu',
      White: 'Bianco',
      'Line width': 'Spessore del tratto',
      'Size: 1': 'Dimensione: 1',
      'Size: 2': 'Dimensione: 2',
      'Size: 3': 'Dimensione: 3',
      'Size: 4': 'Dimensione: 4',
      Undo: 'Annulla',
      Clear: 'Cancella',
    },
  };
  for (const [language, texts] of Object.entries(own)) {
    const table = all[language];
    if (!table) continue;
    for (const [english, translated] of Object.entries(texts)) if (!(english in table.texts)) table.texts[english] = translated;
  }
})();
