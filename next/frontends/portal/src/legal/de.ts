import type { LegalTexts } from './types';

const P = 'Mixel IT and Corporate Services GmbH, Rüterspüelstrasse 17, 8173 Neerach, Schweiz (UID CHE-184.355.981)';

export const de: LegalTexts = {
  ui: {
    centre: 'Rechtliches',
    centreLead: 'Nutzungsbedingungen, Datenschutz und Auftragsverarbeitung für Mixel Remote.',
    draftTitle: 'Entwurf – nicht in Kraft',
    draftText: 'Diese Texte sind Entwürfe in Prüfung und noch nicht verbindlich. Die endgültige Fassung wird hier mit Datum veröffentlicht.',
    version: 'Version',
    updated: 'Stand',
    contents: 'Inhalt',
    otherLanguages: 'Auch verfügbar auf',
    columns: { name: 'Unterauftragsverarbeiter', purpose: 'Zweck', data: 'Daten', location: 'Ort', safeguard: 'Garantie für die Übermittlung' },
    print: 'Drucken oder als PDF speichern',
  },

  terms: {
    title: 'Nutzungsbedingungen für Mixel Remote',
    summary: 'Der Vertrag zwischen Ihrem Unternehmen und Mixel über die Nutzung von Mixel Remote: Abos, Testphase, Abrechnung, Kündigung, Löschung, zulässige Nutzung, Verfügbarkeit und Haftung.',
    sections: [
      { heading: '1. Anbieterin und Geltungsbereich', body: [
        `Mixel Remote wird angeboten von der ${P} («Mixel», «wir»). Diese Bedingungen gelten für den Dienst Mixel Remote: das Portal unter remote.mixel.ch, die Mixel Remote Apps für Computer und Telefone, den Browser-Viewer, Meetings und den Verbindungsdienst (zusammen der «Dienst»).`,
        'Kundin ist das Unternehmen oder die Person, die ein Mixel Remote Konto eröffnet («Kundin», «Sie»). Wer diese Bedingungen für ein Unternehmen annimmt, bestätigt, dazu berechtigt zu sein. Der Auftragsverarbeitungsvertrag und die Datenschutzerklärung sind Bestandteil dieser Bedingungen. Abweichende Bedingungen der Kundin gelten nur, wenn Mixel ihnen schriftlich zustimmt.',
      ] },
      { heading: '2. Konto, Inhaber und Mitglieder', body: [
        'Wer sich registriert, wird Inhaber des Firmenkontos. Inhaber und Administratoren können Personen als Administratoren, Techniker oder Mitglieder einladen und festlegen, welche Anmeldemethoden erlaubt sind und ob die Anmeldung in zwei Schritten Pflicht ist. Die Kundin ist für alles verantwortlich, was mit ihrem Konto geschieht, und dafür, dass ihre Mitglieder diese Bedingungen einhalten.',
        'Halten Sie Anmeldedaten geheim, nutzen Sie wenn möglich die Anmeldung in zwei Schritten und melden Sie einen Verdacht auf Missbrauch unverzüglich an info@mixel.ch. Jede Anmeldung ist persönlich; ein Login darf nicht von mehreren Personen genutzt werden.',
      ] },
      { heading: '3. Abos und Preise', body: [
        'Es gelten die Abos und Limiten, die bei der Bestellung auf remote.mixel.ch/pricing angegeben sind:',
        { list: [
          'Personal: kostenlos, nur für private, nicht kommerzielle Nutzung; 1 Techniker, 10 Geräte, 1 Sitzung gleichzeitig.',
          'Solo: CHF 12 pro Monat; 1 Techniker, 100 Geräte, 1 Sitzung gleichzeitig.',
          'Team: CHF 39 pro Monat; 5 Techniker, 500 Geräte, 3 Sitzungen gleichzeitig; Gerätegruppen und Zugriffsrechte.',
          'Business: CHF 89 pro Monat; 15 Techniker, 2000 Geräte, 10 Sitzungen gleichzeitig; Firmen-Single-Sign-on, Protokoll, Richtlinien, Pflicht zur Anmeldung in zwei Schritten.',
          'Enterprise: nach individuellem Angebot.',
          'Zusätzlicher Techniker: CHF 8 pro Monat.',
        ] },
        'Die Preise verstehen sich in Schweizer Franken inklusive 8,1 % MWST. Sie gelten bei jährlicher Abrechnung; bei monatlicher Abrechnung sind sie 20 % höher. Mixel kann Preise und Leistungen für künftige Abrechnungsperioden ändern; wir informieren die Inhaber mindestens 30 Tage im Voraus per E-Mail, und die Kundin kann vor Inkrafttreten kündigen.',
      ] },
      { heading: '4. Kostenlose Testphase', body: [
        'Ein neues Unternehmen erhält eine kostenlose Testphase von 14 Tagen mit dem Business-Abo. Zahlungsangaben sind nicht nötig. Die Testphase wird pro Unternehmen einmal gewährt.',
        'Wird bis zum Ende der Testphase kein bezahltes Abo gewählt, wird das Konto schreibgeschützt: Niemand kann sich verbinden, Geräte, Mitglieder und Einstellungen lassen sich nur noch ansehen, entfernen oder exportieren. Die Geräte bleiben 30 Tage ab Beginn des Schreibschutzes erhalten und werden danach automatisch entfernt. Wer innerhalb dieser 30 Tage ein bezahltes Abo wählt, kann den Dienst wieder vollständig nutzen.',
      ] },
      { heading: '5. Bestellung, Abrechnung und Zahlung', body: [
        'Bezahlte Abos werden im Portal bestellt und über unseren Zahlungsanbieter Stripe per Karte bezahlt. Ein während der Testphase gekauftes Abo gilt sofort; die erste Zahlung ist am Tag fällig, an dem die Testphase geendet hätte. Abos verlängern sich automatisch um dieselbe Periode (Monat oder Jahr), bis sie gekündigt werden.',
        'Upgrades und zusätzliche Techniker gelten sofort und werden anteilig sofort belastet. Ein Downgrade muss zur aktuellen Zahl von Technikern und Geräten passen. Rechnungen sind im Bereich Abrechnung des Portals abrufbar.',
        'Schlägt eine Zahlung fehl, bleibt das Konto während einer kurzen Frist nutzbar, in der Stripe die Zahlung erneut versucht. Bleibt der Betrag offen, wird das Konto wie in Ziffer 4 beschrieben schreibgeschützt, bis er bezahlt ist.',
      ] },
      { heading: '6. Laufzeit und Kündigung', body: [
        'Der Vertrag läuft auf unbestimmte Zeit. Inhaber und Administratoren können ein bezahltes Abo jederzeit im Bereich Abrechnung kündigen; die Kündigung wirkt auf das Ende der laufenden Abrechnungsperiode, danach wird das Konto schreibgeschützt. Bereits bezahlte Gebühren werden nicht zurückerstattet, soweit das Gesetz nichts anderes vorschreibt.',
        'Mixel kann den Vertrag mit 30 Tagen Frist auf das Ende einer Abrechnungsperiode kündigen und fristlos aus wichtigem Grund, insbesondere bei einem schweren oder wiederholten Verstoss gegen Ziffer 8, Zahlungsverzug oder einer gesetzlichen oder behördlichen Anordnung.',
      ] },
      { heading: '7. Löschung des Kontos und Ihrer Daten', body: [
        'Der Inhaber kann das gesamte Firmenkonto jederzeit im Portal (Einstellungen › Daten und Löschung) oder in der App löschen. Die Löschung wirkt sofort: Das Abo endet sofort ohne Rückerstattung für die Restlaufzeit, jedes Gerät wird abgemeldet und entfernt, offene Einladungen werden widerrufen, und die Firmendaten, die Mitglieder und ihre Mixel Remote Anmeldekonten werden gelöscht. Jedes Mitglied kann sein eigenes Konto auf dieselbe Weise löschen; der letzte Inhaber löscht zuerst das Unternehmen oder ernennt einen anderen Inhaber.',
        'Vor der Löschung kann der Inhaber die Daten des Unternehmens (Unternehmen, Mitglieder, Geräte, Gruppen und Protokoll) als JSON oder CSV herunterladen. Dieser Export ist auch bei schreibgeschütztem Konto möglich.',
        'Mixel behält nur, was das Gesetz verlangt: Buchungsbelege (Firmenname, Stripe-Referenzen, Rechnungen und Zahlungen) während 10 Jahren (Art. 958f OR). Verschlüsselte Sicherungen werden innerhalb von 12 Monaten überschrieben; gelöschte Daten werden nicht aus Sicherungen wiederhergestellt, ausser zur Behebung eines Katastrophenfalls, und werden dann erneut gelöscht.',
      ] },
      { heading: '8. Zulässige Nutzung und Einwilligung der unterstützten Person', body: [
        'Mixel Remote ermöglicht den Zugriff auf Computer und Telefone anderer Personen. Die Kundin darf sich nur mit Geräten verbinden, die ihr gehören oder die sie verwaltet, oder deren Benutzer eingewilligt hat. Insbesondere:',
        { list: [
          'Begleitete Sitzungen: Die Person am Gerät muss jeder Sitzung zustimmen (Mixel Remote fragt sie) und kann sie jederzeit beenden.',
          'Unbeaufsichtigter Zugriff, Zugriff am Anmeldebildschirm und dauerhafter Zugriff dürfen nur auf Geräten eingerichtet werden, die die Kundin verwalten darf, mit Wissen der üblichen Benutzer und gemäss den internen Regeln der Kundin sowie dem Arbeits- und Datenschutzrecht.',
          'Aufzeichnungen, Dateiübertragungen, Bildschirmfotos und der KI-Assistent dürfen nur genutzt werden, wo dies zulässig ist; wird eine Sitzung aufgezeichnet, sind die Beteiligten vorher zu informieren.',
          'Der Dienst darf nicht genutzt werden für unbefugten Zugriff, Überwachung von Personen ohne Rechtsgrundlage, Betrug (zum Beispiel falsche Support-Anrufe), Verbreitung von Schadsoftware, Verletzung von Rechten Dritter, Spam oder andere rechtswidrige Zwecke.',
          'Greifen Sie den Dienst nicht an, überlasten oder dekompilieren Sie ihn nicht, umgehen Sie keine Sicherheitsmassnahmen oder Limiten und verkaufen Sie ihn nicht ohne schriftliche Vereinbarung mit Mixel weiter.',
        ] },
        'Mixel kann den Zugang ganz oder teilweise sperren, wenn konkrete Hinweise auf Missbrauch bestehen, und informiert die Kundin, sofern Gesetz oder der Schutz Dritter dem nicht entgegenstehen. Die Kundin stellt Mixel von Ansprüchen Dritter frei, die aus ihrer rechtswidrigen Nutzung des Dienstes entstehen.',
      ] },
      { heading: '9. Verfügbarkeit und Support', body: [
        'Mixel betreibt den Dienst mit fachlicher Sorgfalt und strebt eine hohe Verfügbarkeit an, garantiert aber keinen unterbrechungsfreien Betrieb. Wartungen erfolgen wenn möglich ausserhalb der Schweizer Geschäftszeiten. Verbindungen hängen von Netzen und Geräten auf beiden Seiten ab, auf die Mixel keinen Einfluss hat. Bezahlte Abos umfassen Support per E-Mail und Telefon während der Schweizer Geschäftszeiten. Service Levels gelten nur, wenn sie schriftlich vereinbart sind (Enterprise).',
        'Mixel entwickelt den Dienst weiter und kann Funktionen ändern oder ersetzen, sofern die wesentlichen Funktionen des gebuchten Abos erhalten bleiben. Die Apps sind aktuell zu halten; ältere Versionen können aus Sicherheitsgründen nicht mehr funktionieren.',
      ] },
      { heading: '10. Daten der Kundin und Vertraulichkeit', body: [
        'Die Kundin behält alle Rechte an ihren Daten. Mixel nutzt Kundendaten nur zur Erbringung des Dienstes, wie im Auftragsverarbeitungsvertrag festgelegt. Bildschirminhalt, Tastatur- und Mauseingaben, Dateien, Zwischenablage und Chat einer Sitzung werden verschlüsselt zwischen den beiden Geräten übertragen; Mixel speichert sie nicht. Aufzeichnungen werden nur auf dem Gerät des Technikers gespeichert.',
        'Beide Parteien behandeln nicht öffentliche Informationen der anderen Partei vertraulich. Diese Pflicht gilt über das Vertragsende hinaus.',
      ] },
      { heading: '11. Datenschutz', body: [
        'Mixel bearbeitet Personendaten gemäss dem Bundesgesetz über den Datenschutz (DSG) und, soweit anwendbar, der EU-Datenschutz-Grundverordnung (DSGVO). Für die Daten, die die Kundin mit dem Dienst bearbeitet, ist die Kundin Verantwortliche und Mixel Auftragsbearbeiterin; es gilt der Auftragsverarbeitungsvertrag. Für eigene Zwecke (Konto, Abrechnung, Sicherheit) ist Mixel Verantwortliche; siehe Datenschutzerklärung.',
      ] },
      { heading: '12. Rechte am Dienst', body: [
        'Alle Rechte am Dienst, an den Apps und ihren Inhalten verbleiben bei Mixel oder ihren Lizenzgebern. Die Kundin erhält für die Vertragsdauer ein nicht ausschliessliches, nicht übertragbares Recht, den Dienst im Rahmen ihres Abos zu nutzen. Open-Source-Komponenten unterliegen ihren eigenen Lizenzen.',
      ] },
      { heading: '13. Gewährleistung und Haftung', body: [
        'Mixel behebt gemeldete Mängel innert angemessener Frist. Soweit gesetzlich zulässig, haftet Mixel nur für Schäden, die vorsätzlich oder grobfahrlässig verursacht wurden; die Haftung für leichte Fahrlässigkeit, für indirekte und Folgeschäden, entgangenen Gewinn, Datenverlust und Betriebsunterbruch ist ausgeschlossen. Die Haftung ist in jedem Fall auf die Gebühren begrenzt, die die Kundin in den 12 Monaten vor dem Schadenereignis für den Dienst bezahlt hat. Die Haftung für Personenschäden und die zwingende gesetzliche Haftung bleiben vorbehalten. Das kostenlose Personal-Abo und die Testphase werden ohne Gewähr bereitgestellt.',
        'Die Kundin ist für die Sicherung der Daten auf ihren Geräten verantwortlich. Mixel haftet nicht für Handlungen von Technikern auf verbundenen Geräten.',
      ] },
      { heading: '14. Änderungen dieser Bedingungen', body: [
        'Mixel kann diese Bedingungen ändern. Die Inhaber werden mindestens 30 Tage vor Inkrafttreten per E-Mail oder im Portal informiert; die Änderung gilt als angenommen, wenn die Kundin den Dienst nach diesem Datum ohne Kündigung weiter nutzt. Änderungen zugunsten der Kundin oder aufgrund zwingenden Rechts können früher wirksam werden.',
      ] },
      { heading: '15. Anwendbares Recht und Gerichtsstand', body: [
        'Es gilt Schweizer Recht unter Ausschluss des Kollisionsrechts und des Wiener Kaufrechts. Gerichtsstand ist Zürich, Schweiz. Zwingende Verbraucherschutzvorschriften des Wohnsitzstaats eines Verbrauchers bleiben vorbehalten. Ist eine Bestimmung unwirksam, bleiben die übrigen Bestimmungen gültig.',
      ] },
    ],
  },

  privacy: {
    title: 'Datenschutzerklärung für Mixel Remote',
    summary: 'Welche Personendaten Mixel bei der Nutzung von Mixel Remote bearbeitet, wozu, wo sie gespeichert werden, wie lange sie aufbewahrt werden und welche Rechte Sie haben.',
    sections: [
      { heading: '1. Verantwortliche Stelle', body: [
        `Verantwortlich für die hier beschriebene Bearbeitung: ${P}. Kontakt für Datenschutzfragen: info@mixel.ch, +41 44 433 14 00, oder per Post an die obige Adresse.`,
        'Diese Erklärung gilt für das Mixel Remote Portal (remote.mixel.ch), die Mixel Remote Apps, den Browser-Viewer, Meetings und den Verbindungsdienst. Nutzt ein Unternehmen Mixel Remote, um seine eigenen Benutzer oder Geräte zu betreuen, entscheidet dieses Unternehmen über diese Daten und ist Verantwortlicher; Mixel bearbeitet sie in seinem Auftrag gemäss dem Auftragsverarbeitungsvertrag. Fragen zu solchen Daten richten Sie am besten zuerst an dieses Unternehmen.',
      ] },
      { heading: '2. Welche Daten wir bearbeiten', body: [
        { list: [
          'Kontodaten: Name, E-Mail-Adresse, Anmeldemethode, gehashtes Passwort (falls verwendet), Einstellungen der Anmeldung in zwei Schritten, Sprache und der gewählte Anmeldeanbieter (Mixel ID, Google, Microsoft oder Apple, falls angeboten).',
          'Firmendaten: Firmenname, Abo, Stand von Testphase und Abrechnung, Mitglieder und ihre Rollen, Einladungen, Gerätegruppen, Zugriffsrechte, Anmelderegeln, E-Mail-Domains, Verteil-Links (nur ein Hash ihres Geheimnisses) und die Annahme der Nutzungsbedingungen (Version und Zeitpunkt).',
          'Gerätedaten: Gerätename, Betriebssystem, Remote-ID, der öffentliche Schlüssel des Geräts, wer es hinzugefügt hat, wann, seine Gruppe und – für Inventar und Zustand, falls genutzt – technische Eigenschaften des Geräts.',
          'Verbindungs-Metadaten: welches Konto oder Gerät sich wann mit welchem Gerät verbunden hat, Beginn und Ende einer Sitzung, die Art des Zugriffs (begleitet oder unbeaufsichtigt), erteilte Zustimmung, für den Verbindungsaufbau nötige IP-Adressen und Netzdaten sowie technische Protokolle des Verbindungsdienstes.',
          'Anwesenheit: ob ein Gerät online ist, seit wann und wann es zuletzt gesehen wurde. Der Verbindungsdienst meldet dies alle 30 Sekunden.',
          'Protokoll: Aktionen im Unternehmen (Geräte hinzugefügt, umbenannt, verschoben oder entfernt, Mitglieder eingeladen oder entfernt, Einstellungen geändert, Daten exportiert) mit Zeit und handelnder Person.',
          'Sitzungsinhalt: Bildschirm, Ton, Tastatur- und Mauseingaben, Dateien, Zwischenablage und Chat werden Ende-zu-Ende-verschlüsselt zwischen den beiden Geräten übertragen (WebRTC mit an die Geräte gebundenen Schlüsseln). Relay-Server leiten nur verschlüsselte Daten weiter; Mixel kann sie nicht lesen und speichert sie nicht.',
          'Aufzeichnungen: Ein Techniker kann eine Sitzung aufzeichnen; die Aufzeichnung wird nur auf seinem Gerät gespeichert und nie zu Mixel hochgeladen.',
          'Abrechnungsdaten: Firmenname, Rechnungsadresse, MWST-Nummer, E-Mail für Rechnungen, gewähltes Abo, Rechnungen und Zahlungen. Kartendaten werden direkt bei Stripe eingegeben; Mixel sieht nie vollständige Kartennummern.',
          'Website-Daten: Beim Besuch von remote.mixel.ch werden technisch notwendige Daten (IP-Adresse, Zeit, Seite, Browser) von Cloudflare bearbeitet, um die Website auszuliefern und zu schützen. Wir verwenden keine Tracking- oder Werbe-Cookies; der Browser speichert nur Ihre Anmeldesitzung, Sprache und Farbdarstellung.',
        ] },
      ] },
      { heading: '3. Zwecke und Rechtsgrundlagen', body: [
        { list: [
          'Erbringung des Dienstes, Verbinden von Geräten, Durchsetzen von Zugriffsrechten und Abo-Limiten – Vertragserfüllung (Art. 6 Abs. 1 lit. b DSGVO).',
          'Sicherheit: Schutz vor Missbrauch und Angriffen, Protokoll, technische Logs – berechtigtes Interesse von uns und unseren Kundinnen an einem sicheren Dienst (Art. 6 Abs. 1 lit. f DSGVO).',
          'Abrechnung und Buchhaltung, Aufbewahrung – rechtliche Pflicht (Art. 6 Abs. 1 lit. c DSGVO, Art. 958f OR) und Vertrag.',
          'Dienst-E-Mails (Anmeldecodes, Einladungen, Hinweise zum Konto) – Vertrag und berechtigtes Interesse. Newsletter senden wir nicht ohne Ihre Einwilligung.',
          'Support-Anfragen – Vertrag und berechtigtes Interesse.',
        ] },
        'Nach dem Schweizer DSG bearbeiten wir Personendaten gemäss seinen Grundsätzen (Rechtmässigkeit, Treu und Glauben, Verhältnismässigkeit, Zweckbindung, Transparenz und Datensicherheit).',
      ] },
      { heading: '4. Wo Ihre Daten gespeichert sind', body: [
        'Die Datenbank, der Anmeldedienst, der Verbindungsdienst und die Relay-Server von Mixel Remote laufen auf Servern, die Mixel bei Hetzner in Deutschland (EU) betreibt. Verschlüsselte Sicherungen liegen bei Hetzner in Deutschland. Das Portal und die Auslieferung der Website erfolgen über das weltweite Netz von Cloudflare. Zahlungen wickelt Stripe ab. Dienst-E-Mails werden über Resend versendet. Die vollständige Liste mit Orten und Garantien finden Sie auf der Seite Unterauftragsverarbeiter.',
        'Die Schweiz anerkennt Deutschland und die EU als Staaten mit angemessenem Datenschutz. Werden Daten in den USA bearbeitet (Cloudflare, Stripe, Resend), stützt sich die Übermittlung auf das Swiss-U.S. und EU-U.S. Data Privacy Framework, sofern der Empfänger zertifiziert ist, sowie auf die EU-Standardvertragsklauseln mit Schweizer Ergänzung.',
      ] },
      { heading: '5. Wer Daten erhält', body: [
        'Innerhalb eines Unternehmens: Inhaber und Administratoren sehen Mitglieder, Geräte und Protokoll des Unternehmens; Techniker sehen die Geräte, die sie verwalten dürfen; Mitglieder sehen die Geräte, die sie hinzugefügt haben. Die Person am Gerät sieht, wer sich verbindet.',
        'Ausserhalb: nur die auf der Seite Unterauftragsverarbeiter genannten Dienstleister, soweit für ihre Aufgabe nötig und vertraglich gebunden; Stripe bearbeitet einige Daten (Betrugsprävention, regulatorische Pflichten) als eigene Verantwortliche; Google, Microsoft oder Apple, wenn Sie sich damit anmelden, nach deren eigenen Datenschutzbestimmungen; Behörden, soweit wir gesetzlich dazu verpflichtet sind. Wir verkaufen keine Personendaten.',
      ] },
      { heading: '6. Wie lange wir Daten aufbewahren', body: [
        { list: [
          'Konto- und Firmendaten: solange das Konto besteht. Löscht der Inhaber das Unternehmen oder löschen Sie Ihr Konto, werden die Daten sofort gelöscht.',
          'Geräte: bis sie entfernt werden; Geräte eines schreibgeschützten Kontos werden 30 Tage nach Beginn des Schreibschutzes automatisch entfernt. Schlüssel und Remote-ID des Geräts bleiben gesperrt, damit sie nie einem anderen Computer zugeteilt werden.',
          'Anwesenheit: Der Zeitpunkt «zuletzt gesehen» bleibt bis zur Entfernung des Geräts gespeichert.',
          'Protokoll: solange das Firmenkonto besteht.',
          'Technische Logs des Verbindungsdienstes: in grössenbegrenzten Dateien, die automatisch überschrieben werden.',
          'Buchungsbelege: 10 Jahre (Art. 958f OR); nach der Löschung eines Unternehmens bleiben nur Firmenname, Stripe-Referenzen und Anzahlen erhalten, ohne Personendaten der Mitglieder.',
          'Sicherungen: verschlüsselt; tägliche Sicherungen 7 Tage, wöchentliche 4 Wochen, monatliche 12 Monate, danach überschrieben.',
        ] },
      ] },
      { heading: '7. Ihre Rechte', body: [
        'Nach DSG und DSGVO können Sie Auskunft über Ihre Daten, Berichtigung unrichtiger Daten, Löschung, Einschränkung der Bearbeitung und Datenherausgabe bzw. -übertragung verlangen und einer auf berechtigtem Interesse beruhenden Bearbeitung widersprechen. Eine Einwilligung können Sie jederzeit mit Wirkung für die Zukunft widerrufen.',
        'Vieles erledigen Sie selbst: Ihr Profil sehen und korrigieren Sie im Portal, Ihr eigenes Konto löschen Sie unter Konto, und der Inhaber exportiert und löscht die Firmendaten unter Einstellungen › Daten und Löschung. Für alles andere schreiben Sie an info@mixel.ch; wir antworten innert 30 Tagen. Wir können Sie bitten, Ihre Identität zu bestätigen.',
        'Sie können sich beim Eidgenössischen Datenschutz- und Öffentlichkeitsbeauftragten (EDÖB, www.edoeb.admin.ch) oder in der EU bei der Aufsichtsbehörde Ihres Landes beschweren.',
      ] },
      { heading: '8. Sicherheit', body: [
        'Alle Verbindungen sind verschlüsselt (TLS; Sitzungsinhalt Ende-zu-Ende mit WebRTC DTLS/SRTP). Geräte weisen sich mit ihrem eigenen Schlüssel aus; jede Aktion wird in der Datenbank gegen die Rollen und Regeln des Unternehmens geprüft; Anmeldung in zwei Schritten und Anmelderegeln für Unternehmen sind verfügbar; Sicherungen sind verschlüsselt; der Zugriff von Mixel-Mitarbeitenden ist auf das Nötige beschränkt und wird protokolliert.',
      ] },
      { heading: '9. Kinder', body: [
        'Mixel Remote richtet sich an Unternehmen und Erwachsene. Personen unter 16 Jahren dürfen das Personal-Abo nur mit Zustimmung eines Elternteils nutzen.',
      ] },
      { heading: '10. Änderungen', body: [
        'Wir passen diese Erklärung an, wenn sich der Dienst oder das Recht ändert. Die aktuelle Fassung ist immer hier abrufbar; über wichtige Änderungen informieren wir die Inhaber per E-Mail oder im Portal.',
      ] },
    ],
  },

  dpa: {
    title: 'Auftragsverarbeitungsvertrag (AVV) für Mixel Remote',
    summary: 'Die Vereinbarung nach Art. 9 DSG und Art. 28 DSGVO über die Personendaten, die Mixel bei der Erbringung von Mixel Remote im Auftrag der Kundin bearbeitet.',
    sections: [
      { heading: '1. Parteien und Geltungsbereich', body: [
        `Dieser Auftragsverarbeitungsvertrag («AVV») wird zwischen der Kundin (Verantwortliche) und der ${P} (Auftragsbearbeiterin, «Mixel») geschlossen. Er ist Bestandteil der Nutzungsbedingungen für Mixel Remote und wird mit ihnen bei der Eröffnung des Firmenkontos angenommen. Er gilt für alle Personendaten, die Mixel bei der Erbringung des Dienstes im Auftrag der Kundin bearbeitet («Kundendaten»). Weichen AVV und Nutzungsbedingungen beim Datenschutz voneinander ab, geht dieser AVV vor.`,
      ] },
      { heading: '2. Gegenstand, Art und Zweck', body: [
        'Gegenstand: Erbringung von Mixel Remote – Firmenkonten, Geräteverwaltung, Verbindungen zwischen Geräten, Anwesenheit, Protokoll, Einladungen und Support. Art: Speichern, Ordnen, Übermitteln und Löschen von Daten; Weiterleiten verschlüsselter Sitzungsdaten. Zweck: Die Kundin soll ihre Geräte und Benutzer aus der Ferne betreuen und verwalten können. Dauer: Vertragsdauer zuzüglich der Löschfristen nach Ziffer 10.',
      ] },
      { heading: '3. Betroffene Personen und Datenkategorien', body: [
        { list: [
          'Betroffene Personen: Inhaber, Administratoren, Techniker und Mitglieder der Kundin; Benutzer der Geräte, die die Kundin verwaltet oder betreut; von der Kundin eingeladene Personen.',
          'Kategorien: Namen, E-Mail-Adressen, Rollen, Anmeldedaten; Gerätenamen, Betriebssysteme, Remote-IDs, Geräteschlüssel, technische Geräteeigenschaften; Verbindungs-Metadaten (wer sich wann mit welchem Gerät verbunden hat, IP-Adressen, Zustimmung); Anwesenheit (online, zuletzt gesehen); Protokolleinträge; Einladungen.',
          'Sitzungsinhalte (Bildschirm, Ton, Eingaben, Dateien, Zwischenablage, Chat) werden Ende-zu-Ende-verschlüsselt zwischen den Geräten übertragen. Mixel hat keinen Zugriff darauf und speichert sie nicht. Aufzeichnungen existieren nur auf dem Gerät des Technikers.',
          'Besonders schützenswerte Personendaten werden nicht gezielt bearbeitet. Erscheinen solche Daten während einer Sitzung auf einem Bildschirm, speichert Mixel sie nicht.',
        ] },
      ] },
      { heading: '4. Weisungen', body: [
        'Mixel bearbeitet Kundendaten nur auf dokumentierte Weisung der Kundin. Die Nutzungsbedingungen, dieser AVV sowie die Einstellungen und Handlungen der Kundin im Dienst sind die abschliessenden Weisungen. Mixel informiert die Kundin unverzüglich, wenn sie eine Weisung für rechtswidrig hält. Eine andere Bearbeitung erfolgt nur, wenn Schweizer oder EU-Recht sie verlangt; Mixel teilt dies der Kundin vorher mit, sofern das Recht es nicht verbietet.',
      ] },
      { heading: '5. Vertraulichkeit', body: [
        'Mixel stellt sicher, dass die zur Bearbeitung von Kundendaten befugten Personen zur Vertraulichkeit verpflichtet sind und die Daten nur bearbeiten, soweit es für den Dienst nötig ist.',
      ] },
      { heading: '6. Sicherheit der Bearbeitung', body: [
        'Mixel trifft angemessene technische und organisatorische Massnahmen gemäss Anhang 1 und passt sie dem Stand der Technik an. Mixel kann Massnahmen durch andere mit mindestens gleichem Schutzniveau ersetzen.',
      ] },
      { heading: '7. Unterauftragsverarbeiter', body: [
        'Die Kundin ermächtigt Mixel, die unter remote.mixel.ch/legal/subprocessors aufgeführten Unterauftragsverarbeiter (Anhang 2) beizuziehen. Mixel informiert die Kundin mindestens 30 Tage im Voraus über einen neuen oder ersetzten Unterauftragsverarbeiter (per E-Mail an die Inhaber oder im Portal). Die Kundin kann innert dieser Frist aus wichtigen datenschutzrechtlichen Gründen widersprechen; findet sich keine Lösung, kann sie den betroffenen Dienst auf den Zeitpunkt der Änderung ohne Kostenfolge kündigen.',
        'Mixel überbindet jedem Unterauftragsverarbeiter vertraglich Datenschutzpflichten, die diesem AVV gleichwertig sind, und bleibt der Kundin gegenüber dafür verantwortlich.',
      ] },
      { heading: '8. Übermittlung ins Ausland', body: [
        'Kundendaten werden in Deutschland (EU) gespeichert. Eine Übermittlung in ein Land ohne angemessenen Datenschutz erfolgt nur mit den Garantien nach Art. 16 DSG und Art. 44 ff. DSGVO, insbesondere den EU-Standardvertragsklauseln mit Schweizer Ergänzung oder einer Zertifizierung unter dem Swiss-U.S. / EU-U.S. Data Privacy Framework. Anhang 2 nennt die Garantie je Unterauftragsverarbeiter.',
      ] },
      { heading: '9. Unterstützung der Kundin', body: [
        { list: [
          'Betroffenenrechte: Mit dem Dienst kann die Kundin Daten selbst einsehen, berichtigen, exportieren (Einstellungen › Daten und Löschung) und löschen. Mixel leitet Anfragen, die bei ihr eingehen, an die Kundin weiter und hilft, wo die Kundin sie nicht selbst beantworten kann.',
          'Verletzungen der Datensicherheit: Mixel informiert die Kundin ohne unangemessene Verzögerung, möglichst innert 48 Stunden, nachdem ihr eine Verletzung der Sicherheit von Kundendaten bekannt wurde, mit den verfügbaren Angaben, und unterstützt die Kundin bei ihren Meldepflichten gegenüber EDÖB, Aufsichtsbehörden und Betroffenen.',
          'Mixel hilft bei Datenschutz-Folgenabschätzungen und Konsultationen von Behörden, soweit sie den Dienst betreffen.',
        ] },
        'Unterstützung über die Selbstbedienungsfunktionen hinaus kann zu den üblichen Ansätzen von Mixel verrechnet werden, wenn sie umfangreich ist oder von der Kundin verursacht wurde.',
      ] },
      { heading: '10. Löschung und Rückgabe', body: [
        'Die Kundin kann ihre Daten jederzeit exportieren und das Firmenkonto selbst löschen. Bei der Löschung werden Kundendaten sofort aus den Produktivsystemen gelöscht; Geräte werden abgemeldet und entfernt. Verschlüsselte Sicherungen werden innerhalb von 12 Monaten überschrieben. Aufgrund gesetzlicher Pflichten aufbewahrt werden nur Buchungsbelege (10 Jahre), die keine Kundendaten von Mitgliedern oder Gerätebenutzern enthalten. Endet der Vertrag ohne Löschung durch die Kundin, werden die Daten eines schreibgeschützten Kontos gemäss den Nutzungsbedingungen gelöscht.',
      ] },
      { heading: '11. Nachweise und Kontrollen', body: [
        'Mixel stellt die Informationen zur Verfügung, die zum Nachweis der Einhaltung dieses AVV nötig sind. Die Kundin kann die Einhaltung einmal pro Kalenderjahr mit 30 Tagen Voranmeldung während der Geschäftszeiten auf eigene Kosten prüfen, ohne Zugang zu Daten anderer Kundinnen oder zu Geschäftsgeheimnissen; schriftliche Auskünfte und vorhandene Berichte gehen Kontrollen vor Ort vor. Kontrollen durch eine zuständige Behörde bleiben jederzeit möglich.',
      ] },
      { heading: '12. Haftung und Dauer', body: [
        'Die Haftung richtet sich nach den Nutzungsbedingungen. Dieser AVV gilt, solange Mixel Kundendaten bearbeitet. Es gilt Schweizer Recht; Gerichtsstand ist Zürich.',
      ] },
      { heading: 'Anhang 1 – Technische und organisatorische Massnahmen', body: [
        { list: [
          'Verschlüsselung: TLS 1.2 oder höher für jede Verbindung; Sitzungsinhalt Ende-zu-Ende-verschlüsselt (WebRTC DTLS/SRTP, Schlüssel an Geräteidentitäten gebunden); Sicherungen werden verschlüsselt, bevor sie den Server verlassen.',
          'Zugriffskontrolle: jede Aktion in der Datenbank geprüft (Rolle, Unternehmen, Anmeldemethode, Zwei-Schritte-Regel); kein direkter Datenbankzugriff für Apps; getrennte Dienstkonten mit minimalen Rechten für Abrechnung, Anwesenheit und E-Mail.',
          'Geräteidentität: Jedes Gerät hat einen eigenen Schlüssel; entfernte Geräte werden sofort gesperrt, laufende Sitzungen enden bei der nächsten Prüfung; Remote-IDs werden nie wiederverwendet.',
          'Authentifizierung: gehashte Passwörter, E-Mail-Codes, optionale und erzwingbare Anmeldung in zwei Schritten, Anmelderegeln für Unternehmen.',
          'Trennung: Die Daten jedes Unternehmens sind in der Datenbank nach Mandant getrennt und werden bei jedem Aufruf geprüft.',
          'Protokollierung: Protokoll pro Unternehmen; Registrierungsprotokoll für Geräte; technische Logs begrenzter Grösse.',
          'Verfügbarkeit: tägliche verschlüsselte Sicherungen (7 täglich, 4 wöchentlich, 12 monatlich) auf getrenntem Speicher; Überwachung der Dienste.',
          'Organisation: Mitarbeitende zur Vertraulichkeit verpflichtet, Zugriff nach dem Prinzip der minimalen Rechte, regelmässige Sicherheitsupdates, Prozess für Sicherheitsvorfälle.',
          'Datensparsamkeit: keine Speicherung von Sitzungsinhalten; Aufzeichnungen nur auf dem Gerät des Technikers; Geheimnisse von Einladungen und Verteil-Links nur als Hash gespeichert.',
        ] },
      ] },
      { heading: 'Anhang 2 – Unterauftragsverarbeiter', body: [
        'Die aktuelle Liste mit Zweck, Ort und Garantie:',
        { table: 'subprocessors' },
      ] },
    ],
  },

  subprocessors: {
    title: 'Unterauftragsverarbeiter von Mixel Remote',
    summary: 'Die Unternehmen, die für Mixel Remote im Auftrag von Mixel Personendaten bearbeiten, wozu und wo.',
    sections: [
      { heading: 'Aktuelle Liste', body: [
        { table: 'subprocessors' },
        'Mixel betreibt die Datenbank, die Anmeldung, die Verbindungs-, Relay-, Meeting- und Abrechnungsdienste selbst auf diesen Servern. Mixel informiert die Kundinnen mindestens 30 Tage bevor ein Unterauftragsverarbeiter hinzukommt oder ersetzt wird (siehe Ziffer 7 des Auftragsverarbeitungsvertrags).',
        'Keine Unterauftragsverarbeiter sind: Google, Microsoft und Apple, wenn sich jemand damit anmeldet (sie handeln nach eigenen Bedingungen); der eigene KI-Server des Unternehmens, falls es den KI-Assistenten nutzt; die eigenen Mail- und Geräteverwaltungssysteme des Unternehmens.',
      ] },
    ],
  },

  subprocessorRows: [
    { purpose: 'Server für Datenbank, Anmeldung, Verbindungs-, Relay-, Meeting- und Abrechnungsdienst; verschlüsselte Sicherungen', data: 'Alle Daten des Dienstes ausser Zahlungsdaten', location: 'Deutschland (EU)', safeguard: 'Angemessener Schutz (EU)' },
    { purpose: 'Auslieferung und Schutz von remote.mixel.ch und den Schnittstellen (DNS, TLS, DDoS-Schutz, Content Delivery, Hosting des Portals)', data: 'IP-Adressen, Anfragedaten; verschlüsselter Verkehr wird durchgeleitet', location: 'Weltweites Netz, USA', safeguard: 'Data Privacy Framework (EU-U.S. und Swiss-U.S.); EU-Standardvertragsklauseln mit Schweizer Ergänzung' },
    { purpose: 'Zahlungen, Abos und Rechnungen', data: 'Firmenname, Rechnungsadresse, MWST-Nummer, E-Mail für Rechnungen, Zahlungsdaten, Rechnungen', location: 'Irland (EU), USA', safeguard: 'Angemessener Schutz (EU); Data Privacy Framework; EU-Standardvertragsklauseln mit Schweizer Ergänzung' },
    { purpose: 'Versand von Dienst-E-Mails: Anmeldecodes, Einladungen, Hinweise zum Konto', data: 'E-Mail-Adresse, Name, Firmenname, Inhalt der E-Mail', location: 'USA', safeguard: 'EU-Standardvertragsklauseln mit Schweizer Ergänzung' },
  ],
};
